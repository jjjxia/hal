// hal-recover: netlist -> certified event graph (import, word groups, original-gate trace, certificate, SSE, folding).
#include "algorithm_recovery/memory.h"
#include "algorithm_recovery/frontend.h"
#include "algorithm_recovery/grouping.h"
#include "algorithm_recovery/observations.h"
#include "algorithm_recovery/guided.h"
#include "algorithm_recovery/certify.h"
#include "hal_core/netlist/netlist.h"
#include "hal_core/netlist/persistent/netlist_serializer.h"
#include "hal_core/plugin_system/plugin_manager.h"
#include <nlohmann/json.hpp>
#include <fstream>
#include <iostream>
#include "algorithm_recovery/memory_replay.h"
#include "hal_core/utilities/enums.h"
#include <limits>
#include <sstream>
#include <cctype>
#include <optional>
#include <algorithm>

namespace
{
    using Options = std::map<std::string, std::string>;
    Options parse_options(int argc, char** argv, int first, const std::set<std::string>& allowed)
    {
        Options result;
        for (int i = first; i < argc; i += 2)
        {
            const std::string name = argv[i];
            if (!allowed.count(name) || i + 1 >= argc || !result.emplace(name, argv[i + 1]).second)
                throw std::runtime_error("unknown, missing-value or duplicate option: " + name);
        }
        return result;
    }
    u32 number(const Options& options, const std::string& name, u32 fallback)
    {
        if (!options.count(name)) return fallback;
        const auto& text = options.at(name);
        if (text.empty() || text.find_first_not_of("0123456789") != std::string::npos) throw std::runtime_error("invalid unsigned integer: " + name);
        const auto value = std::stoull(text);
        if (value > std::numeric_limits<u32>::max()) throw std::runtime_error("integer too large: " + name);
        return value;
    }
    std::vector<std::string> list(const std::string& text)
    {
        std::vector<std::string> result; std::set<std::string> seen; std::string value; std::istringstream input(text);
        if (text.empty() || text.back() == ',') throw std::runtime_error("empty list entry");
        while (std::getline(input, value, ','))
        { if (value.empty() || !seen.insert(value).second) throw std::runtime_error("empty or duplicate list entry"); result.push_back(value); }
        return result;
    }
    nlohmann::json fold_diagnostic(const hal::algorithm_recovery::FoldGraph& graph, const hal::algorithm_recovery::FoldInput& input)
    {
        nlohmann::json histogram = nlohmann::json::object();
        for (const auto& [name, period] : graph.periods)
        { const auto key = std::to_string(period); histogram[key] = histogram.value(key, 0u) + 1; }
        nlohmann::json result{{"available", graph.available}, {"period", graph.period}, {"origin", graph.origin},
            {"window_start", input.start}, {"window_length", input.length}, {"required_complete_periods", 4},
            {"local_periods", graph.periods}, {"period_histogram", histogram},
            {"nodes", graph.nodes.size()}, {"ram_nodes", graph.ram_nodes.size()}, {"histories", graph.histories.size()}, {"excluded", graph.excluded}};
        if (!graph.available)
        {
            result["reason"] = graph.period ? "INSUFFICIENT_PERIODIC_WINDOW" : "NO_PERIODIC_WORDS";
            if (graph.period <= std::numeric_limits<u64>::max() / 4) result["required_window_length"] = graph.period * 4;
        }
        return result;
    }
    // Event graph plus certified per-phase constants, for program recovery (C3).
    // Equations are serialised as SMT-LIB assertions (= __out expr) with declarations.
    void dump_graph(const std::filesystem::path& file, const hal::algorithm_recovery::FoldGraph& graph,
                    const hal::algorithm_recovery::FoldInput& input, hal::algorithm_recovery::OriginalCircuit& original,
                    const hal::algorithm_recovery::ScheduleCertificate* certificate, const hal::algorithm_recovery::PadProtocol* protocol, z3::context& ctx)
    {
        using hal::algorithm_recovery::SourceKind;
        const auto smt = [&](const z3::expr& e) {
            z3::solver s(ctx); s.add((e.is_bool() ? ctx.bool_const("__out") : ctx.bv_const("__out", e.get_sort().bv_size())) == e); return s.to_smt2();
        };
        const auto source_json = [&](const std::map<std::string, hal::algorithm_recovery::Source>& sources) {
            nlohmann::json rows = nlohmann::json::array();
            for (const auto& [symbol, src] : sources)
                rows.push_back({{"symbol", symbol}, {"kind", src.kind == SourceKind::signal ? "signal" : src.kind == SourceKind::input ? "input" : "ram"},
                                {"name", src.name}, {"phase", src.phase}, {"delay", src.delay}});
            return rows;
        };
        nlohmann::json out{{"period", graph.period}, {"origin", graph.origin}, {"window_start", input.start}, {"window_length", input.length},
                           {"available", graph.available}, {"excluded", graph.excluded}, {"words", nlohmann::json::object()},
                           {"inputs", nlohmann::json::object()}, {"memories", nlohmann::json::object()}, {"nodes", nlohmann::json::array()},
                           {"ram_nodes", nlohmann::json::array()}, {"outputs", nlohmann::json::object()}};
        std::vector<u64> events;
        for (u64 phase = 0; phase < graph.period; ++phase)
        {
            u64 e = graph.origin + phase;
            if (certificate && certificate->certified) while (e < certificate->origin) e += graph.period;
            events.push_back(e);
        }
        out["phase_events"] = events;
        if (certificate)
            out["certificate"] = {{"certified", certificate->certified}, {"origin", certificate->origin}, {"period", certificate->period},
                                  {"phases_aligned", certificate->certified && certificate->period % std::max<u64>(graph.period, 1) == 0 && certificate->period == graph.period}};
        const auto known = [&](const std::string& bit, u64 e) -> int {
            if (!certificate) return -1;
            return certificate->taint.known(std::stoll(bit.substr(1)), e);
        };
        for (const auto& [name, word] : input.words)
        {
            nlohmann::json phases = nlohmann::json::array();
            for (const auto e : events) { std::string row; for (const auto& bit : word.bits) { const auto v = known(bit, e); row += v < 0 ? 'x' : char('0' + v); } phases.push_back(row); }
            out["words"][name] = {{"bits", word.bits}, {"numeric_order_known", word.numeric_order_known}, {"periodic", graph.periods.count(name) != 0},
                                  {"certified_known_lsb_first", phases}};
        }
        for (const auto& [name, bits] : input.inputs)
        {
            bool data = true;
            if (protocol) for (const auto& bit : bits) data &= !protocol->non_data.count(std::stoll(bit.substr(1)));
            nlohmann::json phases = nlohmann::json::array();
            for (const auto e : events) { std::string row; for (const auto& bit : bits) { const auto v = known(bit, e); row += v < 0 ? 'x' : char('0' + v); } phases.push_back(row); }
            out["inputs"][name] = {{"bits", bits}, {"data", data}, {"certified_known_lsb_first", phases}};
        }
        for (const auto& [name, memory] : input.memories) out["memories"][name] = {{"read_bits", memory.read_bits}};
        for (const auto& node : graph.nodes)
            out["nodes"].push_back({{"name", node.name}, {"phase", node.phase}, {"width", node.equation.get_sort().bv_size()},
                                    {"sources", source_json(node.sources)}, {"smt2", smt(node.equation)}});
        for (const auto& node : graph.ram_nodes)
            out["ram_nodes"].push_back({{"memory", node.memory}, {"port", node.port}, {"phase", node.phase},
                                        {"sources", source_json(node.sources)}, {"smt2", smt(node.equation)}});
        // Tristate output pads ($mux A=z, the only $mux the frontend accepts) are
        // exported as (data, enable), like the full-state backend; no single smt2.
        std::map<hal::algorithm_recovery::PhysicalBit, std::pair<hal::algorithm_recovery::PhysicalBit, hal::algorithm_recovery::PhysicalBit>> pad_drivers;
        for (const auto& cell : original.module().at("cells")) if (cell.at("type") == "$mux")
        {
            const auto& pins = cell.at("connections");
            pad_drivers.emplace(hal::algorithm_recovery::physical_bit(pins.at("Y")[0]),
                std::make_pair(hal::algorithm_recovery::physical_bit(pins.at("B")[0]), hal::algorithm_recovery::physical_bit(pins.at("S")[0])));
        }
        const auto driven = [&](hal::algorithm_recovery::PhysicalBit physical) {
            return physical < 0 ? nlohmann::json(physical) : nlohmann::json{{"bit", physical}, {"smt2", smt(original.function(physical))}};
        };
        for (const auto& port : original.module().at("ports").items())
        {
            if (port.value().at("direction") == "input") continue;
            nlohmann::json bits = nlohmann::json::array();
            for (const auto& bit : port.value().at("bits"))
            {
                const auto physical = hal::algorithm_recovery::physical_bit(bit);
                if (auto it = pad_drivers.find(physical); it != pad_drivers.end())
                    bits.push_back({{"bit", physical}, {"tristate", true}, {"data", driven(it->second.first)}, {"enable", driven(it->second.second)}});
                else bits.push_back(driven(physical));
            }
            out["outputs"][port.key()] = bits;
        }
        std::ofstream stream(file); stream << out.dump() << '\n';
        if (!stream) throw std::runtime_error("cannot write graph dump");
    }
}



