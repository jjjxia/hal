#include "algorithm_recovery/memory.h"
#include "algorithm_recovery/frontend.h"
#include "algorithm_recovery/grouping.h"
#include "algorithm_recovery/observations.h"
#include "algorithm_recovery/guided.h"
#include "algorithm_recovery/lifting.h"
#include "algorithm_recovery/certify.h"
#include "hal_core/netlist/netlist.h"
#include "hal_core/netlist/persistent/netlist_serializer.h"
#include "hal_core/plugin_system/plugin_manager.h"
#include <nlohmann/json.hpp>
#include <fstream>
#include <iostream>
#include "algorithm_recovery/program.h"
#include "algorithm_recovery/replay.h"
#include "algorithm_recovery/state_machine.h"
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
        for (const auto& port : original.module().at("ports").items())
        {
            if (port.value().at("direction") == "input") continue;
            nlohmann::json bits = nlohmann::json::array();
            for (const auto& bit : port.value().at("bits"))
            {
                const auto physical = hal::algorithm_recovery::physical_bit(bit);
                bits.push_back(physical < 0 ? nlohmann::json(physical) : nlohmann::json{{"bit", physical}, {"smt2", smt(original.function(physical))}});
            }
            out["outputs"][port.key()] = bits;
        }
        std::ofstream stream(file); stream << out.dump() << '\n';
        if (!stream) throw std::runtime_error("cannot write graph dump");
    }
}


