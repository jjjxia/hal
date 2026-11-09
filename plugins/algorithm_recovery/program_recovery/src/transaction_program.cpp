#include "transaction_program.h"
#include <algorithm>
#include <numeric>
#include <set>

namespace program_recovery
{
    static const unsigned MIN_ITERATIONS = 3;

    std::string pyname(const std::string& sym)
    {
        std::string s = sym;
        for (auto& c : s) if (c == ':' || c == '-') c = '_';
        return s;
    }

    Py key_py(const Key& k) { return Py::tuple({py(k.first), py(k.second)}); }
    Py state_key_py(const StateKey& k) { return Py::tuple({py(std::get<0>(k)), py(std::get<1>(k)), py(std::get<2>(k))}); }

    ProgramBuilder::ProgramBuilder(EventGraph& model) : M(model)
    {
        build_flat();
        build_rollup();
    }

    std::string ProgramBuilder::source_code(const Node& node, const std::string& sym, bool tolerant) const
    {
        const Source& s = node.sources.at(sym);
        const std::string base = partition_colon(sym).second;
        if (tolerant)
            return s.delay == 0 ? "v.get(" + tup(base, s.phase).repr() + ", 0)" : "state.get(" + tup(base, s.phase, s.delay).repr() + ", 0)";
        return s.delay == 0 ? "v[" + tup(base, s.phase).repr() + "]" : "state[" + tup(base, s.phase, s.delay).repr() + "]";
    }

    std::vector<std::string> ProgramBuilder::emit_flat_node(const Key& key) const
    {
        const auto& [name, ph] = key;
        const Node& node = M.nodes.at(key);
        Printer pr([&, ph = ph](const std::string& sym) -> std::string {
            const auto [kind, base] = partition_colon(sym);
            if (kind == "word") return source_code(node, sym);
            if (kind == "input") return "pads[" + tup(base, ph).repr() + "]";
            throw std::runtime_error("KeyError " + sym);
        });
        const std::string code = pr.emit(node.expr);
        auto out = pr.lines;
        out.push_back("    v[" + key_py(key).repr() + "] = " + code);
        return out;
    }

