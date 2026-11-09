#include "algorithm_recovery/memory_replay.h"
#include "algorithm_recovery/evaluation.h"
#include "artifacts.h"
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
        using artifacts::require;
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
                require((proof.at("result") == "UNSAT" || proof.at("result") == "CONGRUENCE") && artifacts::file_hash(proof.at("query").get<std::string>()) == proof.at("sha256"), "guided proof identity changed");
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
}
