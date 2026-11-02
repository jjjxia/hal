#include "algorithm_recovery/taint.h"
#include "artifacts.h"
#include <functional>
#include <iostream>

namespace hal::algorithm_recovery
{
    using Json = nlohmann::json;
    using artifacts::require;

    PadProtocol PadProtocol::load(const std::filesystem::path& file, const OriginalCircuit& original)
    {
        PadProtocol result; const auto text = artifacts::read(file);
        result.path = std::filesystem::absolute(file).string(); result.sha256 = artifacts::sha256(text);
        const auto json = Json::parse(text);
        require(json.at("schema") == "PAD_PROTOCOL_V1", "unsupported pad protocol schema");
        require(json.at("source_sha256") == original.source_sha256, "pad protocol is bound to a different netlist");
        result.description = json.at("description");
        for (const auto& bit : json.at("non_data_inputs"))
        {
            const auto physical = physical_bit(bit);
            require(original.inputs.count(physical), "protocol pad is not a physical input");
            require(result.non_data.insert(physical).second, "duplicate protocol pad");
        }
        for (const auto clock : original.clocks)
            if (original.inputs.count(clock)) require(result.non_data.count(clock), "a primary clock pad must be a non-data protocol input");
        return result;
    }

    bool ForcedTaint::applies(PhysicalBit bit, size_t t) const
    {
        const auto it = events.find(bit);
        if (it != events.end() && it->second.count(t)) return true;
        for (auto rule = periodic.lower_bound({bit, 0, 0, 0}); rule != periodic.end() && std::get<0>(*rule) == bit; ++rule)
        {
            const auto [b, origin, period, phase] = *rule;
            if (t >= origin && (t - origin) % period == phase) return true;
        }
        return false;
    }
    size_t ForcedTaint::size() const
    {
        size_t n = periodic.size();
        for (const auto& [bit, times] : events) n += times.size();
        return n;
    }

    bool TaintTrace::is_tainted(PhysicalBit bit, size_t t) const
    {
        if (bit == -1 || bit == -2) return false;
        if (bit < 0) return true;
        return tainted.at(t).at(column.at(bit));
    }
    int TaintTrace::known(PhysicalBit bit, size_t t) const
    {
        if (bit == -1) return 0;
        if (bit == -2) return 1;
        if (bit < 0 || is_tainted(bit, t)) return -1;
        return value.at(t).at(column.at(bit)) ? 1 : 0;
    }

    namespace
    {
        struct Cell { bool carry; std::vector<PhysicalBit> in; uint16_t table; PhysicalBit out; std::vector<bool> used; };
        struct FF { PhysicalBit q, c, d, e, r; bool async, set, falling, io; };
        struct Ram { std::string name; PhysicalBit rclk, rclke, re, wclk, wclke, we; std::vector<PhysicalBit> raddr, waddr, wdata, mask, rdata; };
        // Values a function can take when the tainted operands vary freely.
        template<class F> std::pair<bool, bool> outcomes(const std::vector<std::pair<bool, bool>>& operands, F function)
        {
            std::vector<unsigned> free; for (unsigned i = 0; i < operands.size(); ++i) if (operands[i].second) free.push_back(i);
            bool zero = false, one = false; std::vector<bool> v(operands.size());
            for (unsigned i = 0; i < operands.size(); ++i) v[i] = operands[i].first;
            for (unsigned a = 0; a < (1u << free.size()); ++a)
            {
                for (unsigned k = 0; k < free.size(); ++k) v[free[k]] = (a >> k) & 1;
                (function(v) ? one : zero) = true;
                if (zero && one) break;
            }
            return {zero, one};
        }
    }