    void ProgramBuilder::build_flat()
    {
        const Json& cert = M.G.at("certificate");
        header = {"# Recovered untimed transaction program (generated; do not edit).",
                  "# case=" + M.case_name + "; graph period=" + std::to_string(M.P) + " events; certified origin=" +
                      std::to_string(cert.at("origin").get<long long>()) + "; one call = one protocol transaction",
                  "def sgn(x, w):", "    return x - (1 << w) if (x >> (w - 1)) & 1 else x",
                  "def sra(x, s, w):", "    return (sgn(x, w) >> min(s, w)) & ((1 << w) - 1)", "",
                  "def route(x, pairs):  # bit k of x -> bit o, for (o, k) in pairs",
                  "    return sum(((x >> k) & 1) << o for o, k in pairs)",
                  "def pins(*pairs):  # 1-bit values placed at bit o, for (o, v) in pairs",
                  "    return sum((v & 1) << o for o, v in pairs)",
                  "def bits(x, *idx):  # gather: bit i of the result = bit idx[i] of x (None: constant 0)",
                  "    return sum(((x >> k) & 1) << i for i, k in enumerate(idx) if k is not None)",
                  "def pick(*vk):  # gather across values: bit i = bit k_i of v_i, for v_i, k_i in pairs",
                  "    return sum(((v >> k) & 1) << i for i, (v, k) in enumerate(zip(vk[::2], vk[1::2])) if k is not None)",
                  "def place(y, *idx):  # scatter: bit i of y -> bit idx[i]",
                  "    return sum(((y >> i) & 1) << k for i, k in enumerate(idx))",
                  "def padword(pads, ph, names):  # word whose bit i is data pad names[i] at phase ph",
                  "    return sum((pads.get((n, ph), 0) & 1) << i for i, n in enumerate(names))", "",
                  "def transaction(pads, state):",
                  "    \"\"\"pads[(pad, phase)] -> bit for data pads; state[(word, phase, delay)] -> loop-carried value.\"\"\"",
                  "    v = {}"};
        std::vector<std::string> lines = header;
        for (const auto& key : M.order)
        {
            const auto l = emit_flat_node(key);
            lines.insert(lines.end(), l.begin(), l.end());
        }
        const size_t body_start = lines.size();
        lines.push_back("    outputs = {}");
        for (const auto& ob : M.observations)
        {
            const long long ph = ob.phase;
            Printer pr([&](const std::string& sym) -> std::string {
                const auto [kind, base] = partition_colon(sym);
                if (kind == "word")
                {
                    const auto [p2, d] = M.last_write(base, ph);
                    return d == 0 ? "v[" + tup(base, p2).repr() + "]" : "state[" + tup(base, p2, d).repr() + "]";
                }
                if (kind == "input") return "pads[" + tup(base, ph).repr() + "]";
                throw std::runtime_error("KeyError " + sym);
            });
            const std::string code = pr.emit(ob.expr);
            lines.insert(lines.end(), pr.lines.begin(), pr.lines.end());
            lines.push_back("    outputs[" + tup(ob.port, ob.phase).repr() + "] = " + code);
        }
        state_keys.assign(M.state_reads.begin(), M.state_reads.end());
        lines.push_back("    new_state = {}");
        for (const auto& [name, ph, d] : state_keys)
        {
            const std::string src = d == 1 ? "v[" + tup(name, ph).repr() + "]" : "state[" + tup(name, ph, d - 1).repr() + "]";
            lines.push_back("    new_state[" + tup(name, ph, d).repr() + "] = " + src);
        }
        lines.push_back("    return outputs, new_state");
        tail.assign(lines.begin() + static_cast<long>(body_start), lines.end());
        program_flat = join(lines, "\n") + "\n";
    }

    std::vector<std::pair<std::string, z3::expr>> ProgramBuilder::raw_symbols(const std::string& name, long long ph) const
    {
        std::map<std::string, z3::expr> by;
        for (const auto& v : z3_vars(M.nodes.at({name, ph}).raw)) by.insert_or_assign(sym_name(v), v);
        return {by.begin(), by.end()};
    }

    bool ProgramBuilder::data_symbol(const std::string& sym, long long ph) const
    {
        return M.known_pattern(sym, ph).find('x') != std::string::npos;
    }

    bool ProgramBuilder::compatible(long long prev, long long cur, long long first) const
    {
        const auto& cur_names = steps.at(cur);
        const auto& first_names = steps.at(first);
        const std::set<std::string> carried(first_names.begin(), first_names.end());
        for (const auto& n : cur_names) if (!carried.count(n)) return false;
        for (const auto& name : first_names)
        {
            if (!M.nodes.count({name, cur})) return false;
            if (!M.live.count({name, cur}))
            {
                for (const auto& [sym, _] : raw_symbols(name, cur))
                {
                    if (sym.rfind("word:", 0) == 0 && data_symbol(sym, cur))
                    {
                        const Source& src = M.nodes.at({name, cur}).sources.at(sym);
                        const std::string base = partition_colon(sym).second;
                        if (src.delay != 0 || (!carried.count(base) && !M.live.count({src.name, src.phase}))) return false;
                    }
                }
            }
            if (M.nodes.at({name, cur}).raw_sha256 != M.nodes.at({name, first}).raw_sha256) return false;
        }
        for (const auto& name : first_names)
        {
            for (const auto& [sym, _] : raw_symbols(name, cur))
            {
                if (sym.rfind("word:", 0) != 0) continue;
                if (data_symbol(sym, cur) != data_symbol(sym, first)) return false;
                if (!data_symbol(sym, cur)) continue;
                const Source& s = M.nodes.at({name, cur}).sources.at(sym);
                const std::string base = partition_colon(sym).second;
                if (carried.count(base))
                {
                    if (!(s.name == base && s.phase == prev && s.delay == 0)) return false;
                }
                else
                {
                    const Source& f = M.nodes.at({name, first}).sources.at(sym);
                    if (!(s.name == f.name && s.phase == f.phase && s.delay == f.delay)) return false;
                }
            }
        }
        return true;
    }

