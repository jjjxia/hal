#include "algorithm_recovery/guided.h"
#include "artifacts.h"
#include "hal_core/netlist/gate.h"
#include "hal_core/netlist/module.h"
#include "hal_core/netlist/net.h"
#include "hal_core/netlist/netlist.h"
#include "sequential_symbolic_execution/sequential_symbolic_execution.h"
#include <iostream>

namespace hal::algorithm_recovery
{
    using Json = nlohmann::json;
    using artifacts::require;
    namespace
    {
        template<class T> T unwrap(hal::Result<T>&& value)
        { if (value.is_error()) throw std::runtime_error(value.get_error().get()); return std::move(value.get()); }
        z3::expr pack(z3::context& ctx, const std::vector<z3::expr>& lsb)
        {
            require(!lsb.empty(), "empty word"); z3::expr_vector msb(ctx);
            for (auto it = lsb.rbegin(); it != lsb.rend(); ++it) msb.push_back(*it);
            return msb.size() == 1 ? msb[0] : z3::concat(msb);
        }
    }
    Result<GuidedAnalysis> analyze_observations(Netlist* nl, const Json& grouped, OriginalCircuit& original,
        const Observations& trace, z3::context& ctx, const std::filesystem::path& output, u32 window_length, const std::vector<std::string>& profiles)
    {
        try
        {
            require(nl && window_length > 0, "missing netlist or empty extraction window");
            require(trace.metadata.at("status") == "ORIGINAL_GATE_TRACE" && grouped.at("status") == "HAL_DANA_BITORDER_EXTRACTED", "unexpected input stage");
            require(trace.metadata.at("files").at(original.source_path) == original.source_sha256, "trace does not bind the original input");
            const auto out = std::filesystem::absolute(output);
            require(!std::filesystem::exists(out) || std::filesystem::is_empty(out), "SSE output directory must be empty");
            std::filesystem::create_directories(out / "proofs");
            GuidedAnalysis result;
            const auto& ports = original.module().at("ports");
            std::map<PhysicalBit, u32> native;
            for (const auto* net : nl->get_nets())
            {
                const auto& name = net->get_name(); PhysicalBit physical;
                if (net->is_gnd_net()) physical = -1;
                else if (net->is_vcc_net()) physical = -2;
                else if (ports.contains(name)) physical = physical_bit(ports.at(name).at("bits")[0]);
                else
                {
                    require(name.size() > 1 && name[0] == 'n' && name.find_first_not_of("0123456789", 1) == std::string::npos, "unmapped native net " + name);
                    physical = std::stoll(name.substr(1));
                }
                result.physical_nets.emplace(net->get_id(), physical);
                if (physical >= 0) require(native.emplace(physical, net->get_id()).second, "aliased native physical net");
            }
            std::set<PhysicalBit> controls = original.controls; controls.insert(original.clocks.begin(), original.clocks.end());
            std::map<std::string, std::vector<u32>> windows; std::set<u32> times;
            const std::set<std::string> selected(profiles.begin(), profiles.end());
            require(selected.size() == profiles.size(), "duplicate analysis profile");
            for (const auto& profile : selected) require(trace.metadata.at("windows").contains(profile), "profile absent from trace: " + profile);
            for (auto it = trace.metadata.at("windows").begin(); it != trace.metadata.at("windows").end(); ++it)
            {
                if (!selected.empty() && !selected.count(it.key())) continue;
                const auto& w = it.value(); require(w.size() >= window_length, "insufficient trace for requested extraction window: " + it.key());
                auto& used = windows[it.key()];
                for (size_t index = w.size() - window_length; index < w.size(); ++index)
                {
                    const u32 t = w[index]; require(t < trace.pre.size() && (used.empty() || t == used.back() + 1), "invalid observation window");
                    used.push_back(t); times.insert(t);
                }
            }
            require(!windows.empty(), "no analysis profiles available");
            // Only new control variants invoke SSE. Materializing a map of all
            // controls for every event wastes memory on long observation windows.
            const auto known_values = [&](u32 t) {
                std::map<u32, bool> values;
                for (auto control : controls)
                {
                    const auto value = trace.value(control, t);
                    if (value >= 0 && native.count(control)) values[native.at(control)] = value != 0;
                }
                return values;
            };
            std::vector<bool> bitmap;
            for (const auto* gate : nl->get_gates()) if (bitmap.size() <= gate->get_id()) bitmap.resize(static_cast<size_t>(gate->get_id()) + 1, false);
            for (const auto* gate : nl->get_gates())
            {
                const auto& name = gate->get_type()->get_name(); bitmap[gate->get_id()] = name == "SB_LUT4" || name == "SB_CARRY" || name == "VCC" || name == "GND";
            }
            z3::expr_vector from(ctx), to(ctx);
            for (const auto& [id, physical] : result.physical_nets)
            {
                from.push_back(ctx.bv_const(("net_" + std::to_string(id) + "_0").c_str(), 1));
                to.push_back(physical >= 0 ? ctx.bv_const(("b" + std::to_string(physical)).c_str(), 1) : ctx.bv_val(physical == -2 ? 1 : 0, 1));
            }
            auto normalized = [&](z3::expr value) { return value.substitute(from, to).simplify(); };
            std::map<PhysicalBit, std::set<PhysicalBit>> cone_cache;
            auto cone = [&](PhysicalBit root) {
                if (!cone_cache.count(root))
                {
                    std::vector<PhysicalBit> pending{root}; std::set<PhysicalBit> seen, found;
                    while (!pending.empty())
                    {
                        auto bit = pending.back(); pending.pop_back(); if (!seen.insert(bit).second) continue;
                        if (controls.count(bit)) { found.insert(bit); continue; }
                        auto deps = original.dependencies(bit); pending.insert(pending.end(), deps.begin(), deps.end());
                    }
                    cone_cache.emplace(root, std::move(found));
                }
                return cone_cache.at(root);
            };
            Json proofs = Json::array(); unsigned calls = 0, cache_hits = 0, ff_count = 0;
            for (auto word = grouped.at("words").begin(); word != grouped.at("words").end(); ++word)
            {
                auto* module = nl->get_module_by_id(word.value().at("module_id")); require(module, "missing DANA module");
                GuidedWord row; row.numeric_order_known = word.value().at("order_origin") != "UNRESOLVED";
                std::vector<u32> dnets = word.value().at("d_nets").get<std::vector<u32>>();
                for (size_t i = 0; i < dnets.size(); ++i)
                {
                    const auto q = result.physical_nets.at(word.value().at("q_nets")[i]);
                    const auto d = result.physical_nets.at(dnets[i]); row.q.push_back(q); row.d.push_back(d);
                    auto* gate = nl->get_gate_by_id(word.value().at("gates")[i]); require(gate, "missing DANA FF");
                    const auto& ff = original.flip_flops.at(q);
                    require(gate->get_type()->get_name() == (ff.type == "$dff" ? "REC_IO_DFF" : ff.type), "FF primitive changed");
                    require(result.physical_nets.at(gate->get_fan_out_net("Q")->get_id()) == q && ff.pins.at("D") == d, "FF output/input correspondence differs");
                    for (const auto& [name, bit] : ff.pins) if (name != "Q")
                        require(gate->get_fan_in_net(name) && result.physical_nets.at(gate->get_fan_in_net(name)->get_id()) == bit, "FF control/data wiring differs");
                }
                ff_count += row.q.size();
                std::map<u32, std::pair<PinGroup<ModulePin>*, unsigned>> net_to_group;
                for (auto* group : module->get_pin_groups())
                {
                    unsigned i = 0; for (const auto* pin : group->get_pins()) net_to_group[pin->get_net()->get_id()] = {group, i++};
                }
                std::map<u32, PinGroup<ModulePin>*> pin_groups;
                for (auto d : dnets) { auto* group = net_to_group.at(d).first; pin_groups.emplace(group->get_id(), group); }
                std::set<PhysicalBit> all_controls;
                std::vector<std::pair<Module*, PinGroup<ModulePin>*>> targets;
                for (auto [id, group] : pin_groups)
                {
                    targets.emplace_back(module, group);
                    for (const auto* pin : group->get_pins()) { auto found = cone(result.physical_nets.at(pin->get_net()->get_id())); all_controls.insert(found.begin(), found.end()); }
                }
                std::vector<PhysicalBit> ordered_controls(all_controls.begin(), all_controls.end());
                std::sort(ordered_controls.begin(), ordered_controls.end(), [](auto a, auto b) { return std::to_string(a) < std::to_string(b); });
                row.control_cone = ordered_controls;
                std::map<std::vector<int>, z3::expr> variants;
                for (auto t : times)
                {
                    std::vector<int> key; for (auto c : ordered_controls) key.push_back(trace.value(c, t));
                    if (!variants.count(key))
                    {
                        // Every included gate is combinational; FF/RAM endpoints
                        // are cut. Use one normalized snapshot time (0), retaining
                        // the real observation index and conditions in the proof.
                        auto values = unwrap(sse::get_word_values_at_z3(targets, std::vector<u32>(targets.size(), 0), bitmap, {known_values(t)}, {}, false, ctx));
                        ++calls; std::map<u32, z3::expr> terms;
                        for (size_t i = 0; i < targets.size(); ++i) terms.emplace(targets[i].second->get_id(), normalized(values.at(i)));
                        std::vector<z3::expr> bits;
                        for (auto d : dnets) { const auto [group, index] = net_to_group.at(d); bits.push_back(terms.at(group->get_id()).extract(index, index)); }
                        auto expression = pack(ctx, bits).simplify(); z3::expr_vector hypotheses(ctx), obligations(ctx);
                        Json conditions = Json::array();
                        for (size_t i = 0; i < key.size(); ++i) if (key[i] >= 0)
                        { hypotheses.push_back(original.function(ordered_controls[i]) == ctx.bool_val(key[i] != 0)); conditions.push_back({bit_json(ordered_controls[i]), key[i]}); }
                        for (unsigned i = 0; i < row.d.size(); ++i) obligations.push_back((expression.extract(i, i) == 1) == original.function(row.d[i]));
                        // Keep unconditional proof receipts explicit as true.
                        const auto condition = hypotheses.empty() ? ctx.bool_val(true) : z3::mk_and(hypotheses);
                        z3::solver proof(ctx); proof.set("timeout", 60000u);
                        proof.add(condition); proof.add(!z3::mk_and(obligations));
                        const auto file = out / "proofs" / ("sse_" + std::to_string(calls) + ".smt2"); artifacts::write(file, proof.to_smt2());
                        require(proof.check() == z3::unsat, "conditional original-LUT equivalence not proven: " + word.key());
                        z3::solver sanity(ctx); sanity.set("timeout", 60000u); sanity.add(condition);
                        require(sanity.check() == z3::sat, "vacuous or UNKNOWN control assumptions");
                        proofs.push_back({{"word", word.key()}, {"time", t}, {"conditions", conditions}, {"query", file.string()}, {"sha256", artifacts::file_hash(file)}, {"result", "UNSAT"}, {"condition_satisfiable", true}});
                        variants.emplace(key, expression);
                    }
                    else ++cache_hits;
                    row.equations.emplace(t, variants.at(key));
                }
                for (size_t t = 0; t < trace.pre.size(); ++t)
                {
                    std::vector<FFUpdate> updates; for (auto q : row.q) updates.push_back(ff_update(original.flip_flops.at(q), trace, t));
                    row.updates.push_back(std::move(updates));
                }
                result.words.emplace(word.key(), std::move(row));
                if (result.words.size() % 25 == 0) std::cout << "SSE " << result.words.size() << '/' << grouped.at("words").size() << " calls=" << calls << std::endl;
            }
            FoldInput base;
            for (auto pad : original.inputs) base.inputs.emplace("pad" + std::to_string(pad), std::vector<std::string>{"b" + std::to_string(pad)});
            auto memories = unwrap(extract_ice40_rams(nl, ctx)); unsigned ram_proofs = 0;
            for (auto& memory : memories)
            {
                MemoryWindow window;
                for (auto id : memory.pins.at("RDATA")) window.read_bits.push_back("b" + std::to_string(result.physical_nets.at(id)));
                for (auto& [port, expression] : memory.ports)
                {
                    expression = normalized(expression); std::vector<PhysicalBit> pins;
                    for (auto id : memory.pins.at(port)) pins.push_back(result.physical_nets.at(id));
                    z3::solver proof(ctx); proof.set("timeout", 60000u); proof.add(expression != original.word(pins));
                    const auto file = out / "proofs" / ("ram_" + memory.name + '_' + port + ".smt2"); artifacts::write(file, proof.to_smt2());
                    require(proof.check() == z3::unsat, "unconditional RAM port equivalence not proven"); ++ram_proofs;
                    proofs.push_back({{"memory", memory.name}, {"port", port}, {"query", file.string()}, {"sha256", artifacts::file_hash(file)}, {"result", "UNSAT"}});
                }
                const auto name = memory.name; window.model = std::move(memory); base.memories.emplace(name, std::move(window));
            }
            std::map<std::pair<unsigned, std::vector<int>>, z3::expr> effective;
            for (const auto& [profile, times] : windows)
            {
                auto input = base; input.start = times.front(); input.length = times.size();
                for (const auto& [name, row] : result.words)
                {
                    WordWindow word; word.numeric_order_known = row.numeric_order_known;
                    for (auto q : row.q) word.bits.push_back("b" + std::to_string(q));
                    for (auto t : times)
                    {
                        const auto& updates = row.updates.at(t);
                        const auto full = std::all_of(updates.begin(), updates.end(), [](const auto& u) { return u.write == Write::full; });
                        const auto hold = std::all_of(updates.begin(), updates.end(), [](const auto& u) { return u.write == Write::hold; });
                        word.writes.push_back(full ? Write::full : hold ? Write::hold : Write::mixed_or_unknown);
                        std::vector<int> reset; for (const auto& u : updates) reset.push_back(u.reset_value);
                        const auto& raw = row.equations.at(t); const auto key = std::make_pair(raw.id(), reset);
                        if (!effective.count(key))
                        {
                            std::vector<z3::expr> bits;
                            for (unsigned i = 0; i < reset.size(); ++i) bits.push_back(reset[i] >= 0 ? ctx.bv_val(reset[i], 1) : raw.extract(i, i));
                            effective.emplace(key, pack(ctx, bits).simplify());
                        }
                        word.equations.push_back(effective.at(key));
                    }
                    input.words.emplace(name, std::move(word));
                }
                result.windows.emplace(profile, std::move(input));
            }
            result.report = {{"status", "HAL_GUIDED_EQUATIONS_ORIGINAL_LUT_CHECKED"}, {"groups", result.words.size()}, {"FF_bits", ff_count},
                {"HAL_calls", calls}, {"cache_hits", cache_hits}, {"RAM_instances", base.memories.size()}, {"RAM_port_proofs", ram_proofs}, {"windows", windows}, {"proofs", proofs},
                {"observation_files", trace.metadata.at("files")}, {"window_length", window_length},
                {"manual_semantic_inputs", trace.metadata.value("manual_semantic_inputs", Json::array())}, {"scope", "Observed primitive-control conditions, nonvacuous universal original-LUT checks; RAM ports unconditional. No global schedule/whole-device claim."}};
            artifacts::write(out / "sse_report.json", result.report.dump(2) + '\n');
            return OK(std::move(result));
        }
        catch (const std::exception& error) { return ERR(error.what()); }
    }
}