    Result<TaintTrace> compute_taint(OriginalCircuit& original, const Observations& trace, const PadProtocol& protocol, const ForcedTaint& forced)
    {
        try
        {
            TaintTrace result; const auto events = trace.pre.size();
            for (const auto bit : original.leaves()) result.column.emplace(bit, result.column.size());
            std::vector<Cell> cells; std::map<PhysicalBit, size_t> cell_of;
            for (const auto& [out, cell] : original.combinational_cells())
            {
                Cell c; c.out = out; c.carry = cell.at("type") == "SB_CARRY"; c.table = 0; const auto& pins = cell.at("connections");
                if (c.carry) for (const auto* name : {"I0", "I1", "CI"}) c.in.push_back(physical_bit(pins.at(name)[0]));
                else
                {
                    for (unsigned i = 0; i < 4; ++i) c.in.push_back(physical_bit(pins.at("I" + std::to_string(i))[0]));
                    c.table = static_cast<uint16_t>(std::stoul(cell.at("parameters").at("LUT_INIT").get<std::string>(), nullptr, 2));
                }
                // A LUT input the truth table ignores is neither an ordering
                // dependency nor an information-flow source.
                for (unsigned i = 0; i < c.in.size(); ++i)
                {
                    bool used = c.carry;
                    if (!c.carry) for (unsigned k = 0; k < 16 && !used; ++k) used = ((c.table >> k) ^ (c.table >> (k ^ (1u << i)))) & 1;
                    c.used.push_back(used);
                }
                cell_of.emplace(out, cells.size()); cells.push_back(std::move(c));
            }
            std::vector<size_t> order; std::vector<uint8_t> mark(cells.size(), 0);
            for (size_t root = 0; root < cells.size(); ++root)
            {
                if (mark[root]) continue;
                std::vector<std::pair<size_t, size_t>> stack{{root, 0}}; mark[root] = 1;
                while (!stack.empty())
                {
                    auto& [index, next] = stack.back();
                    if (next < cells[index].in.size())
                    {
                        const auto i = next++; if (!cells[index].used[i]) continue;
                        const auto dep = cells[index].in[i]; const auto it = cell_of.find(dep);
                        if (it == cell_of.end()) continue;
                        require(mark[it->second] != 1, "combinational feedback in original netlist");
                        if (!mark[it->second]) { mark[it->second] = 1; stack.push_back({it->second, 0}); }
                    }
                    else { mark[index] = 2; order.push_back(index); stack.pop_back(); }
                }
            }
            for (const auto index : order) result.column.emplace(cells[index].out, result.column.size());
            std::vector<FF> ffs;
            for (const auto& [q, ff] : original.flip_flops)
            {
                FF f{q, ff.pins.at("C"), ff.pins.at("D"), ff.pins.count("E") ? ff.pins.at("E") : -2,
                     ff.pins.count("R") ? ff.pins.at("R") : ff.pins.count("S") ? ff.pins.at("S") : -1,
                     !ff.synchronous_reset && (ff.pins.count("R") || ff.pins.count("S")), ff.pins.count("S") != 0, ff.falling, ff.type == "$dff"};
                ffs.push_back(f);
            }
            std::vector<Ram> rams;
            for (auto cell = original.module().at("cells").begin(); cell != original.module().at("cells").end(); ++cell)
                if (cell.value().at("type") == "SB_RAM40_4K")
                {
                    const auto& pins = cell.value().at("connections"); Ram r; r.name = cell.key();
                    const auto one = [&](const char* name) { return physical_bit(pins.at(name)[0]); };
                    const auto many = [&](const char* name) { std::vector<PhysicalBit> v; for (const auto& b : pins.at(name)) v.push_back(physical_bit(b)); return v; };
                    r.rclk = one("RCLK"); r.rclke = one("RCLKE"); r.re = one("RE"); r.wclk = one("WCLK"); r.wclke = one("WCLKE"); r.we = one("WE");
                    r.raddr = many("RADDR"); r.waddr = many("WADDR"); r.wdata = many("WDATA"); r.mask = many("MASK"); r.rdata = many("RDATA");
                    rams.push_back(std::move(r));
                }
            std::map<PhysicalBit, size_t> ram_of_read;
            for (size_t i = 0; i < rams.size(); ++i) for (const auto bit : rams[i].rdata) ram_of_read.emplace(bit, i);

            const auto columns = result.column.size();
            std::vector<bool> state(ffs.size()), latch(rams.size(), true), contents(rams.size(), false);
            for (size_t i = 0; i < ffs.size(); ++i) state[i] = ffs[i].io; // unknown power-up value
            std::map<PhysicalBit, size_t> ff_of; for (size_t i = 0; i < ffs.size(); ++i) ff_of.emplace(ffs[i].q, i);
            uint64_t mismatches = 0, comb_checked = 0, next_checked = 0, async_fixpoint_rounds = 0;
            std::vector<bool> val(columns), tnt(columns);
            const auto get = [&](PhysicalBit bit) -> std::pair<bool, bool> {
                if (bit == -1) return {false, false};
                if (bit == -2) return {true, false};
                if (bit < 0) return {false, true};
                const auto c = result.column.at(bit); return {val[c], tnt[c]};
            };
            result.tainted.reserve(events); result.value.reserve(events);
            std::vector<bool> leaf_override(ffs.size(), false), override_value(ffs.size(), false);
            for (size_t t = 0; t < events; ++t)
            {
                // Leaves: pads, stored FF outputs and RAM read latches.
                for (const auto bit : original.leaves())
                {
                    const auto c = result.column.at(bit); const auto observed = trace.value(bit, t);
                    bool taint;
                    if (original.inputs.count(bit)) taint = !protocol.non_data.count(bit);
                    else if (ff_of.count(bit)) taint = state[ff_of.at(bit)];
                    else taint = latch[ram_of_read.at(bit)];
                    if (observed < 0) taint = true;
                    if (forced.applies(bit, t)) taint = true;
                    val[c] = observed > 0; tnt[c] = taint;
                }
                for (unsigned round = 0;; ++round)
                {
                    require(round < 64, "asynchronous control taint did not stabilize");
                    for (const auto index : order)
                    {
                        const auto& cell = cells[index]; std::vector<std::pair<bool, bool>> ops;
                        for (size_t i = 0; i < cell.in.size(); ++i) ops.push_back(cell.used[i] ? get(cell.in[i]) : std::make_pair(false, false));
                        const auto evaluate = [&](const std::vector<bool>& v) {
                            if (cell.carry) return (v[0] && v[1]) || (v[0] && v[2]) || (v[1] && v[2]);
                            return bool((cell.table >> (v[0] | (v[1] << 1) | (v[2] << 2) | (v[3] << 3))) & 1);
                        };
                        std::vector<bool> actual; for (const auto& op : ops) actual.push_back(op.first);
                        const auto [zero, one] = outcomes(ops, evaluate);
                        const auto c = result.column.at(cell.out);
                        val[c] = evaluate(actual); tnt[c] = (zero && one) || forced.applies(cell.out, t);
                    }
                    bool changed = false;
                    for (size_t i = 0; i < ffs.size(); ++i) if (ffs[i].async)
                    {
                        const auto [rv, rt] = get(ffs[i].r); const auto c = result.column.at(ffs[i].q);
                        if (rt && !tnt[c]) { tnt[c] = true; changed = true; }
                        else if (!rt && rv && tnt[c] && !forced.applies(ffs[i].q, t))
                        { tnt[c] = false; val[c] = ffs[i].set; changed = true; }
                    }
                    if (!changed) break;
                    ++async_fixpoint_rounds;
                }
                for (const auto& [bit, c] : result.column)
                {
                    if (!original.combinational(bit) || tnt[c] || !trace.positions.count(bit)) continue;
                    const auto observed = trace.value(bit, t); if (observed < 0) continue;
                    ++comb_checked; if (bool(observed) != val[c]) ++mismatches;
                }
                result.tainted.push_back(tnt); result.value.push_back(val);
                // Synchronous successor taint for the next pre-edge frame.
                std::vector<bool> next(ffs.size());
                for (size_t i = 0; i < ffs.size(); ++i)
                {
                    const auto& f = ffs[i]; const auto [cv, ct] = get(f.c);
                    const int before = trace.value(f.c, t), after = trace.value(f.c, t, true);
                    if (ct || before < 0 || after < 0) { next[i] = true; continue; }
                    const bool edge = f.falling ? (before == 1 && after == 0) : (before == 0 && after == 1);
                    const std::vector<std::pair<bool, bool>> ops{get(f.d), get(f.e), get(f.r), get(f.q)};
                    const auto function = [&](const std::vector<bool>& v) {
                        const bool d = v[0], e = v[1], r = v[2], q = v[3];
                        if (f.async && r) return f.set;
                        if (!edge) return q;
                        if (!e) return q;
                        if (!f.async && f.r >= 0 && r) return f.set;
                        return d;
                    };
                    const auto [zero, one] = outcomes(ops, function);
                    next[i] = zero && one;
                    if (!next[i])
                    {
                        const auto observed = trace.value(f.q, t, true);
                        if (observed >= 0) { ++next_checked; if (bool(observed) != one) ++mismatches; }
                    }
                }
                state = next;
                for (size_t i = 0; i < rams.size(); ++i)
                {
                    const auto& r = rams[i];
                    const auto any = [&](const std::vector<PhysicalBit>& bits) { for (const auto b : bits) if (get(b).second) return true; return false; };
                    const auto edge_of = [&](PhysicalBit clock) -> int {
                        if (get(clock).second) return -1;
                        const int before = trace.value(clock, t), after = trace.value(clock, t, true);
                        if (before < 0 || after < 0) return -1;
                        return before == 0 && after == 1;
                    };
                    const auto enabled = [&](PhysicalBit a, PhysicalBit b) -> int {
                        const auto [av, at] = get(a); const auto [bv, bt] = get(b);
                        if ((!at && !av) || (!bt && !bv)) return 0;
                        return at || bt ? -1 : 1;
                    };
                    const auto read_edge = edge_of(r.rclk), write_edge = edge_of(r.wclk);
                    if (read_edge != 0)
                    {
                        const auto en = enabled(r.re, r.rclke);
                        if (read_edge < 0 || en < 0) latch[i] = true;
                        else if (en == 1) latch[i] = contents[i] || any(r.raddr);
                    }
                    if (write_edge != 0)
                    {
                        const auto en = enabled(r.we, r.wclke);
                        if (write_edge < 0 || en < 0) contents[i] = true;
                        else if (en == 1 && (any(r.waddr) || any(r.wdata) || any(r.mask))) contents[i] = true;
                    }
                }
            }
            require(mismatches == 0, "taint evaluator disagrees with original-gate observations (" + std::to_string(mismatches) + ")");
            uint64_t tainted_ff = 0, total_ff = 0, tainted_controls = 0, total_controls = 0;
            for (size_t t = 0; t < events; ++t)
            {
                for (const auto& f : ffs) { ++total_ff; tainted_ff += result.is_tainted(f.q, t); }
                for (const auto bit : original.controls) if (bit >= 0) { ++total_controls; tainted_controls += result.is_tainted(bit, t); }
            }
            result.report = {{"status", "CELL_EXACT_GATE_TAINT"}, {"events", events}, {"columns", columns},
                {"protocol", {{"path", protocol.path}, {"sha256", protocol.sha256}, {"non_data_inputs", protocol.non_data}, {"description", protocol.description}}},
                {"untainted_ff_observations", total_ff - tainted_ff}, {"ff_observations", total_ff},
                {"untainted_control_observations", total_controls - tainted_controls}, {"control_observations", total_controls},
                {"checked_combinational_values", comb_checked}, {"checked_ff_successors", next_checked}, {"async_fixpoint_rounds", async_fixpoint_rounds},
                {"forced_taints", forced.size()}, {"ram_rule", "sticky contents taint per RAM; read latch tainted by contents or address/enables"},
                {"scope", "Taint over one recorded run. Lemma 1 premise: shared configuration state; unknown power-up/read-latch values are tainted."}};
            return OK(std::move(result));
        }
        catch (const std::exception& error) { return ERR(error.what()); }
    }
}