    bool ProgramBuilder::first_step_ok(long long first) const
    {
        const auto& names = steps.at(first);
        const std::set<std::string> carried(names.begin(), names.end());
        std::map<std::string, std::pair<long long, long long>> seen;
        for (const auto& name : names)
        {
            for (const auto& [sym, _] : raw_symbols(name, first))
            {
                const std::string base = partition_colon(sym).second;
                if (sym.rfind("word:", 0) == 0 && carried.count(base) && data_symbol(sym, first))
                {
                    const Source& s = M.nodes.at({name, first}).sources.at(sym);
                    const auto [it, inserted] = seen.emplace(base, std::make_pair(s.phase, s.delay));
                    if (it->second != std::make_pair(s.phase, s.delay)) return false;
                }
            }
        }
        return true;
    }

    std::optional<Counter> ProgramBuilder::find_counter(const std::vector<std::pair<std::string, unsigned>>& iter_vars,
                                                    const std::vector<std::map<std::string, BigInt>>& table) const
    {
        std::vector<std::pair<std::string, unsigned>> bits;
        for (const auto& [sym, w] : iter_vars) for (unsigned k = 0; k < w; ++k) bits.emplace_back(sym, k);
        if (!(1 <= bits.size() && bits.size() <= 8) || table.size() < 3) return std::nullopt;
        std::vector<std::vector<int>> rows;
        for (const auto& row : table)
        {
            std::vector<int> r;
            for (const auto& [sym, k] : bits) r.push_back(static_cast<int>((row.at(sym) >> k) & 1));
            rows.push_back(r);
        }
        const unsigned W = static_cast<unsigned>(bits.size());
        const BigInt mod = BigInt(1) << W;
        auto pymod = [&](BigInt x) { x %= mod; if (x < 0) x += mod; return x; };
        std::vector<unsigned> perm(W);
        std::iota(perm.begin(), perm.end(), 0u);
        do
        {
            std::vector<BigInt> seq;
            for (const auto& r : rows)
            {
                BigInt s = 0;
                for (unsigned q = 0; q < W; ++q) s += BigInt(r[perm[q]]) << q;
                seq.push_back(s);
            }
            const BigInt b = pymod(seq[1] - seq[0]);
            bool ok = b != 0;
            for (size_t t = 0; ok && t < seq.size(); ++t) ok = pymod(seq[t] - seq[0] - b * BigInt(t)) == 0;
            if (ok)
            {
                Counter c{seq[0], b, W, {}};
                for (unsigned q = 0; q < W; ++q) c.bits.push_back(bits[perm[q]]);
                return c;
            }
        } while (std::next_permutation(perm.begin(), perm.end()));
        return std::nullopt;
    }