int main(int argc, char** argv)
{
    const std::map<std::string, int> arguments{{"import", 5}, {"groups", 4}, {"trace", 6}, {"graph", 7}};
    if (argc < 2 || !arguments.count(argv[1]) || argc < arguments.at(argv[1]))
    {
        std::cerr << "Usage: hal-recover import recovered.json library.hgl empty_output_directory\n"
                     "       hal-recover groups input.hal empty_output_directory [--mi-controls 3] [--mi-types all] [--mi-threads 1] [--dana-threads 4]\n"
                     "       hal-recover trace recovered.json recovered.v cells_sim.v empty_output_directory [--profile-length 768] [--profiles zero,one,pattern,irregular] [--seed 20261031]\n"
                     "                         or --stimulus-json physical_pad_stimulus.json (exclusive with profile/length/seed options)\n"
                     "       hal-recover graph ordered.hal groups.json recovered.json trace_directory empty_output_directory [--window 512] [--profiles PROFILE,...]\n"
                     "                         [--protocol pad_protocol.json] [--align-certificate 1] [--ablation-trace-controls 1]\n"
                     "Native HAL import and word grouping, original-gate trace, schedule certificate, certified SSE word equations\n"
                     "and the event graph (certificate/, graph_<profile>.json, fold_report.json).\n";
        return 2;
    }
    try
    {
        const std::map<std::string, std::set<std::string>> accepted{
            {"groups", {"--mi-controls", "--mi-types", "--mi-threads", "--dana-threads"}},
            {"trace", {"--profile-length", "--profiles", "--seed", "--stimulus-json"}},
            {"graph", {"--window", "--profiles", "--protocol", "--ablation-trace-controls", "--align-certificate"}}};
        const std::string command = argv[1];
        const auto allowed = accepted.count(command) ? accepted.at(command) : std::set<std::string>{};
        const auto options = parse_options(argc, argv, arguments.at(command), allowed);
        if (command == "trace")
        {
            z3::context ctx;
            if (options.count("--stimulus-json") && (options.count("--profiles") || options.count("--profile-length") || options.count("--seed")))
                throw std::runtime_error("--stimulus-json cannot be combined with --profiles, --profile-length, or --seed");
            if (options.count("--stimulus-json") && options.at("--stimulus-json").empty())
                throw std::runtime_error("--stimulus-json requires a nonempty path");
            const auto profiles = options.count("--profiles") ? list(options.at("--profiles")) : std::vector<std::string>{"zero", "one", "pattern", "irregular"};
            auto trace = hal::algorithm_recovery::generate_observations(argv[2], argv[3], argv[4], argv[5], ctx,
                number(options, "--seed", 20261031), number(options, "--profile-length", 768), profiles,
                options.count("--stimulus-json") ? std::filesystem::path(options.at("--stimulus-json")) : std::filesystem::path{});
            if (trace.is_error()) throw std::runtime_error(trace.get_error().get());
            std::cout << "TRACED " << trace.get().pre.size() << " events, " << trace.get().positions.size() << " observed nets\n";
            return 0;
        }
        if (command == "import")
        {
            auto imported = hal::algorithm_recovery::import_recovered(argv[2], argv[3], argv[4]);
            if (imported.is_error()) throw std::runtime_error(imported.get_error().get());
            std::cout << imported.get().receipt.dump(2) << '\n';
            return 0;
        }
        if (!hal::plugin_manager::load("hgl_parser") || !hal::plugin_manager::load("algorithm_recovery"))
            throw std::runtime_error("cannot load hgl_parser or algorithm_recovery");
        auto nl = hal::netlist_serializer::deserialize_from_file(argv[2]);
        if (command == "groups")
        {
            const auto out = std::filesystem::absolute(argv[3]);
            if (std::filesystem::exists(out) && !std::filesystem::is_empty(out)) throw std::runtime_error("group output directory must be empty");
            hal::algorithm_recovery::GroupingOptions settings;
            settings.dana_threads = number(options, "--dana-threads", settings.dana_threads);
            settings.mi_threads = number(options, "--mi-threads", settings.mi_threads);
            settings.max_control_signals = number(options, "--mi-controls", settings.max_control_signals);
            if (options.count("--mi-types") && options.at("--mi-types") != "all")
            {
                settings.types.clear();
                for (auto type : list(options.at("--mi-types")))
                {
                    for (auto& character : type) character = static_cast<char>(std::toupper(static_cast<unsigned char>(character)));
                    settings.types.push_back(hal::enum_from_string<hal::module_identification::CandidateType>(type));
                }
            }
            auto groups = hal::algorithm_recovery::recover_groups(nl.get(), settings);
            if (groups.is_error()) throw std::runtime_error(groups.get_error().get());
            std::filesystem::create_directories(out);
            for (const auto& [name, value] : std::map<std::string, nlohmann::json>{{"dana", groups.get().dana}, {"mi_simple", groups.get().candidates}, {"groups", groups.get().groups}})
            {
                std::ofstream stream(out / (name + ".json")); stream << value.dump(2) << '\n';
                if (!stream) throw std::runtime_error("cannot save " + name);
            }
            if (!hal::netlist_serializer::serialize_to_file(nl.get(), out / "ordered.hal")) throw std::runtime_error("cannot save ordered netlist");
            return 0;
        }
        // graph
        nlohmann::json groups; std::ifstream(argv[3]) >> groups;
        z3::context ctx; hal::algorithm_recovery::OriginalCircuit original(argv[4], ctx);
        auto trace = hal::algorithm_recovery::Observations::load(argv[5]);
        const auto profiles_to_analyze = options.count("--profiles") ? list(options.at("--profiles")) : std::vector<std::string>{};
        const auto window = number(options, "--window", 512);
        std::optional<hal::algorithm_recovery::ScheduleCertificate> certificate;
        if (options.count("--protocol"))
        {
            const auto out = std::filesystem::absolute(argv[6]);
            if (std::filesystem::exists(out) && !std::filesystem::is_empty(out)) throw std::runtime_error("graph output directory must be empty");
            const auto protocol = hal::algorithm_recovery::PadProtocol::load(options.at("--protocol"), original);
            size_t horizon = 0;
            for (auto it = trace.metadata.at("windows").begin(); it != trace.metadata.at("windows").end(); ++it)
                if (profiles_to_analyze.empty() || std::find(profiles_to_analyze.begin(), profiles_to_analyze.end(), it.key()) != profiles_to_analyze.end())
                    horizon = std::max<size_t>(horizon, it.value().back().get<size_t>() + 1);
            auto cert = hal::algorithm_recovery::certify_schedule(original, trace, protocol, ctx, out / "certificate", horizon);
            if (cert.is_error()) throw std::runtime_error(cert.get_error().get());
            certificate = std::move(cert.get());
            std::cout << "CERTIFICATE " << certificate->report.at("status") << std::endl;
        }
        u32 extraction_window = window;
        if (certificate && certificate->certified)
        {
            // Extract only inside the certified periodic region: prefix events
            // before the schedule origin are B-steps, not periodic phases.
            for (auto it = trace.metadata.at("windows").begin(); it != trace.metadata.at("windows").end(); ++it)
                if (profiles_to_analyze.empty() || std::find(profiles_to_analyze.begin(), profiles_to_analyze.end(), it.key()) != profiles_to_analyze.end())
                {
                    const auto end = it.value().back().get<u64>() + 1;
                    if (end <= certificate->origin) throw std::runtime_error("profile window ends before the certified schedule origin");
                    extraction_window = static_cast<u32>(std::min<u64>(extraction_window, end - certificate->origin));
                }
            if (extraction_window != window) std::cout << "WINDOW clipped to certified region: " << extraction_window << std::endl;
        }
        // Evaluation-only ablation: keep the certificate (windows, per-phase constants) but extract
        // equations with EVERY FF control fixed to its trace value, as trace-guided recovery does.
        const bool ablation = options.count("--ablation-trace-controls") != 0;
        if (ablation) std::cout << "ABLATION: all FF controls concretized to trace values (not a certified extraction)" << std::endl;
        auto analyzed = hal::algorithm_recovery::analyze_observations(nl.get(), groups, original, trace, ctx, argv[6], extraction_window, profiles_to_analyze,
            certificate && !ablation ? &certificate->taint : nullptr);
        if (analyzed.is_error()) throw std::runtime_error(analyzed.get_error().get());
        // Check the extracted RAM port model against the trace and the final RAM dump (memory_replay/).
        auto warmed = hal::algorithm_recovery::replay_memories(analyzed.get(), original, trace,
            std::filesystem::path(argv[5]) / "native_ram_final.txt", std::filesystem::path(argv[6]) / "memory_replay");
        if (warmed.is_error()) throw std::runtime_error(warmed.get_error().get());
        // --align-certificate 1: unfold the graph to a multiple of the certificate period, so that the
        // certificate's per-event constants are constants of each graph phase (needed by program recovery).
        const bool align = options.count("--align-certificate") && options.at("--align-certificate") != "0";
        if (align && !(certificate && certificate->certified && certificate->period > 0))
            throw std::runtime_error("--align-certificate needs a certified schedule (--protocol)");
        std::optional<hal::algorithm_recovery::PadProtocol> protocol_for_dump;
        if (options.count("--protocol")) protocol_for_dump = hal::algorithm_recovery::PadProtocol::load(options.at("--protocol"), original);
        nlohmann::json profiles = nlohmann::json::object();
        for (const auto& [name, window_input] : analyzed.get().windows)
        {
            auto input = window_input;
            if (align) input.period_multiple = certificate->period;
            auto folded = hal::algorithm_recovery::fold_observed(input, ctx);
            if (folded.is_error()) throw std::runtime_error(folded.get_error().get());
            const auto& graph = folded.get();
            profiles[name] = fold_diagnostic(graph, input);
            dump_graph(std::filesystem::path(argv[6]) / ("graph_" + name + ".json"), graph, input, original,
                       certificate ? &*certificate : nullptr, protocol_for_dump ? &*protocol_for_dump : nullptr, ctx);
            std::cout << "GRAPH " << name << ' ' << profiles[name].dump() << std::endl;
        }
        if (certificate)
        {
            const nlohmann::json certificate_summary = {{"status", certificate->report.at("status")}, {"certified", certificate->certified},
                {"origin", certificate->origin}, {"period", certificate->period}, {"report", "certificate/certificate.json"},
                {"concretization", analyzed.get().report.at("concretization")}, {"complete_update_words", analyzed.get().report.at("complete_update_words")},
                {"requested_window", window}, {"extraction_window", extraction_window}};
            for (auto& [name, row] : profiles.items()) row["certificate"] = certificate_summary;
        }
        std::ofstream stream(std::filesystem::path(argv[6]) / "fold_report.json"); stream << profiles.dump(2) << '\n';
        if (!stream) throw std::runtime_error("cannot save fold report");
        return 0;
    }
    catch (const std::exception& e)
    {
        std::cerr << e.what() << '\n';
        return 1;
    }
}
