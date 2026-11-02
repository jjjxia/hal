#include "algorithm_recovery/state_machine.h"
#include "algorithm_recovery/lifting.h"
#include "artifacts.h"
#include "state_driver.h"
#include "hal_core/netlist/netlist.h"
#include "hal_core/netlist/gate.h"
#include "hal_core/netlist/module.h"
#include "hal_core/netlist/net.h"
#include "sequential_symbolic_execution/sequential_symbolic_execution.h"
#include <iostream>

namespace hal::algorithm_recovery
{
    namespace
    {
        using Json = nlohmann::json;
        using artifacts::require;
        template<class T> T unwrap(hal::Result<T>&& value)
        { if (value.is_error()) throw std::runtime_error(value.get_error().get()); return std::move(value.get()); }
        std::string control_name(PhysicalBit bit)
        { return bit >= 0 ? "c_b" + std::to_string(bit) : bit == -1 ? "c_zero" : "c_one"; }
    }

    Result<Json> export_state_machine(Netlist* netlist, const Json& groups, OriginalCircuit& original,
        const GuidedAnalysis& guided, const std::filesystem::path& output, z3::context& ctx, const StateMachineOptions& options)
    {
        try
        {
            require(netlist && options.max_delta_events > 0 && !guided.windows.empty(), "missing native state-machine input");
            require(groups.at("words").size() == guided.words.size(), "grouped/guided state inventory mismatch");
            const auto out = std::filesystem::absolute(output);
            require(!std::filesystem::exists(out) || std::filesystem::is_empty(out), "state-machine output directory must be empty");
            std::filesystem::create_directories(out / "proofs");
            Json schema{{"execution_kind", "GUARDED_EVENT_TRANSITIONS"}, {"words", Json::object()}, {"memories", Json::object()},
                {"pads", Json::object()}, {"outputs", Json::object()}, {"bindings", Json::object()}, {"proofs", Json::array()},
                {"source_json", original.source_path}, {"source_sha256", original.source_sha256}, {"manual_semantic_inputs", guided.report.value("manual_semantic_inputs", Json::array())}};
            schema["extraction_windows"] = guided.report.at("windows");
            schema["extraction_observation_files"] = guided.report.at("observation_files");
            std::map<PhysicalBit, u32> native;
            z3::expr_vector native_from(ctx), native_to(ctx), physical_from(ctx), physical_to(ctx);
            for (const auto& [id, bit] : guided.physical_nets)
            {
                if (bit >= 0) require(native.emplace(bit, id).second, "aliased physical HAL net");
                native_from.push_back(ctx.bv_const(("net_" + std::to_string(id) + "_0").c_str(), 1));
                require(bit >= -2, "unknown physical constant in state extraction");
                native_to.push_back(bit >= 0 ? ctx.bv_const(("b" + std::to_string(bit)).c_str(), 1) : ctx.bv_val(bit == -2, 1));
            }
            std::set<PhysicalBit> owned;
            const auto own = [&](PhysicalBit bit, const z3::expr& value) {
                require(bit >= 0 && owned.insert(bit).second, "multiply owned physical state/input bit");
                physical_from.push_back(ctx.bv_const(("b" + std::to_string(bit)).c_str(), 1)); physical_to.push_back(value);
            };
            std::set<PhysicalBit> controls{-1, -2};
            for (const auto& [name, word] : guided.words)
            {
                const auto symbol = "state:" + name; const unsigned width = word.q.size(); require(width > 0, "empty state word");
                const auto variable = ctx.bv_const(symbol.c_str(), width); Integer known = 0;
                std::map<std::string, std::pair<Json, Integer>> blocks;
                for (unsigned bit = 0; bit < width; ++bit)
                {
                    const auto& ff = original.flip_flops.at(word.q[bit]); require(ff.pins.at("D") == word.d.at(bit), "physical FF D correspondence changed");
                    require(guided.physical_nets.at(groups.at("words").at(name).at("q_nets")[bit]) == word.q[bit], "physical word order changed");
                    own(word.q[bit], variable.extract(bit, bit)); if (ff.type != "$dff") known |= Integer(1) << bit;
                    for (const auto* pin : {"C", "E", "R", "S"}) if (ff.pins.count(pin)) controls.insert(ff.pins.at(pin));
                    require(!(ff.pins.count("R") && ff.pins.count("S")), "simultaneous FF reset/set is unsupported");
                    const bool set = ff.pins.count("S"), reset = ff.pins.count("R") || set;
                    Json block{{"clock", control_name(ff.pins.at("C"))}, {"falling", ff.falling},
                        {"enable", control_name(ff.pins.count("E") ? ff.pins.at("E") : -2)},
                        {"reset", control_name(reset ? ff.pins.at(set ? "S" : "R") : -1)}, {"set", set}, {"synchronous", ff.synchronous_reset}};
                    auto& entry = blocks[block.dump()]; entry.first = block; entry.second |= Integer(1) << bit;
                }
                Json rows = Json::array(); for (auto& [key, block] : blocks) { block.first["mask"] = block.second.str(); rows.push_back(block.first); }
                schema["words"][name] = {{"width", width}, {"q_bits", word.q}, {"initial", "0"}, {"initial_known_mask", known.str()},
                    {"numeric_order_known", word.numeric_order_known}, {"blocks", rows}, {"variants", Json::array()}, {"fallback", "d_" + name + "_full"}};
                schema["bindings"][symbol] = {{"kind", "state"}, {"name", name}, {"width", width}};
                controls.insert(word.control_cone.begin(), word.control_cone.end());
            }
            require(owned.size() == original.flip_flops.size(), "state machine does not cover all physical FFs");
            const auto& memories = guided.windows.begin()->second.memories;
            for (const auto& [name, window] : memories)
            {
                const auto& ram = window.model; const auto symbol = "read:" + name; const auto variable = ctx.bv_const(symbol.c_str(), 16);
                std::vector<PhysicalBit> reads;
                for (unsigned bit = 0; bit < ram.pins.at("RDATA").size(); ++bit)
                { const auto physical = guided.physical_nets.at(ram.pins.at("RDATA")[bit]); reads.push_back(physical); own(physical, variable.extract(bit, bit)); }
                require(reads.size() == 16, "invalid event RAM read width");
                const auto read_clock = guided.physical_nets.at(ram.read_clock), write_clock = guided.physical_nets.at(ram.write_clock);
                controls.insert(read_clock); controls.insert(write_clock);
                schema["memories"][name] = {{"initial", ram.initial}, {"read_initial_known", false}, {"rdata_bits", reads},
                    {"read_clock", control_name(read_clock)}, {"write_clock", control_name(write_clock)}, {"ports", Json::object()}};
                schema["bindings"][symbol] = {{"kind", "memory"}, {"name", name}, {"width", 16}};
            }
            for (const auto bit : original.inputs)
            {
                require(bit >= 0, "constant input pad"); const auto name = "pad" + std::to_string(bit); const auto symbol = "pad:" + name;
                own(bit, ctx.bv_const(symbol.c_str(), 1)); schema["pads"][name] = {{"physical", bit}};
                schema["bindings"][symbol] = {{"kind", "pad"}, {"name", name}, {"width", 1}};
            }
            std::map<PhysicalBit, std::pair<PhysicalBit, PhysicalBit>> pad_drivers;
            for (const auto& cell : original.module().at("cells")) if (cell.at("type") == "$mux")
            {
                const auto& pins = cell.at("connections");
                require(pins.at("A") == nlohmann::ordered_json::array({"z"}), "unsupported non-pad mux");
                require(pad_drivers.emplace(physical_bit(pins.at("Y")[0]), std::make_pair(physical_bit(pins.at("B")[0]), physical_bit(pins.at("S")[0]))).second,
                        "multiple pad drivers");
            }
            for (auto port = original.module().at("ports").begin(); port != original.module().at("ports").end(); ++port)
            {
                if (port.value().at("direction") == "input") continue;
                for (unsigned index = 0; index < port.value().at("bits").size(); ++index)
                {
                    const auto bit = physical_bit(port.value().at("bits")[index]);
                    const auto name = port.key() + (port.value().at("bits").size() == 1 ? "" : "_" + std::to_string(index));
                    PhysicalBit data = bit, enable = -2;
                    if (pad_drivers.count(bit)) { data = pad_drivers.at(bit).first; enable = pad_drivers.at(bit).second; }
                    else if (port.value().at("direction") == "inout") { data = -1; enable = -1; }
                    controls.insert(data); controls.insert(enable);
                    schema["outputs"][name] = {{"physical", bit}, {"data", control_name(data)}, {"enable", control_name(enable)}};
                }
            }
            std::vector<bool> bitmap;
            for (const auto* gate : netlist->get_gates()) if (bitmap.size() <= gate->get_id()) bitmap.resize(gate->get_id() + 1, false);
            for (const auto* gate : netlist->get_gates())
            { const auto& type = gate->get_type()->get_name(); bitmap[gate->get_id()] = type == "SB_LUT4" || type == "SB_CARRY" || type == "VCC" || type == "GND"; }
            const auto normalize_native = [&](z3::expr value) { return value.substitute(native_from, native_to).simplify(); };
            const auto pack = [&](z3::expr value) { return value.substitute(physical_from, physical_to).simplify(); };
            unsigned proof_id = 0;
            const auto prove = [&](const std::string& name, const z3::expr& condition, const z3::expr& a, const z3::expr& b) {
                z3::solver solver(ctx); solver.set("timeout", options.program.proof_timeout_ms);
                if (options.program.proof_resource_limit) solver.set("rlimit", options.program.proof_resource_limit);
                solver.add(condition && a != b); const auto file = out / "proofs" / (std::to_string(++proof_id) + ".smt2");
                artifacts::write(file, solver.to_smt2()); const auto status = solver.check();
                if (status == z3::unknown && a.is_bv() && a.get_sort().bv_size() > 1)
                {
                    // Whole-word query UNKNOWN: discharge each bit separately.
                    std::filesystem::remove(file);
                    for (unsigned i = 0; i < a.get_sort().bv_size(); ++i)
                    {
                        z3::solver bit(ctx); bit.set("timeout", options.program.proof_timeout_ms);
                        if (options.program.proof_resource_limit) bit.set("rlimit", options.program.proof_resource_limit);
                        bit.add(condition && a.extract(i, i) != b.extract(i, i));
                        const auto bit_file = out / "proofs" / (std::to_string(++proof_id) + ".smt2"); artifacts::write(bit_file, bit.to_smt2());
                        const auto bit_status = bit.check();
                        require(bit_status == z3::unsat, "state-machine HAL/original equivalence not proved: " + name + " bit " + std::to_string(i) + " " + (bit_status == z3::unknown ? bit.reason_unknown() : "SAT"));
                        schema["proofs"].push_back({{"name", name}, {"bit", i}, {"query", bit_file.string()}, {"sha256", artifacts::file_hash(bit_file)}, {"result", "UNSAT"}});
                    }
                    return;
                }
                require(status == z3::unsat, "state-machine HAL/original equivalence not proved: " + name + " " + (status == z3::unknown ? solver.reason_unknown() : "SAT"));
                schema["proofs"].push_back({{"name", name}, {"query", file.string()}, {"sha256", artifacts::file_hash(file)}, {"result", "UNSAT"}});
            };
            FoldGraph data_graph, control_graph;
            data_graph.available = control_graph.available = true; data_graph.period = control_graph.period = 1;
            const auto add = [&](FoldGraph& graph, const std::string& name, const z3::expr& equation, bool known) {
                const auto packed = pack(equation); const BitVectorProgram program(packed); std::map<std::string, Source> sources;
                for (const auto& [symbol, width] : program.inputs())
                {
                    require(schema.at("bindings").contains(symbol) && schema.at("bindings").at(symbol).at("width") == width,
                            "unclosed physical state boundary: " + symbol);
                    sources.emplace(symbol, Source{SourceKind::input, symbol, 0, 0});
                }
                graph.nodes.push_back({name, 0, packed, std::move(sources), known});
            };
            std::vector<std::vector<Net*>> requests; std::vector<PhysicalBit> control_ids;
            std::map<PhysicalBit, z3::expr> control_values;
            for (const auto bit : controls)
            {
                require(bit >= -2, "unknown constant in event control/output");
                if (bit < 0) control_values.emplace(bit, ctx.bv_val(bit == -2, 1));
                else { auto* net = netlist->get_net_by_id(native.at(bit)); require(net, "missing physical control net"); requests.push_back({net}); control_ids.push_back(bit); }
            }
            if (!requests.empty())
            {
                const auto values = unwrap(sse::get_word_values_at_z3(requests, std::vector<u32>(requests.size(), 0), bitmap, {{}}, {}, false, ctx));
                for (size_t i = 0; i < control_ids.size(); ++i) control_values.emplace(control_ids[i], normalize_native(values.at(i)));
            }
            for (const auto& [bit, value] : control_values)
            {
                prove(control_name(bit), ctx.bool_val(true), value, original.word({bit})); add(control_graph, control_name(bit), value, true);
            }
            requests.clear(); std::vector<std::string> word_names;
            for (const auto& [name, word] : guided.words)
            {
                std::vector<Net*> nets;
                for (const auto physical : word.d)
                {
                    if (physical < 0)
                    {
                        Net* constant = nullptr;
                        for (const auto& [id, bit] : guided.physical_nets) if (bit == physical) { constant = netlist->get_net_by_id(id); break; }
                        require(constant, "missing native constant D net"); nets.push_back(constant);
                    }
                    else nets.push_back(netlist->get_net_by_id(native.at(physical)));
                }
                requests.push_back(std::move(nets)); word_names.push_back(name);
            }
            const auto all_d = unwrap(sse::get_word_values_at_z3(requests, std::vector<u32>(requests.size(), 0), bitmap, {{}}, {}, false, ctx));
            std::map<std::string, z3::expr> full_d;
            for (size_t i = 0; i < word_names.size(); ++i)
            {
                const auto& name = word_names[i]; const auto& word = guided.words.at(name); const auto value = normalize_native(all_d.at(i));
                prove("d_" + name, ctx.bool_val(true), value, original.word(word.d)); full_d.emplace(name, value);
                add(data_graph, "d_" + name + "_full", value, word.numeric_order_known);
            }
            std::map<std::string, std::set<std::string>> seen_variants;
            for (const auto& old_proof : guided.report.at("proofs")) if (old_proof.contains("word") && !old_proof.at("conditions").empty())
            {
                const std::string name = old_proof.at("word"); const auto& word = guided.words.at(name);
                const auto& value = word.equations.at(old_proof.at("time").get<u32>()); const auto key = old_proof.at("conditions").dump() + value.to_string();
                if (!seen_variants[name].insert(key).second) continue;
                Json conditions = Json::array(); auto guard = ctx.bool_val(true);
                for (const auto& condition : old_proof.at("conditions"))
                {
                    const auto bit = physical_bit(condition[0]); const int wanted = condition[1]; require(wanted == 0 || wanted == 1, "unknown specialization guard");
                    guard = guard && control_values.at(bit) == wanted;
                    conditions.push_back({{"control", control_name(bit)}, {"value", wanted}});
                }
                const auto function = "d_" + name + "_v" + std::to_string(schema["words"][name]["variants"].size());
                prove(function, guard, value, full_d.at(name)); add(data_graph, function, value, word.numeric_order_known);
                schema["words"][name]["variants"].push_back({{"function", function}, {"when", conditions}});
            }
            for (const auto& [name, window] : memories) for (const auto& [port, value] : window.model.ports)
            {
                std::vector<PhysicalBit> pins; for (const auto id : window.model.pins.at(port)) pins.push_back(guided.physical_nets.at(id));
                const auto function = "ram_" + name + '_' + port; prove(function, ctx.bool_val(true), value, original.word(pins));
                add(data_graph, function, value, true); schema["memories"][name]["ports"][port] = function;
            }
            ArithmeticRecovery recovery(options.arithmetic);
            const auto arithmetic = unwrap(lift_arithmetic(data_graph, recovery, out / "arithmetic"));
            unwrap(export_program(control_graph, out / "controls", options.program, nullptr, "COMBINATIONAL_FUNCTIONS"));
            unwrap(export_program(data_graph, out / "data", options.program, arithmetic, "COMBINATIONAL_FUNCTIONS"));
            const auto data = unwrap(check_program_artifacts(out / "data")), control = unwrap(check_program_artifacts(out / "controls"));
            schema["FF_bits"] = original.flip_flops.size(); schema["control_functions"] = control_graph.nodes.size(); schema["data_functions"] = data_graph.nodes.size();
            schema["scope"] = "All physical FF words and RAM storage; guarded observed data specializations plus unconditional HAL fallbacks. Control/data cones universally checked. Event/primitive runtime and generated composition trusted/tested; two-state completions, not a four-state or unrestricted timing proof.";
            artifacts::write(out / "state_schema.json", schema.dump(2) + '\n');
            return OK(export_state_driver(out, schema, data, control, options.max_delta_events));
        }
        catch (const std::exception& error) { return ERR(error.what()); }
    }
}