    std::vector<std::vector<long long>> ProgramBuilder::split_run(const std::vector<long long>& run) const
    {
        // iteration_constants(run)
        const long long first = run[0];
        std::map<std::string, z3::expr> syms;
        for (const auto& name : steps.at(first))
            for (const auto& [sym, var] : raw_symbols(name, first)) syms.insert_or_assign(sym, var);
        std::vector<std::pair<std::string, unsigned>> iv;
        for (const auto& [sym, var] : syms)
        {
            std::vector<std::string> pats;
            for (long long ph : run) pats.push_back(M.known_pattern(sym, ph));
            bool varies = false, known = true;
            for (const auto& pt : pats) { varies |= pt != pats[0]; known &= pt.find('x') == std::string::npos; }
            if (varies && known) iv.emplace_back(sym, var.get_sort().bv_size());
        }
        std::vector<std::map<std::string, BigInt>> table;
        for (long long ph : run)
        {
            std::map<std::string, BigInt> row;
            for (const auto& [sym, _] : iv)
            {
                const std::string& pt = M.known_pattern(sym, ph);
                row[sym] = from_binary(std::string(pt.rbegin(), pt.rend()));
            }
            table.push_back(row);
        }
        if (iv.empty() || find_counter(iv, table).has_value()) return {run};
        std::vector<std::string> names = steps.at(run[0]);
        std::sort(names.begin(), names.end());
        std::vector<std::vector<unsigned>> sig;
        for (long long ph : run)
        {
            std::vector<unsigned> s;
            for (const auto& nm : names) s.push_back(M.nodes.at({nm, ph}).expr.id());
            sig.push_back(s);
        }
        std::vector<std::vector<long long>> blocks;
        size_t start = 0;
        for (size_t k = 1; k <= run.size(); ++k)
        {
            if (k == run.size() || sig[k] != sig[start])
            {
                blocks.emplace_back(run.begin() + static_cast<long>(start), run.begin() + static_cast<long>(k));
                start = k;
            }
        }
        if (blocks.size() <= std::max<size_t>(4, run.size() / 8))
        {
            std::vector<std::vector<long long>> out;
            for (auto& b : blocks) if (b.size() >= MIN_ITERATIONS) out.push_back(b);
            return out;
        }
        return {run};
    }

