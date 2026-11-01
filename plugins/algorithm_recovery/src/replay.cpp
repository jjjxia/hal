#include "algorithm_recovery/replay.h"
#include "algorithm_recovery/evaluation.h"
#include "artifacts.h"
#include "process.h"
#include <algorithm>
#include <fstream>
#include <iostream>
#include <optional>

namespace hal::algorithm_recovery
{
    namespace
    {
        using Json = nlohmann::json;
        using Counts = std::map<std::string, uint64_t>;
        using States = std::map<std::string, RamState>;
        using Key = std::pair<std::string, unsigned>;
        using HistoryKey = std::tuple<std::string, unsigned, unsigned>;
        using artifacts::require;
        template<class T> T unwrap(hal::Result<T>&& value)
        { if (value.is_error()) throw std::runtime_error(value.get_error().get()); return std::move(value.get()); }
        struct Packed { Integer value = 0, known = 0; size_t known_bits = 0; };
        Packed packed(const Observations& trace, const std::vector<PhysicalBit>& bits, size_t t, bool after = false)
        {
            Packed result;
            for (unsigned i = 0; i < bits.size(); ++i)
            {
                const auto value = trace.value(bits[i], t, after);
                if (value < 0) continue;
                ++result.known_bits; boost::multiprecision::bit_set(result.known, i);
                if (value) boost::multiprecision::bit_set(result.value, i);
            }
            return result;
        }
        void compare(const Integer& value, const Observations& trace, const std::vector<PhysicalBit>& bits, size_t t, bool after,
                     Counts& counts, const std::string& known, const std::string& unknown, const std::string& label)
        {
            require(value >= 0 && value < (Integer(1) << bits.size()), "out-of-range replay result: " + label);
            const auto expected = packed(trace, bits, t, after);
            counts[known] += expected.known_bits; counts[unknown] += bits.size() - expected.known_bits;
            require(((value ^ expected.value) & expected.known) == 0,
                "known-bit replay mismatch: " + label + " event=" + std::to_string(t) + (after ? " post" : " pre"));
        }
        Integer integer(const Json& value)
        {
            require(value.is_string(), "program value must be a decimal string"); const auto text = value.get<std::string>();
            require(!text.empty() && text.find_first_not_of("0123456789") == std::string::npos, "invalid program bit-vector value");
            Integer result = 0; for (auto c : text) { result *= 10; result += c - '0'; } return result;
        }
        unsigned halfword(const Json& value)
        { require(value.is_number_integer() && value >= 0 && value <= 65535, "invalid program RAM halfword"); return value.get<unsigned>(); }
        Json memory_json(const States& states)
        {
            Json result = Json::object();
            for (const auto& [name, state] : states) result[name] = {{"data", state.memory}, {"read", state.read}};
            return result;
        }
        std::vector<PhysicalBit> pins(const GuidedAnalysis& analysis, const Ice40Ram& memory, const std::string& port)
        {
            std::vector<PhysicalBit> result;
            for (auto id : memory.pins.at(port)) result.push_back(analysis.physical_nets.at(id));
            return result;
        }
        bool rising(const Observations& trace, PhysicalBit clock, size_t t)
        {
            const auto before = trace.value(clock, t), after = trace.value(clock, t, true);
            require(before >= 0 && after >= 0, "replay requires known primary clock edges"); return before == 0 && after == 1;
        }
        PhysicalBit physical_symbol(const std::string& symbol)
        {
            require(symbol.size() > 1 && symbol[0] == 'b' && symbol.find_first_not_of("0123456789", 1) == std::string::npos, "nonphysical warmup boundary: " + symbol);
            return std::stoll(symbol.substr(1));
        }
        void check_trace(const GuidedAnalysis& analysis, const Observations& trace)
        {
            require(analysis.report.at("observation_files") == trace.metadata.at("files"), "replay observation identity differs from extraction");
            require(trace.pre.size() == trace.post.size() && trace.pre.size() == trace.metadata.at("frames"), "incomplete replay observations");
            require(!analysis.windows.empty(), "no guided extraction windows");
            for (const auto& proof : analysis.report.at("proofs"))
                require(proof.at("result") == "UNSAT" && artifacts::file_hash(proof.at("query").get<std::string>()) == proof.at("sha256"), "guided proof identity changed");
        }
        void check_saved_model(const FoldGraph& graph, const Json& model)
        {
            require(model.at("period") == graph.period && model.at("origin") == graph.origin, "saved period/origin mismatch");
            require(model.at("nodes").size() == graph.nodes.size() && model.at("ram_nodes").size() == graph.ram_nodes.size(), "saved node inventory mismatch");
            const auto common = [&](const auto& node, const Json& row) {
                require(row.at("phase") == node.phase && row.at("width") == node.equation.get_sort().bv_size() &&
                    row.at("raw_sha256") == artifacts::sha256(node.equation.to_string()), "saved equation identity mismatch");
                const BitVectorProgram compiled(node.equation);
                require(row.at("sources").size() == compiled.inputs().size() && node.sources.size() == compiled.inputs().size(), "saved source inventory mismatch");
                for (size_t i = 0; i < compiled.inputs().size(); ++i)
                {
                    const auto& [symbol, width] = compiled.inputs()[i]; const auto& source = node.sources.at(symbol); const auto& saved = row.at("sources")[i];
                    const auto kind = source.kind == SourceKind::signal ? "signal" : source.kind == SourceKind::input ? "input" : "ram";
                    require(saved.at("symbol") == symbol && saved.at("width") == width && saved.at("kind") == kind && saved.at("name") == source.name &&
                        saved.at("phase") == source.phase && saved.at("delay") == source.delay, "saved source binding mismatch");
                }
            };
            for (size_t i = 0; i < graph.nodes.size(); ++i)
            { const auto& node = graph.nodes[i]; const auto& row = model.at("nodes")[i]; common(node, row); require(row.at("name") == node.name && row.at("numeric_order_known") == node.numeric_order_known, "saved word/order mismatch"); }
            for (size_t i = 0; i < graph.ram_nodes.size(); ++i)
            { const auto& node = graph.ram_nodes[i]; const auto& row = model.at("ram_nodes")[i]; common(node, row); require(row.at("name") == node.memory && row.at("port") == node.port, "saved RAM port identity mismatch"); }
            Json memories = Json::object(), histories = Json::array();
            for (const auto& [name, memory] : graph.memories)
                memories[name] = {{"initial", memory.initial}, {"read_clock", memory.read_clock}, {"write_clock", memory.write_clock}};
            for (const auto& src : graph.histories) histories.push_back({src.name, src.phase, src.delay});
            require(model.at("memories") == memories && model.at("histories") == histories, "saved RAM/history identity mismatch");
        }
    }