int main(int argc, char** argv)
{
    const std::map<std::string, int> arguments{{"extract-ram", 4}, {"groups", 4}, {"import", 5}, {"trace", 6}, {"analyze", 7}, {"synthesize", 5}, {"state", 7}, {"replay-state", 5}, {"certify", 6}, {"graph", 7}};
    if (argc < 2 || !arguments.count(argv[1]) || argc < arguments.at(argv[1]))
    {
        std::cerr << "Usage: hal-recover extract-ram input.hal output.json\n"
                     "       hal-recover synthesize proved_program_directory arithmetic.json empty_output_directory\n"
                     "       hal-recover replay-state state_program_directory trace_directory empty_output_directory\n"
                     "       hal-recover state ordered.hal groups.json recovered.json trace_directory empty_output_directory [--window 512] [--profiles PROFILE,...]\n"
                     "       hal-recover import recovered.json library.hgl empty_output_directory\n"
                     "       hal-recover groups input.hal empty_output_directory [--mi-controls 3] [--mi-types all] [--mi-threads 1] [--dana-threads 4]\n"
                     "       hal-recover trace recovered.json recovered.v cells_sim.v empty_output_directory [--profile-length 768] [--profiles zero,one,pattern,irregular] [--seed 20261031]\n"
                     "                         or --stimulus-json physical_pad_stimulus.json (exclusive with profile/length/seed options)\n"
                     "       hal-recover analyze ordered.hal groups.json recovered.json trace_directory empty_output_directory [--window 512] [--profiles PROFILE,...] [--compare-window N] [--protocol pad_protocol.json]\n"
                     "       hal-recover graph (same arguments as analyze): certificate, SSE and folding only; writes graph_<profile>.json\n"
                     "       hal-recover certify recovered.json trace_directory pad_protocol.json empty_output_directory [--horizon N]\n"
                     "Native HAL import/grouping, original-gate trace, SSE/folding, checked lifting, C++ export and coupled replay.\n";
        return 2;
    }
    try
    {
        const std::map<std::string, std::set<std::string>> accepted{
            {"groups", {"--mi-controls", "--mi-types", "--mi-threads", "--dana-threads"}},
            {"trace", {"--profile-length", "--profiles", "--seed", "--stimulus-json"}}, {"analyze", {"--window", "--profiles", "--compare-window", "--protocol"}},
            {"state", {"--window", "--profiles", "--protocol"}}, {"certify", {"--horizon"}},
            {"graph", {"--window", "--profiles", "--protocol"}}};
        const auto allowed = accepted.count(argv[1]) ? accepted.at(argv[1]) : std::set<std::string>{};
        const auto options = parse_options(argc, argv, arguments.at(argv[1]), allowed);
        if (std::string(argv[1]) == "replay-state")
        {
            const auto trace = hal::algorithm_recovery::Observations::load(argv[3]);
            auto replay = hal::algorithm_recovery::replay_state_machine(argv[2], trace, argv[3], argv[4]);
            if (replay.is_error()) throw std::runtime_error(replay.get_error().get());
            std::cout << replay.get().dump(2) << '\n'; return 0;
        }
        if (std::string(argv[1]) == "synthesize")
        {
            z3::context ctx;
            auto result = hal::algorithm_recovery::resynthesize_program(argv[2], argv[3], argv[4], ctx);
            if (result.is_error()) throw std::runtime_error(result.get_error().get());
            auto replay = hal::algorithm_recovery::replay_resynthesis(argv[2], argv[4], std::filesystem::path(argv[4]) / "replay");
            if (replay.is_error()) throw std::runtime_error(replay.get_error().get());
            std::cout << result.get().dump(2) << '\n' << replay.get().at("status") << '\n'; return 0;
        }
        if (std::string(argv[1]) == "trace")
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
        if (std::string(argv[1]) == "certify")
        {
            z3::context ctx; hal::algorithm_recovery::OriginalCircuit original(argv[2], ctx);
            const auto trace = hal::algorithm_recovery::Observations::load(argv[3]);
            const auto protocol = hal::algorithm_recovery::PadProtocol::load(argv[4], original);
            auto cert = hal::algorithm_recovery::certify_schedule(original, trace, protocol, ctx, argv[5], number(options, "--horizon", static_cast<u32>(trace.pre.size() - 1)));
            if (cert.is_error()) throw std::runtime_error(cert.get_error().get());
            std::cout << cert.get().report.at("status") << '\n';
            return cert.get().certified ? 0 : 1;
        }
        if (std::string(argv[1]) == "import")
        {
            auto imported = hal::algorithm_recovery::import_recovered(argv[2], argv[3], argv[4]);
            if (imported.is_error()) throw std::runtime_error(imported.get_error().get());
            std::cout << imported.get().receipt.dump(2) << '\n';
            return 0;
        }
        if (!hal::plugin_manager::load("hgl_parser") || !hal::plugin_manager::load("algorithm_recovery"))
            throw std::runtime_error("cannot load hgl_parser or algorithm_recovery");
        auto nl = hal::netlist_serializer::deserialize_from_file(argv[2]);
        if (std::string(argv[1]) == "analyze" || std::string(argv[1]) == "state" || std::string(argv[1]) == "graph")
        {
            nlohmann::json groups; std::ifstream(argv[3]) >> groups;
            z3::context ctx; hal::algorithm_recovery::OriginalCircuit original(argv[4], ctx);
            auto trace = hal::algorithm_recovery::Observations::load(argv[5]);
            const auto profiles_to_analyze = options.count("--profiles") ? list(options.at("--profiles")) : std::vector<std::string>{};
            const auto window = number(options, "--window", 512), comparison_window = number(options, "--compare-window", 0);
            if (options.count("--compare-window") && (!comparison_window || comparison_window > window))
                throw std::runtime_error("comparison window must be positive and no larger than extraction window");
            std::optional<hal::algorithm_recovery::ScheduleCertificate> certificate;
            if (options.count("--protocol"))
            {
                const auto out = std::filesystem::absolute(argv[6]);
                if (std::filesystem::exists(out) && !std::filesystem::is_empty(out)) throw std::runtime_error("analysis output directory must be empty");
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
            auto analyzed = hal::algorithm_recovery::analyze_observations(nl.get(), groups, original, trace, ctx, argv[6], extraction_window, profiles_to_analyze,
                certificate ? &certificate->taint : nullptr);
            if (analyzed.is_error()) throw std::runtime_error(analyzed.get_error().get());
            if (std::string(argv[1]) == "state")
            {
                auto result = hal::algorithm_recovery::export_state_machine(nl.get(), groups, original, analyzed.get(), std::filesystem::path(argv[6]) / "state_machine", ctx);
                if (result.is_error()) throw std::runtime_error(result.get_error().get());
                auto replay = hal::algorithm_recovery::replay_state_machine(std::filesystem::path(argv[6]) / "state_machine", trace, argv[5], std::filesystem::path(argv[6]) / "state_replay");
                if (replay.is_error()) throw std::runtime_error(replay.get_error().get());
                std::cout << replay.get().dump(2) << '\n'; return 0;
            }
            auto warmed = hal::algorithm_recovery::replay_memories(analyzed.get(), original, trace,
                std::filesystem::path(argv[5]) / "native_ram_final.txt", std::filesystem::path(argv[6]) / "memory_replay");
            if (warmed.is_error()) throw std::runtime_error(warmed.get_error().get());
            nlohmann::json profiles = nlohmann::json::object();
            nlohmann::json replays = nlohmann::json::object();
            bool periodic_replay_failed = false;
            hal::algorithm_recovery::ArithmeticRecovery arithmetic;
            for (const auto& [name, input] : analyzed.get().windows)
            {
                auto folded = hal::algorithm_recovery::fold_observed(input, ctx);
                if (folded.is_error()) throw std::runtime_error(folded.get_error().get());
                const auto& graph = folded.get();
                profiles[name] = fold_diagnostic(graph, input);
                {
                    std::optional<hal::algorithm_recovery::PadProtocol> protocol_for_dump;
                    if (options.count("--protocol")) protocol_for_dump = hal::algorithm_recovery::PadProtocol::load(options.at("--protocol"), original);
                    dump_graph(std::filesystem::path(argv[6]) / ("graph_" + name + ".json"), graph, input, original,
                               certificate ? &*certificate : nullptr, protocol_for_dump ? &*protocol_for_dump : nullptr, ctx);
                }
                if (std::string(argv[1]) == "graph") { std::cout << "GRAPH " << name << ' ' << profiles[name].dump() << std::endl; continue; }
                if (options.count("--compare-window"))
                {
                    const auto length = comparison_window;
                    hal::algorithm_recovery::FoldInput smaller; smaller.length = length; smaller.start = input.start + input.length - length;
                    smaller.inputs = input.inputs; smaller.memories = input.memories;
                    for (const auto& [word_name, word] : input.words)
                    {
                        hal::algorithm_recovery::WordWindow row; row.bits = word.bits; row.numeric_order_known = word.numeric_order_known;
                        row.writes.assign(word.writes.end() - length, word.writes.end()); row.equations.assign(word.equations.end() - length, word.equations.end());
                        smaller.words.emplace(word_name, std::move(row));
                    }
                    auto comparison = hal::algorithm_recovery::fold_observed(smaller, ctx);
                    if (comparison.is_error()) throw std::runtime_error(comparison.get_error().get());
                    profiles[name]["comparison"] = fold_diagnostic(comparison.get(), smaller);
                    profiles[name]["comparison"]["scope"] = "Diagnostic only: exact suffix of the same proved extraction; no graph replacement or exclusion of newly periodic words";
                }
                std::cout << "FOLDED " << name << ' ' << profiles[name].dump() << std::endl;
                auto routing = hal::algorithm_recovery::recover_routing(graph, input, std::filesystem::path(argv[6]) / ("routing_" + name));
                if (routing.is_error()) throw std::runtime_error(routing.get_error().get());
                profiles[name]["routing_groups"] = routing.get().accepted.size();
                std::cout << "ROUTING " << name << ' ' << routing.get().accepted.size() << " accepted" << std::endl;
                auto lifted = hal::algorithm_recovery::lift_arithmetic(graph, arithmetic, std::filesystem::path(argv[6]) / ("lift_" + name), routing.get());
                if (lifted.is_error()) throw std::runtime_error(lifted.get_error().get());
                profiles[name]["arithmetic_counts"] = lifted.get().at("counts");
                profiles[name]["fit_calls"] = lifted.get().at("fit_calls");
                std::cout << "LIFTED " << name << ' ' << lifted.get().at("counts").dump() << std::endl;
                auto program = hal::algorithm_recovery::export_program(graph, std::filesystem::path(argv[6]) / ("program_" + name), {}, lifted.get());
                if (program.is_error()) throw std::runtime_error(program.get_error().get());
                profiles[name]["program_status"] = program.get().at("status");
                auto replay = hal::algorithm_recovery::replay_program(name, graph, analyzed.get(), trace, warmed.get(),
                    std::filesystem::path(argv[6]) / ("program_" + name), std::filesystem::path(argv[6]) / ("replay_" + name));
                if (replay.is_error())
                {
                    periodic_replay_failed = true;
                    replays[name] = {{"status", "SAVED_PROGRAM_ORIGINAL_GATE_REPLAY_FAILED"}, {"reason", replay.get_error().get()}};
                    std::cerr << "REPLAY_FAILED " << name << ' ' << replay.get_error().get() << '\n';
                }
                else replays[name] = replay.get();
                profiles[name]["replay_status"] = replays.at(name).at("status");
            }
            nlohmann::json certificate_summary;
            if (certificate)
            {
                certificate_summary = {{"status", certificate->report.at("status")}, {"certified", certificate->certified},
                    {"origin", certificate->origin}, {"period", certificate->period}, {"report", "certificate/certificate.json"},
                    {"concretization", analyzed.get().report.at("concretization")}, {"complete_update_words", analyzed.get().report.at("complete_update_words")},
                    {"requested_window", window}, {"extraction_window", extraction_window}};
                for (auto& [name, row] : profiles.items()) row["certificate"] = certificate_summary;
            }
            std::ofstream stream(std::filesystem::path(argv[6]) / "fold_report.json"); stream << profiles.dump(2) << '\n';
            if (std::string(argv[1]) == "graph") return 0;
            if (!stream) throw std::runtime_error("cannot save fold report");
            bool executed = false;
            for (const auto& replay : replays) executed |= replay.at("status") == "SAVED_PROGRAM_ORIGINAL_GATE_REPLAY_PASS";
            nlohmann::json verified = {{"status", executed ? "NATIVE_CPP_CHAIN_AUDITED" : "NO_EXECUTABLE_GRAPH"}, {"profiles", replays}, {"ram", warmed.get().report},
                {"groups", analyzed.get().report.at("groups")}, {"FF_bits", analyzed.get().report.at("FF_bits")},
                {"HAL_SSE_calls", analyzed.get().report.at("HAL_calls")}, {"RAM_port_proofs", analyzed.get().report.at("RAM_port_proofs")},
                {"observation_files", trace.metadata.at("files")}, {"manual_semantic_inputs", trace.metadata.value("manual_semantic_inputs", nlohmann::json::array())},
                {"scope", "Native HAL grouping/order, conditional guided SSE, observed folding, checked routing/arithmetic, saved C++ expression proofs and original-gate training/withheld coupled replay. Explicit FF cuts, unresolved orders and observed schedules remain limitations; no whole-device equivalence claim."}};
            const auto state_path = std::filesystem::path(argv[6]) / "state_machine";
            auto state_program = hal::algorithm_recovery::export_state_machine(nl.get(), groups, original, analyzed.get(), state_path, ctx);
            bool state_pass = false;
            if (state_program.is_error()) verified["state_machine"] = {{"status", "STATE_EXPORT_FAILED"}, {"reason", state_program.get_error().get()}};
            else
            {
                verified["state_machine"]["program"] = state_program.get();
                auto state_replay = hal::algorithm_recovery::replay_state_machine(state_path, trace, argv[5], std::filesystem::path(argv[6]) / "state_replay");
                if (state_replay.is_error()) verified["state_machine"]["replay"] = {{"status", "STATE_REPLAY_FAILED"}, {"reason", state_replay.get_error().get()}};
                else { state_pass = true; verified["state_machine"]["replay"] = state_replay.get(); }
            }
            verified["state_machine"]["scope"] = "Separate guarded event backend includes all FF/RAM storage, clocks/resets/enable/hold and unconditional fallbacks. No global observed period required. Proof scope and finite replay/X limitations are in its own receipts.";
            verified["status"] = !state_pass ? "PARTIAL_RECOVERY_STATE_STAGE_FAILED" : periodic_replay_failed ? "PARTIAL_RECOVERY_PERIODIC_REPLAY_FAILED"
                : executed ? "NATIVE_CPP_PERIODIC_AND_STATE_CHAIN_AUDITED" : "NATIVE_CPP_STATE_CHAIN_AUDITED";
            if (certificate) verified["certificate"] = certificate_summary;
            std::ofstream receipt(std::filesystem::path(argv[6]) / "native_verified.json"); receipt << verified.dump(2) << '\n';
            if (!receipt) throw std::runtime_error("cannot save native verification report");
            if (!state_pass) std::cerr << verified["state_machine"].dump(2) << '\n';
            return state_pass && !periodic_replay_failed ? 0 : 1;
        }
        if (std::string(argv[1]) == "groups")
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
        z3::context ctx;
        auto result = hal::algorithm_recovery::extract_ice40_rams(nl.get(), ctx);
        if (result.is_error())
            throw std::runtime_error(result.get_error().get());
        nlohmann::json out = {{"schema", 1}, {"status", "RAM_PORTS_EXTRACTED"},
            {"semantics", "SB_RAM40_4K mode 0, read-before-write, MASK, INIT; read register initially unconstrained"},
            {"memories", nlohmann::json::object()}};
        for (const auto& ram : result.get())
        {
            auto& row = out["memories"][ram.name];
            row = {{"gate_id", ram.gate_id}, {"rclk", ram.read_clock}, {"wclk", ram.write_clock},
                   {"initial", ram.initial}, {"pins", ram.pins}};
            for (const auto& [port, term] : ram.ports)
                row["functions"][port] = {{"width", term.get_sort().bv_size()}, {"smt2", term.to_string()}};
        }
        std::ofstream stream(argv[3]);
        stream << out.dump(2) << '\n';
        if (!stream)
            throw std::runtime_error("cannot write output");
        std::cout << "Extracted " << result.get().size() << " RAMs using native HAL SSE\n";
    }
    catch (const std::exception& e)
    {
        std::cerr << e.what() << '\n';
        return 1;
    }
    return 0;
}