    void ProgramBuilder::build_rollup()
    {
        for (const auto& key : M.order) steps[key.second].push_back(key.first);
        for (const auto& [ph, _] : steps) step_phases.push_back(ph);

        std::vector<std::vector<long long>> runs;
        size_t i = 0;
        while (i < step_phases.size())
        {
            size_t j = i + 1;
            if (first_step_ok(step_phases[i]))
                while (j < step_phases.size() && compatible(step_phases[j - 1], step_phases[j], step_phases[i])) ++j;
            if (j - i >= MIN_ITERATIONS)
            {
                runs.emplace_back(step_phases.begin() + static_cast<long>(i), step_phases.begin() + static_cast<long>(j));
                i = j;
            }
            else ++i;
        }
        std::vector<std::vector<long long>> split;
        for (const auto& run : runs) for (auto& sub : split_run(run)) split.push_back(sub);

        for (size_t li = 0; li < split.size(); ++li)
        {
            const auto& run = split[li];
            const long long first = run[0];
            std::vector<std::string> names = steps.at(first);
            std::sort(names.begin(), names.end());
            std::map<std::string, z3::expr> sym_vars;
            for (const auto& name : names)
                for (const auto& [sym, var] : raw_symbols(name, first)) sym_vars.insert_or_assign(sym, var);
            std::set<std::vector<unsigned>> sigs;
            for (long long ph : run)
            {
                std::vector<unsigned> s;
                for (const auto& nm : names) s.push_back(M.nodes.at({nm, ph}).expr.id());
                sigs.insert(s);
            }
            const bool uniform = sigs.size() == 1;
            std::vector<std::pair<std::string, z3::expr>> invariant;
            Loop L;
            L.index = li;
            L.phases = run;
            L.names = names;
            if (!uniform)
            {
                for (const auto& [sym, var] : sym_vars)
                {
                    std::vector<std::string> pats;
                    for (long long ph : run) pats.push_back(M.known_pattern(sym, ph));
                    bool same = true, known = true;
                    for (const auto& pt : pats) { same &= pt == pats[0]; known &= pt.find('x') == std::string::npos; }
                    const unsigned w = var.get_sort().bv_size();
                    if (same)
                    {
                        const std::string& pat = pats[0];
                        if (pat.find('x') == std::string::npos) invariant.emplace_back(sym, const_bv(M.ctx, pat, w));
                        else if (pat.find_first_not_of('x') != std::string::npos) invariant.emplace_back(sym, masked_known(var, pat));
                    }
                    else if (known) L.iter_vars.emplace_back(sym, w);
                    else L.partial.emplace_back(sym, w);
                }
            }
            auto lsb_int = [](const std::string& pt) { return from_binary(std::string(pt.rbegin(), pt.rend())); };
            for (long long ph : run)
            {
                std::map<std::string, BigInt> row;
                for (const auto& [sym, _] : L.iter_vars) row[sym] = lsb_int(M.known_pattern(sym, ph));
                L.table.push_back(row);
                std::map<std::string, std::pair<BigInt, BigInt>> prow;
                for (const auto& [sym, _] : L.partial)
                {
                    const std::string& pt = M.known_pattern(sym, ph);
                    std::string m, val;
                    for (char c : pt) { m += c != 'x' ? '1' : '0'; val += c != 'x' ? c : '0'; }
                    prow[sym] = {lsb_int(m), lsb_int(val)};
                }
                L.ptable.push_back(prow);
            }
            std::vector<std::pair<std::string, z3::expr>> subs = invariant;
            std::map<std::string, z3::expr> iter_sym;
            for (const auto& [sym, w] : L.iter_vars)
            {
                iter_sym.emplace(sym, M.ctx.bv_const(("iter:" + sym).c_str(), w));
                subs.emplace_back(sym, iter_sym.at(sym));
            }
            for (const auto& [sym, w] : L.partial) subs.emplace_back(sym, M.ctx.bv_const(("pv:" + sym).c_str(), w));
            for (const auto& name : names)
            {
                if (uniform) { L.templates.emplace(name, M.nodes.at({name, first}).expr); continue; }
                std::vector<std::pair<z3::expr, z3::expr>> s2;
                for (const auto& [sym, e] : subs) s2.emplace_back(sym_vars.at(sym), e);
                L.templates.emplace(name, substitute(M.nodes.at({name, first}).raw, s2).simplify());
            }
            for (size_t it = 0; it < run.size(); ++it)
            {
                std::vector<std::pair<z3::expr, z3::expr>> inst;
                for (const auto& [sym, w] : L.iter_vars)
                    inst.emplace_back(iter_sym.at(sym), M.ctx.bv_val(L.table[it].at(sym).str().c_str(), w));
                for (const auto& [sym, w] : L.partial)
                {
                    const auto& [imask, ival] = L.ptable[it].at(sym);
                    // Python: (BitVec("pv:"+sym), (sym_vars[sym] & BitVecVal(~imask)) | BitVecVal(ival)), left to right
                    z3::expr pv = M.ctx.bv_const(("pv:" + sym).c_str(), w);
                    z3::expr keep = M.ctx.bv_val((mask_of(w) ^ imask).str().c_str(), w);
                    z3::expr anded = sym_vars.at(sym) & keep;
                    z3::expr val = M.ctx.bv_val(ival.str().c_str(), w);
                    inst.emplace_back(pv, anded | val);
                }
                for (const auto& name : names)
                {
                    z3::expr instance = inst.empty() ? L.templates.at(name) : substitute(L.templates.at(name), inst);
                    z3::solver solver(M.ctx);
                    z3::params p(M.ctx);
                    p.set("timeout", 120000u);
                    solver.set(p);
                    solver.add(instance != M.nodes.at({name, run[it]}).expr);
                    const z3::check_result r = solver.check();
                    const std::string rs = r == z3::unsat ? "unsat" : r == z3::sat ? "sat" : "unknown";
                    roll_proofs[rs] += 1;
                    require(r == z3::unsat, "roll-up iteration " + std::to_string(it) + " of " + name + " not proven: " + rs);
                }
            }
            L.stride = run[1] - run[0];
            L.regular = true;
            for (size_t k = 0; k + 1 < run.size(); ++k) L.regular &= run[k + 1] - run[k] == L.stride;
            L.counter = find_counter(L.iter_vars, L.table);
            loops.push_back(std::move(L));
        }

        // rolled program
        std::map<Key, size_t> in_loop;
        for (size_t li = 0; li < loops.size(); ++li)
            for (long long ph : loops[li].phases)
                for (const auto& name : loops[li].names) in_loop[{name, ph}] = li;
        std::vector<std::string> rolled = header;
        std::set<size_t> emitted;
        for (const auto& key : M.order)
        {
            auto it = in_loop.find(key);
            std::vector<std::string> part;
            if (it == in_loop.end()) part = emit_flat_node(key);
            else if (emitted.insert(it->second).second) part = emit_loop(loops[it->second]);
            rolled.insert(rolled.end(), part.begin(), part.end());
        }
        rolled.insert(rolled.end(), tail.begin(), tail.end());
        program_rolled = join(rolled, "\n") + "\n";
    }