    Result<MemoryReplay> replay_memories(const GuidedAnalysis& analysis, const OriginalCircuit& original, const Observations& trace,
        const std::filesystem::path& final_dump, const std::filesystem::path& output)
    {
        try
        {
            check_trace(analysis, trace); const auto out = std::filesystem::absolute(output), dump = std::filesystem::absolute(final_dump);
            require(!std::filesystem::exists(out) || std::filesystem::is_empty(out), "memory replay directory must be empty"); std::filesystem::create_directories(out);
            require(trace.metadata.at("files").at(dump.string()) == artifacts::file_hash(dump), "final RAM dump identity mismatch");
            const auto& memories = analysis.windows.begin()->second.memories;
            std::map<PhysicalBit, std::pair<std::string, unsigned>> owner;
            std::map<std::vector<PhysicalBit>, std::string> original_cells;
            for (auto cell = original.module().at("cells").begin(); cell != original.module().at("cells").end(); ++cell)
                if (cell.value().at("type") == "SB_RAM40_4K")
                {
                    std::vector<PhysicalBit> q; for (const auto& bit : cell.value().at("connections").at("RDATA")) q.push_back(physical_bit(bit));
                    require(original_cells.emplace(q, cell.key()).second, "aliased original RAM read bus");
                }
            require(original_cells.size() == memories.size(), "original/native RAM inventory mismatch");
            std::map<std::string, std::string> by_cell;
            States state; Counts counts;
            struct Port { std::string memory, name; BitVectorProgram program; std::vector<PhysicalBit> operands, pins; };
            std::vector<Port> functions;
            for (const auto& [name, window] : memories)
            {
                const auto& model = window.model; const auto qs = pins(analysis, model, "RDATA");
                require(qs.size() == 16 && by_cell.emplace(original_cells.at(qs), name).second, "ambiguous RAM source correspondence");
                for (unsigned i = 0; i < qs.size(); ++i) require(qs[i] >= 0 && owner.emplace(qs[i], std::make_pair(name, i)).second, "multiply owned RAM read bit");
                state.emplace(name, RamState{model.initial, 0});
                for (const auto& [port, equation] : model.ports)
                {
                    Port function{name, port, BitVectorProgram(equation), {}, pins(analysis, model, port)};
                    for (const auto& [symbol, width] : function.program.inputs())
                    { require(width == 1, "nonphysical RAM warmup operand"); function.operands.push_back(physical_symbol(symbol)); }
                    functions.push_back(std::move(function));
                }
            }
            MemoryReplay result; result.states.push_back(state);
            for (size_t t = 0; t < trace.pre.size(); ++t)
            {
                std::map<std::string, std::map<std::string, unsigned>> ports;
                // All functions see old internal RAM read registers, never the
                // observed read pins. FF/pad cuts are the explicit warmup inputs.
                for (const auto& function : functions)
                {
                    std::vector<Integer> operands;
                    for (const auto bit : function.operands)
                    {
                        const auto ram = owner.find(bit);
                        if (ram != owner.end()) operands.push_back((state.at(ram->second.first).read >> ram->second.second) & 1);
                        else
                        { const auto value = trace.value(bit, t); counts["unknown_operand_observations"] += value < 0; operands.push_back(value < 0 ? 0 : value); }
                    }
                    const auto value = function.program.evaluate(operands);
                    ports[function.memory][function.name] = value.convert_to<unsigned>();
                    compare(value, trace, function.pins, t, false, counts, "known_observations", "unknown_observations", function.memory + "." + function.name);
                }
                for (const auto& [name, window] : memories)
                {
                    const auto& model = window.model; const auto& p = ports.at(name);
                    const bool re = rising(trace, analysis.physical_nets.at(model.read_clock), t), we = rising(trace, analysis.physical_nets.at(model.write_clock), t);
                    const bool reading = re && p.at("RE") && p.at("RCLKE"), writing = we && p.at("WE") && p.at("WCLKE");
                    counts["enabled_reads"] += reading; counts["enabled_writes"] += writing;
                    if (writing) counts["written_bit_events"] += __builtin_popcount((~p.at("MASK")) & 65535);
                    const auto qs = pins(analysis, model, "RDATA");
                    compare(state.at(name).read, trace, qs, t, false, counts, "known_observations", "unknown_observations", name + ".RDATA");
                    const RamPorts values{static_cast<u16>(p.at("RADDR")), static_cast<u16>(p.at("WADDR")), static_cast<u16>(p.at("WDATA")), static_cast<u16>(p.at("MASK")),
                        bool(p.at("RE")), bool(p.at("RCLKE")), bool(p.at("WE")), bool(p.at("WCLKE"))};
                    step_ram(state.at(name), values, re, we);
                    compare(state.at(name).read, trace, qs, t, true, counts, "known_observations", "unknown_observations", name + ".RDATA");
                }
                result.states.push_back(state);
                if (t % 512 == 511) std::cout << "RAM_REPLAY " << t + 1 << '/' << trace.pre.size() << std::endl;
            }
            std::ifstream input(dump); require(bool(input), "cannot read final RAM dump"); std::string line;
            std::set<std::pair<std::string, unsigned>> seen;
            while (std::getline(input, line))
            {
                std::istringstream row(line); std::string cell, text, extra; unsigned address;
                require(bool(row >> cell >> address >> text) && !(row >> extra) && address < 256 && text.size() == 16 && text.find_first_not_of("01xz") == std::string::npos, "malformed final RAM dump");
                require(by_cell.count(cell) && seen.emplace(cell, address).second, "unknown/duplicate final RAM dump row");
                const auto value = state.at(by_cell.at(cell)).memory[address];
                for (unsigned bit = 0; bit < 16; ++bit)
                {
                    const auto expected = text[15 - bit];
                    if (expected == 'x' || expected == 'z') { ++counts["final_storage_unknown_bits"]; continue; }
                    ++counts["final_storage_known_bits"];
                    require(((value >> bit) & 1) == unsigned(expected - '0'), "final original-simulator RAM storage mismatch: " + cell + " address=" + std::to_string(address));
                }
            }
            require(seen.size() == memories.size() * 256, "incomplete final RAM storage dump");
            result.report = {{"status", "HAL_PORT_RAM_STATE_ORIGINAL_GATE_REPLAY_PASS"}, {"counts", counts}, {"instances", memories.size()}, {"events", trace.pre.size()},
                {"RAM_read_observations_used_as_inputs", 0}, {"source_cells", by_cell}, {"final_dump_sha256", artifacts::file_hash(dump)},
                {"initial_read_choice", 0}, {"unknown_operand_choice", 0}, {"observation_files", trace.metadata.at("files")}};
            artifacts::write(out / "memory_replay.json", result.report.dump(2) + '\n');
            std::cout << "RAM_REPLAY_PASS " << Json(counts).dump() << std::endl;
            return OK(std::move(result));
        }
        catch (const std::exception& error) { return ERR(error.what()); }
    }