    std::vector<std::string> ProgramBuilder::emit_loop(const Loop& L, bool lifted)
    {
        const long long first = L.phases[0];
        const std::set<std::string> carried(L.names.begin(), L.names.end());
        const std::string idx = std::to_string(L.index);
        std::vector<std::string> out{"    # loop " + idx + ": " + std::to_string(L.phases.size()) + " iterations writing " + join(L.names, ", ")};
        const size_t n = L.phases.size();
        std::string phase_expr;
        if (L.regular) phase_expr = std::to_string(first) + " + " + std::to_string(L.stride) + " * it";
        else
        {
            std::vector<Py> ps;
            for (long long p : L.phases) ps.push_back(py(p));
            out.push_back("    L" + idx + "_PHASE = " + Py::list(ps).repr());
            phase_expr = "L" + idx + "_PHASE[it]";
        }
        for (const auto& [sym, w] : L.iter_vars)
        {
            if (L.counter) continue;
            std::vector<Py> vals;
            for (const auto& row : L.table) vals.push_back(py(row.at(sym)));
            out.push_back("    L" + idx + "_" + pyname(sym) + " = " + Py::list(vals).repr());
        }
        for (const auto& [sym, w] : L.partial)
        {
            std::vector<Py> vals;
            for (const auto& row : L.ptable) vals.push_back(Py::tuple({py(row.at(sym).first), py(row.at(sym).second)}));
            out.push_back("    L" + idx + "_" + pyname(sym) + " = " + Py::list(vals).repr() + "  # (known mask, known value)");
        }
        std::map<std::string, std::string> init;
        for (const auto& name : L.names)
        {
            const Node& node = M.nodes.at({name, first});
            for (const auto& s : node.sources.items)
            {
                const std::string base = partition_colon(s.symbol).second;
                if (s.symbol.rfind("word:", 0) == 0 && carried.count(base) && data_symbol(s.symbol, first) && !init.count(base))
                    init[base] = source_code(node, s.symbol, true);
            }
        }
        for (const auto& [base, code] : init) out.push_back("    cur_" + base + " = " + code);
        out.push_back("    for it in range(" + std::to_string(n) + "):");
        out.push_back("        ph = " + phase_expr);
        if (L.counter)
        {
            const Counter& c = *L.counter;
            out.push_back("        r = (" + py_int(c.a) + " + " + py_int(c.b) + " * it) & " + py_hex(mask_of(c.width)) +
                          "  # iteration counter (" + std::to_string(c.width) + " bits)");
            std::map<std::string, std::vector<std::pair<unsigned, unsigned>>> per_sym;
            for (unsigned q = 0; q < c.bits.size(); ++q) per_sym[c.bits[q].first].emplace_back(c.bits[q].second, q);
            for (const auto& [sym, w] : L.iter_vars)
            {
                auto pairs = per_sym[sym];
                std::sort(pairs.begin(), pairs.end());
                std::vector<std::string> parts;
                for (const auto& [k, q] : pairs)
                    parts.push_back(k ? "(((r >> " + std::to_string(q) + ") & 1) << " + std::to_string(k) + ")" : "((r >> " + std::to_string(q) + ") & 1)");
                out.push_back("        " + pyname("iter:" + sym) + " = " + join(parts, " | "));
            }
        }
        else
        {
            for (const auto& [sym, w] : L.iter_vars)
                out.push_back("        " + pyname("iter:" + sym) + " = L" + idx + "_" + pyname(sym) + "[it]");
        }
        for (const auto& [sym, w] : L.partial)
        {
            const std::string base_ = partition_colon(sym).second;
            const std::string mask_ = pyname("imask:" + sym), val_ = pyname("ival:" + sym);
            out.push_back("        " + mask_ + ", " + val_ + " = L" + idx + "_" + pyname(sym) + "[it]");
            std::string value_;
            if (sym.rfind("word:", 0) == 0)
            {
                const Node* holder = nullptr;
                for (const auto& nm : L.names)
                    if (M.nodes.at({nm, first}).sources.find(sym)) { holder = &M.nodes.at({nm, first}); break; }
                require(holder != nullptr, "StopIteration: no holder for " + sym);
                value_ = carried.count(base_) ? "cur_" + base_ : source_code(*holder, sym, true);
            }
            else value_ = "pads.get((" + py_str_repr(base_) + ", ph), 0)";
            out.push_back("        " + pyname("pv:" + sym) + " = (" + value_ + " & ~" + mask_ + ") | " + val_ + "  # certified known bits of this iteration");
        }
        std::vector<std::string> new_names;
        for (const auto& name : L.names)
        {
            const Node& node = M.nodes.at({name, first});
            Printer::Env env = [&](const std::string& sym) -> std::string {
                const auto [kind, base] = partition_colon(sym);
                if (kind == "word")
                {
                    if (carried.count(base)) return "cur_" + base;
                    return source_code(node, sym, true);
                }
                if (kind == "input") return "pads.get((" + py_str_repr(base) + ", ph), 0)";
                if (kind == "iter" || kind == "imask" || kind == "ival" || kind == "pv") return pyname(sym);
                throw std::runtime_error("KeyError " + sym);
            };
            std::optional<std::vector<std::string>> lifted_code;
            if (lifted && lift_hook)
            {
                std::map<std::string, std::pair<std::string, unsigned>> alias;
                if (L.counter)
                {
                    std::map<std::string, unsigned> ivw(L.iter_vars.begin(), L.iter_vars.end());
                    for (unsigned q = 0; q < L.counter->bits.size(); ++q)
                        if (ivw.at(L.counter->bits[q].first) == 1) alias["iter:" + L.counter->bits[q].first] = {"r", q};
                }
                Json node_id = Json::array({name, "loop" + idx});
                lifted_code = lift_hook(L.templates.at(name), env, 8, [](const std::string& s) {
                    const auto k = partition_colon(s).first;
                    return k == "iter" || k == "imask" || k == "ival";
                }, "new_" + name, alias, node_id, "ph", name);
            }
            Printer pr(env);
            const std::string code = pr.emit(L.templates.at(name));
            std::vector<std::string> plain;
            for (const auto& ln : pr.lines) plain.push_back("    " + ln);
            plain.push_back("        new_" + name + " = " + code);
            const auto& chosen = (lifted_code && lifted_code->size() <= plain.size()) ? *lifted_code : plain;
            out.insert(out.end(), chosen.begin(), chosen.end());
            new_names.push_back(name);
        }
        for (const auto& name : new_names) out.push_back("        v[(" + py_str_repr(name) + ", ph)] = new_" + name);
        std::vector<std::string> cw;
        for (const auto& nm : new_names) if (init.count(nm)) cw.push_back(nm);
        if (!cw.empty())
        {
            std::vector<std::string> lhs, rhs;
            for (const auto& nm : cw) { lhs.push_back("cur_" + nm); rhs.push_back("new_" + nm); }
            out.push_back("        " + join(lhs, ", ") + " = " + join(rhs, ", "));
        }
        return out;
    }
}