    Result<Json> replay_program(const std::string& profile, const FoldGraph& graph, const GuidedAnalysis& analysis,
        const Observations& trace, const MemoryReplay& warm, const std::filesystem::path& program_directory,
        const std::filesystem::path& output, const ReplayOptions& options)
    {
        try
        {
            const auto out = std::filesystem::absolute(output), program = std::filesystem::absolute(program_directory);
            require(options.minimum_training_frames > 0 && options.minimum_withheld_frames > 0 && options.process_timeout_ms > 0, "replay requires positive training/withheld coverage and timeout");
            require(!std::filesystem::exists(out) || std::filesystem::is_empty(out), "program replay directory must be empty"); std::filesystem::create_directories(out);
            if (!graph.available)
            {
                require(Json::parse(artifacts::read(program / "program.json")).at("status") == "NO_GRAPH", "absent graph acquired a program");
                Json report = {{"status", "NO_GRAPH"}, {"period", graph.period}};
                artifacts::write(out / "replay.json", report.dump(2) + '\n'); return OK(std::move(report));
            }
            check_trace(analysis, trace);
            require(warm.states.size() == trace.pre.size() + 1 && warm.report.at("observation_files") == trace.metadata.at("files"), "RAM warmup does not cover these observations");
            const auto model = unwrap(check_program_artifacts(program)); check_saved_model(graph, model);
            const auto& input = analysis.windows.at(profile);
            require(graph.period > 0 && graph.origin >= input.start && graph.origin + graph.period <= input.start + input.length, "graph outside extraction window");
            std::map<std::string, std::vector<PhysicalBit>> boundaries;
            for (const auto& [name, bits] : input.inputs)
                for (const auto& bit : bits) boundaries[name].push_back(physical_symbol(bit));
            for (const auto& [name, word] : input.words)
                if (!graph.periods.count(name)) boundaries.emplace("cut_" + name, analysis.words.at(name).q);
            std::set<std::string> active; std::set<Key> expected_nodes;
            for (const auto& node : graph.nodes) { active.insert(node.name); expected_nodes.emplace(node.name, node.phase); }
            require(!active.empty(), "no active words for replay contract");
            Json contract = Json::object();
            for (const auto& name : active)
            {
                const auto& word = analysis.words.at(name); Json phases = Json::array();
                require(word.updates.size() == trace.pre.size(), "missing FF observation updates");
                for (unsigned phase = 0; phase < graph.period; ++phase)
                {
                    const auto t = graph.origin + phase; Json mask = Json::array(), reset = Json::array(), conditions = Json::array();
                    const auto& updates = word.updates.at(t);
                    const auto full = std::all_of(updates.begin(), updates.end(), [](const auto& u) { return u.write == Write::full; });
                    const auto hold = std::all_of(updates.begin(), updates.end(), [](const auto& u) { return u.write == Write::hold; });
                    require((full || hold) && full == bool(expected_nodes.count({name, phase})), "graph/physical write schedule mismatch");
                    for (const auto& update : updates) { mask.push_back(update.write == Write::full ? 1 : 0); reset.push_back(update.reset_value); }
                    if (full) for (const auto bit : word.control_cone)
                    { const auto value = trace.value(bit, t); if (value >= 0) conditions.push_back({bit_json(bit), value}); }
                    phases.push_back({{"mask", mask}, {"reset", reset}, {"controls", conditions}});
                }
                contract[name] = phases;
            }
            artifacts::write(out / "contract.json", contract.dump(2) + '\n');
            const auto applicable = [&](size_t start) {
                for (const auto& name : active)
                {
                    const auto& word = analysis.words.at(name);
                    for (unsigned phase = 0; phase < graph.period; ++phase)
                    {
                        const auto& wanted = word.updates.at(graph.origin + phase); const auto& actual = word.updates.at(start + phase);
                        if (wanted.size() != actual.size()) return false;
                        for (unsigned bit = 0; bit < wanted.size(); ++bit)
                            if (wanted[bit].write != actual[bit].write || wanted[bit].reset_value != actual[bit].reset_value) return false;
                        if (!std::all_of(wanted.begin(), wanted.end(), [](const auto& u) { return u.write == Write::full; })) continue;
                        for (const auto bit : word.control_cone)
                        { const auto value = trace.value(bit, graph.origin + phase); if (value >= 0 && trace.value(bit, start + phase) != value) return false; }
                    }
                }
                return true;
            };
            const std::vector<std::string> command{options.compiler, "-std=c++17", "-O0", (program / "recovered.cpp").string(), "-o", (out / "recovered").string()};
            process::run({options.compiler, "--version"}, out / "compiler_version.txt", options.process_timeout_ms);
            process::run(command, out / "compile.log", options.process_timeout_ms);
            unwrap(check_program_artifacts(program));
            process::JsonProcess executable({(out / "recovered").string()}, out / "program_stderr.log", options.process_timeout_ms);
            require(executable.request({{"command", "metadata"}}) == model, "compiled program metadata/dispatch contract differs");
            require(executable.request({{"command", "initial"}}).at("memories") == memory_json(warm.states.front()), "compiled RAM INIT differs");
            const auto& window = trace.metadata.at("windows").at(profile);
            require(!window.empty(), "empty replay profile"); const size_t first = window.front(), last = window.back();
            require(window.size() == last - first + 1 && last < trace.pre.size(), "invalid profile range");
            for (size_t i = 0; i < window.size(); ++i) require(window[i] == first + i, "noncontiguous replay profile");
            Counts counts; std::optional<size_t> previous; Json history = Json::array(), ram_state;
            std::ofstream frames(out / "frames.jsonl"); require(bool(frames), "cannot write replay frame receipts");
            for (size_t start = first; start + graph.period <= last + 1; ++start)
            {
                if (start % graph.period != graph.origin % graph.period) continue;
                std::string kind;
                if (start >= input.start && start + graph.period <= input.start + input.length) kind = "training";
                else if (start + graph.period <= input.start || start >= input.start + input.length) kind = "withheld";
                else { ++counts["partial_training_overlap_frames"]; previous.reset(); continue; }
                if (!applicable(start)) { ++counts[kind + "_outside_contract"]; previous.reset(); continue; }
                if (!previous || *previous + graph.period != start)
                {
                    bool available = true;
                    for (const auto& src : graph.histories) if (start + src.phase < src.delay * graph.period) available = false;
                    if (!available) { ++counts[kind + "_insufficient_history_frames"]; previous.reset(); continue; }
                    history = Json::array(); ram_state = memory_json(warm.states.at(start));
                    for (const auto& src : graph.histories)
                    {
                        const auto& qs = analysis.words.at(src.name).q;
                        const auto value = packed(trace, qs, start + src.phase - src.delay * graph.period, true);
                        history.push_back({src.name, src.phase, src.delay, value.value.str()}); counts[kind + "_unknown_history_bits"] += qs.size() - value.known_bits;
                    }
                    ++counts[kind + "_initialized_segments"];
                }
                Json operands = Json::array();
                for (const auto& row : model.at("inputs"))
                {
                    const std::string name = row[0]; const unsigned phase = row[1], width = row[2]; require(phase < graph.period, "input phase outside frame");
                    if (name.rfind("event_rise_", 0) == 0)
                    {
                        require(width == 1, "bad clock-edge width"); const auto id = std::stoul(name.substr(11));
                        operands.push_back({name, phase, rising(trace, analysis.physical_nets.at(id), start + phase) ? "1" : "0"});
                    }
                    else
                    {
                        const auto& bits = boundaries.at(name); require(bits.size() == width, "boundary width mismatch");
                        const auto value = packed(trace, bits, start + phase);
                        counts[kind + "_unknown_boundary_bits"] += bits.size() - value.known_bits; operands.push_back({name, phase, value.value.str()});
                    }
                }
                const Json request{{"command", "run"}, {"history", history}, {"inputs", operands}, {"memories", ram_state}}; Json response;
                try
                {
                    response = executable.request(request);
                    require(response.at("memories") == memory_json(warm.states.at(start + graph.period)), "coupled program RAM state differs from original-gate warmup");
                    require(response.at("reads").size() == graph.period, "wrong program read-trace length");
                    for (unsigned phase = 0; phase < graph.period; ++phase)
                    {
                        const auto& reads = response.at("reads")[phase]; require(reads.size() == graph.memories.size(), "wrong program read-trace inventory");
                        for (const auto& [name, memory] : graph.memories)
                        {
                            const auto& pair = reads.at(name); require(pair.size() == 2, "invalid before/after RAM trace");
                            const auto before = halfword(pair[0]), after = halfword(pair[1]);
                            require(before == warm.states.at(start + phase).at(name).read && after == warm.states.at(start + phase + 1).at(name).read, "internal RAM read transition differs from warmup");
                            const auto qs = pins(analysis, memory, "RDATA");
                            compare(Integer(before), trace, qs, start + phase, false, counts, kind + "_ram_known_bits", kind + "_ram_unknown_bits", name);
                            compare(Integer(after), trace, qs, start + phase, true, counts, kind + "_ram_known_bits", kind + "_ram_unknown_bits", name);
                        }
                    }
                    std::map<Key, Integer> values; std::set<Key> seen;
                    for (const auto& row : response.at("values"))
                    {
                        require(row.size() == 3, "invalid program node row"); const Key key{row[0], row[1]};
                        require(expected_nodes.count(key) && seen.insert(key).second, "unexpected/duplicate program node"); const auto value = integer(row[2]);
                        compare(value, trace, analysis.words.at(key.first).q, start + key.second, true, counts,
                            kind + "_compared_bits", kind + "_unknown_output_bits", key.first); values.emplace(key, value);
                    }
                    require(seen == expected_nodes, "missing program node values");
                    std::map<HistoryKey, Integer> old;
                    for (const auto& row : history) old.emplace(HistoryKey{row[0], row[1], row[2]}, integer(row[3]));
                    Json expected = Json::array();
                    for (const auto& src : graph.histories)
                    {
                        const auto& value = src.delay == 1 ? values.at({src.name, src.phase}) : old.at({src.name, src.phase, src.delay - 1});
                        expected.push_back({src.name, src.phase, src.delay, value.str()});
                    }
                    require(response.at("history") == expected, "program history shift mismatch");
                }
                catch (...)
                {
                    artifacts::write(out / "failed_request.json", request.dump(2) + '\n'); artifacts::write(out / "failed_response.json", response.dump(2) + '\n');
                    artifacts::write(out / "failed_frame.json", Json{{"start", start}, {"kind", kind}}.dump(2) + '\n'); throw;
                }
                history = response.at("history"); ram_state = response.at("memories"); previous = start; ++counts[kind + "_frames"];
                frames << Json{{"start", start}, {"kind", kind}, {"request_sha256", artifacts::sha256(request.dump())}, {"response_sha256", artifacts::sha256(response.dump())}}.dump() << '\n';
                if ((counts["training_frames"] + counts["withheld_frames"]) % 10 == 0)
                    std::cout << "REPLAY " << profile << ' ' << counts["training_frames"] << " training, " << counts["withheld_frames"] << " withheld" << std::endl;
            }
            executable.finish(); frames.close(); require(bool(frames), "frame receipt write failed");
            require(counts["training_frames"] >= options.minimum_training_frames && counts["withheld_frames"] >= options.minimum_withheld_frames, "insufficient applicable training/withheld replay coverage");
            Json report = {{"status", "SAVED_PROGRAM_ORIGINAL_GATE_REPLAY_PASS"}, {"counts", counts}, {"period", graph.period}, {"nodes", graph.nodes.size()},
                {"minimum_training_frames", options.minimum_training_frames}, {"minimum_withheld_frames", options.minimum_withheld_frames},
                {"mismatching_known_bits", 0}, {"generated_RAM_initialization_checked", true}, {"RAM_read_inputs", 0},
                {"compiler_command", command}, {"compiler_version_sha256", artifacts::file_hash(out / "compiler_version.txt")},
                {"executable_sha256", artifacts::file_hash(out / "recovered")}, {"program_manifest_sha256", artifacts::file_hash(program / "program.json")},
                {"contract_sha256", artifacts::file_hash(out / "contract.json")}, {"frames_sha256", artifacts::file_hash(out / "frames.jsonl")},
                {"scope", "Saved compiled program replay on applicable original-gate training/withheld observations. RAM from INIT and symbolic ports; explicit FF boundaries/history and unknown-bit counts. No unconditional period or whole-device equivalence claim."}};
            artifacts::write(out / "replay.json", report.dump(2) + '\n');
            std::cout << "REPLAY_PASS " << profile << ' ' << Json(counts).dump() << std::endl;
            return OK(std::move(report));
        }
        catch (const std::exception& error) { return ERR(error.what()); }
    }
}
