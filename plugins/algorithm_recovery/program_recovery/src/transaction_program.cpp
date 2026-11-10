#include "transaction_program.h"
#include <algorithm>
#include <numeric>
#include <set>

namespace program_recovery
{
    static const unsigned MIN_ITERATIONS = 3;
    static const size_t MAX_BODY_STEPS = 32; // longest loop body searched, in steps (write phases)

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

    std::vector<std::string> Loop::names() const
    {
        std::vector<std::string> out;
        std::set<std::string> seen;
        for (const auto& s : body)
            for (const auto& n : s.names)
                if (seen.insert(n).second) out.push_back(n);
        return out;
    }

    // An iteration that spans several steps: block `cur` repeats the first block if it writes the same words at the same
    // phase offsets with the same raw equations, if every data read of a word the body writes comes from that word's
    // latest write in loop order (earlier in this iteration, else in the previous one), and if every other data read
    // comes from the same source as in the first block.
    bool ProgramBuilder::block_compatible(const std::vector<long long>& prev, const std::vector<long long>& cur,
                                          const std::vector<long long>& first) const
    {
        const size_t k = first.size();
        std::set<std::string> written;
        for (long long ph : first) written.insert(steps.at(ph).begin(), steps.at(ph).end());
        auto writes = [&](long long ph, const std::string& w) {
            const auto& ns = steps.at(ph);
            return std::find(ns.begin(), ns.end(), w) != ns.end();
        };
        for (size_t q = 0; q < k; ++q)
        {
            if (cur[q] - cur[0] != first[q] - first[0]) return false;
            std::vector<std::string> a = steps.at(cur[q]), b = steps.at(first[q]);
            std::sort(a.begin(), a.end());
            std::sort(b.begin(), b.end());
            if (a != b) return false;
            for (const auto& name : b)
            {
                const Node& cn = M.nodes.at({name, cur[q]});
                const Node& fn = M.nodes.at({name, first[q]});
                if (cn.raw_sha256 != fn.raw_sha256) return false;
                for (const auto& [sym, _] : raw_symbols(name, cur[q]))
                {
                    if (sym.rfind("word:", 0) != 0) continue;
                    if (data_symbol(sym, cur[q]) != data_symbol(sym, first[q])) return false;
                    if (!data_symbol(sym, cur[q])) continue;
                    const Source& s = cn.sources.at(sym);
                    const std::string base = partition_colon(sym).second;
                    if (written.count(base))
                    {
                        long long expected = -1;
                        for (size_t q2 = q; q2-- > 0;)
                            if (writes(cur[q2], base)) { expected = cur[q2]; break; }
                        if (expected < 0)
                            for (size_t q2 = k; q2-- > 0;)
                                if (writes(prev[q2], base)) { expected = prev[q2]; break; }
                        if (!(s.name == base && s.phase == expected && s.delay == 0)) return false;
                    }
                    else
                    {
                        const Source& f = fn.sources.at(sym);
                        if (!(s.name == f.name && s.phase == f.phase && s.delay == f.delay)) return false;
                    }
                }
            }
        }
        return true;
    }

    // First block: a body word read before the body writes it is read from one source (the loop's initial value); a read
    // after the body wrote it in this block comes from that write.
    bool ProgramBuilder::first_block_ok(const std::vector<long long>& block) const
    {
        std::set<std::string> written;
        for (long long ph : block) written.insert(steps.at(ph).begin(), steps.at(ph).end());
        std::map<std::string, std::pair<long long, long long>> initial;
        for (size_t q = 0; q < block.size(); ++q)
            for (const auto& name : steps.at(block[q]))
                for (const auto& [sym, _] : raw_symbols(name, block[q]))
                {
                    const std::string base = partition_colon(sym).second;
                    if (sym.rfind("word:", 0) != 0 || !written.count(base) || !data_symbol(sym, block[q])) continue;
                    const Source& s = M.nodes.at({name, block[q]}).sources.at(sym);
                    long long latest = -1;
                    for (size_t q2 = q; q2-- > 0;)
                    {
                        const auto& ns = steps.at(block[q2]);
                        if (std::find(ns.begin(), ns.end(), base) != ns.end()) { latest = block[q2]; break; }
                    }
                    if (latest >= 0)
                    {
                        if (!(s.name == base && s.phase == latest && s.delay == 0)) return false;
                        continue;
                    }
                    const auto [it, inserted] = initial.emplace(base, std::make_pair(s.phase, s.delay));
                    if (it->second != std::make_pair(s.phase, s.delay)) return false;
                }
        return true;
    }

    // One step of a loop body over the phases `run` (one per iteration): iteration constants, templates and the proof
    // that every iteration is the template instance.
    LoopStep ProgramBuilder::make_step(const std::vector<long long>& run, long long offset)
    {
        LoopStep S;
        S.offset = offset;
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
        S.names = names;
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
                else if (known) S.iter_vars.emplace_back(sym, w);
                else S.partial.emplace_back(sym, w);
            }
        }
        auto lsb_int = [](const std::string& pt) { return from_binary(std::string(pt.rbegin(), pt.rend())); };
        for (long long ph : run)
        {
            std::map<std::string, BigInt> row;
            for (const auto& [sym, _] : S.iter_vars) row[sym] = lsb_int(M.known_pattern(sym, ph));
            S.table.push_back(row);
            std::map<std::string, std::pair<BigInt, BigInt>> prow;
            for (const auto& [sym, _] : S.partial)
            {
                const std::string& pt = M.known_pattern(sym, ph);
                std::string m, val;
                for (char c : pt) { m += c != 'x' ? '1' : '0'; val += c != 'x' ? c : '0'; }
                prow[sym] = {lsb_int(m), lsb_int(val)};
            }
            S.ptable.push_back(prow);
        }
        std::vector<std::pair<std::string, z3::expr>> subs = invariant;
        std::map<std::string, z3::expr> iter_sym;
        for (const auto& [sym, w] : S.iter_vars)
        {
            iter_sym.emplace(sym, M.ctx.bv_const(("iter:" + sym).c_str(), w));
            // bits equal in every iteration enter the template as constants; only the varying bits stay symbolic
            BigInt vary = 0, common = 0;
            for (unsigned b = 0; b < w; ++b)
            {
                const BigInt bit = BigInt(1) << b;
                const bool v0 = ((S.table[0].at(sym) >> b) & 1) != 0;
                bool same = true;
                for (const auto& row : S.table) same &= (((row.at(sym) >> b) & 1) != 0) == v0;
                if (!same) vary |= bit;
                else if (v0) common |= bit;
            }
            if (vary == mask_of(w)) subs.emplace_back(sym, iter_sym.at(sym));
            else subs.emplace_back(sym, (iter_sym.at(sym) & M.ctx.bv_val(vary.str().c_str(), w)) | M.ctx.bv_val(common.str().c_str(), w));
        }
        for (const auto& [sym, w] : S.partial) subs.emplace_back(sym, M.ctx.bv_const(("pv:" + sym).c_str(), w));
        for (const auto& name : names)
        {
            if (uniform) { S.templates.emplace(name, M.nodes.at({name, first}).expr); continue; }
            std::vector<std::pair<z3::expr, z3::expr>> s2;
            for (const auto& [sym, e] : subs) s2.emplace_back(sym_vars.at(sym), e);
            S.templates.emplace(name, substitute(M.nodes.at({name, first}).raw, s2).simplify());
        }
        for (size_t it = 0; it < run.size(); ++it)
        {
            std::vector<std::pair<z3::expr, z3::expr>> inst;
            for (const auto& [sym, w] : S.iter_vars)
                inst.emplace_back(iter_sym.at(sym), M.ctx.bv_val(S.table[it].at(sym).str().c_str(), w));
            for (const auto& [sym, w] : S.partial)
            {
                const auto& [imask, ival] = S.ptable[it].at(sym);
                // Python: (BitVec("pv:"+sym), (sym_vars[sym] & BitVecVal(~imask)) | BitVecVal(ival)), left to right
                z3::expr pv = M.ctx.bv_const(("pv:" + sym).c_str(), w);
                z3::expr keep = M.ctx.bv_val((mask_of(w) ^ imask).str().c_str(), w);
                z3::expr anded = sym_vars.at(sym) & keep;
                z3::expr val = M.ctx.bv_val(ival.str().c_str(), w);
                inst.emplace_back(pv, anded | val);
            }
            for (const auto& name : names)
            {
                z3::expr instance = inst.empty() ? S.templates.at(name) : substitute(S.templates.at(name), inst);
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
        S.counter = find_counter(S.iter_vars, S.table);
        return S;
    }

    void ProgramBuilder::build_rollup()
    {
        for (const auto& key : M.order) steps[key.second].push_back(key.first);
        for (const auto& [ph, _] : steps) step_phases.push_back(ph);

        // loops whose body is one step
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
        std::vector<std::vector<std::vector<long long>>> found; // loop -> iteration -> phases of its steps
        std::set<long long> covered;
        for (const auto& run : split)
        {
            std::vector<std::vector<long long>> its;
            for (long long ph : run) { its.push_back({ph}); covered.insert(ph); }
            found.push_back(std::move(its));
        }
        // loops whose iteration spans several steps, on each stretch of steps outside the one-step loops; per start,
        // the body length that puts the most steps into a loop (the shortest on a tie)
        for (size_t a = 0; a < step_phases.size();)
        {
            if (covered.count(step_phases[a])) { ++a; continue; }
            size_t end = a;
            while (end < step_phases.size() && !covered.count(step_phases[end])) ++end;
            for (size_t j = a; j < end;)
            {
                auto block = [&](size_t k, size_t it) {
                    return std::vector<long long>(step_phases.begin() + static_cast<long>(j + it * k),
                                                  step_phases.begin() + static_cast<long>(j + (it + 1) * k));
                };
                size_t best_k = 0, best_n = 0;
                for (size_t k = 2; k <= MAX_BODY_STEPS && j + MIN_ITERATIONS * k <= end; ++k)
                {
                    const auto b0 = block(k, 0);
                    if (!first_block_ok(b0)) continue;
                    size_t n = 1;
                    while (j + (n + 1) * k <= end && block_compatible(block(k, n - 1), block(k, n), b0)) ++n;
                    if (n >= MIN_ITERATIONS && n * k > best_n * best_k) { best_k = k; best_n = n; }
                }
                if (!best_k) { ++j; continue; }
                std::vector<std::vector<long long>> its;
                for (size_t it = 0; it < best_n; ++it) its.push_back(block(best_k, it));
                found.push_back(std::move(its));
                j += best_k * best_n;
            }
            a = end;
        }
        std::stable_sort(found.begin(), found.end(), [](const auto& x, const auto& y) { return x[0][0] < y[0][0]; });

        for (size_t li = 0; li < found.size(); ++li)
        {
            const auto& its = found[li];
            Loop L;
            L.index = li;
            for (const auto& blk : its) L.phases.push_back(blk[0]);
            for (size_t q = 0; q < its[0].size(); ++q)
            {
                std::vector<long long> run;
                for (const auto& blk : its) run.push_back(blk[q]);
                L.body.push_back(make_step(run, its[0][q] - its[0][0]));
            }
            L.stride = L.phases[1] - L.phases[0];
            L.regular = true;
            for (size_t k = 0; k + 1 < L.phases.size(); ++k) L.regular &= L.phases[k + 1] - L.phases[k] == L.stride;
            loops.push_back(std::move(L));
        }

        // rolled program
        std::map<Key, size_t> in_loop;
        for (size_t li = 0; li < loops.size(); ++li)
            for (long long ph : loops[li].phases)
                for (const auto& s : loops[li].body)
                    for (const auto& name : s.names) in_loop[{name, ph + s.offset}] = li;
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
        const bool multi = L.body.size() > 1;
        const long long first = L.phases[0];
        const std::vector<std::string> all_names = L.names();
        const std::set<std::string> carried(all_names.begin(), all_names.end());
        const std::string idx = std::to_string(L.index);
        const size_t n = L.phases.size();
        std::vector<std::string> out{"    # loop " + idx + ": " + std::to_string(n) + " iterations" +
                                     (multi ? " of a " + std::to_string(L.body.size()) + "-step body" : "") + " writing " + join(all_names, ", ")};
        auto table_name = [&](size_t q, const std::string& sym) { return "L" + idx + "_" + (multi ? std::to_string(q) + "_" : "") + pyname(sym); };
        if (!L.regular)
        {
            std::vector<Py> ps;
            for (long long p : L.phases) ps.push_back(py(p));
            out.push_back("    L" + idx + "_PHASE = " + Py::list(ps).repr());
        }
        auto phase_expr = [&](const LoopStep& s) {
            if (L.regular) return std::to_string(first + s.offset) + " + " + std::to_string(L.stride) + " * it";
            return "L" + idx + "_PHASE[it]" + (s.offset ? " + " + std::to_string(s.offset) : std::string());
        };
        for (size_t q = 0; q < L.body.size(); ++q)
            for (const auto& [sym, w] : L.body[q].iter_vars)
            {
                if (L.body[q].counter) continue;
                std::vector<Py> vals;
                for (const auto& row : L.body[q].table) vals.push_back(py(row.at(sym)));
                out.push_back("    " + table_name(q, sym) + " = " + Py::list(vals).repr());
            }
        for (size_t q = 0; q < L.body.size(); ++q)
            for (const auto& [sym, w] : L.body[q].partial)
            {
                std::vector<Py> vals;
                for (const auto& row : L.body[q].ptable) vals.push_back(Py::tuple({py(row.at(sym).first), py(row.at(sym).second)}));
                out.push_back("    " + table_name(q, sym) + " = " + Py::list(vals).repr() + "  # (known mask, known value)");
            }
        // initial values of the body words read before the body writes them; read_carried: body words read as cur_<word>
        // The initial value is read from its source only if the first iteration's (specialised) equation uses it: the
        // template is the raw equation, which may name a word the first iteration ignores (a hold path its control
        // never takes); reading it would add a state value the certified interface does not have. Each first-iteration
        // instance is proved equal to its specialised equation, so an unused initial value cannot change the result.
        std::map<std::string, std::string> init;
        std::set<std::string> read_carried, written_before, unused_initial;
        for (const auto& s : L.body)
        {
            for (const auto& name : s.names)
            {
                const Node& node = M.nodes.at({name, first + s.offset});
                std::set<std::string> used;
                for (const auto& v : z3_vars(node.expr)) used.insert(sym_name(v));
                for (const auto& src : node.sources.items)
                {
                    const std::string base = partition_colon(src.symbol).second;
                    if (src.symbol.rfind("word:", 0) == 0 && carried.count(base) && data_symbol(src.symbol, first + s.offset))
                    {
                        read_carried.insert(base);
                        if (written_before.count(base) || init.count(base)) continue;
                        if (used.count(src.symbol)) init[base] = source_code(node, src.symbol, true);
                        else unused_initial.insert(base);
                    }
                }
            }
            written_before.insert(s.names.begin(), s.names.end());
        }
        for (const auto& base : unused_initial)
            if (!init.count(base)) init[base] = "0  # initial value not used: the first iteration does not read it";
        for (const auto& [base, code] : init) out.push_back("    cur_" + base + " = " + code);
        out.push_back("    for it in range(" + std::to_string(n) + "):");
        for (size_t q = 0; q < L.body.size(); ++q)
        {
            const LoopStep& S = L.body[q];
            const long long ph0 = first + S.offset;
            out.push_back("        ph = " + phase_expr(S));
            if (S.counter)
            {
                const Counter& c = *S.counter;
                out.push_back("        r = (" + py_int(c.a) + " + " + py_int(c.b) + " * it) & " + py_hex(mask_of(c.width)) +
                              "  # iteration counter (" + std::to_string(c.width) + " bits)");
                std::map<std::string, std::vector<std::pair<unsigned, unsigned>>> per_sym;
                for (unsigned qb = 0; qb < c.bits.size(); ++qb) per_sym[c.bits[qb].first].emplace_back(c.bits[qb].second, qb);
                for (const auto& [sym, w] : S.iter_vars)
                {
                    auto pairs = per_sym[sym];
                    std::sort(pairs.begin(), pairs.end());
                    std::vector<std::string> parts;
                    for (const auto& [k, qb] : pairs)
                        parts.push_back(k ? "(((r >> " + std::to_string(qb) + ") & 1) << " + std::to_string(k) + ")" : "((r >> " + std::to_string(qb) + ") & 1)");
                    out.push_back("        " + pyname("iter:" + sym) + " = " + join(parts, " | "));
                }
            }
            else
            {
                for (const auto& [sym, w] : S.iter_vars)
                    out.push_back("        " + pyname("iter:" + sym) + " = " + table_name(q, sym) + "[it]");
            }
            for (const auto& [sym, w] : S.partial)
            {
                const std::string base_ = partition_colon(sym).second;
                const std::string mask_ = pyname("imask:" + sym), val_ = pyname("ival:" + sym);
                out.push_back("        " + mask_ + ", " + val_ + " = " + table_name(q, sym) + "[it]");
                std::string value_;
                if (sym.rfind("word:", 0) == 0)
                {
                    const Node* holder = nullptr;
                    for (const auto& nm : S.names)
                        if (M.nodes.at({nm, ph0}).sources.find(sym)) { holder = &M.nodes.at({nm, ph0}); break; }
                    require(holder != nullptr, "StopIteration: no holder for " + sym);
                    value_ = carried.count(base_) ? "cur_" + base_ : source_code(*holder, sym, true);
                }
                else value_ = "pads.get((" + py_str_repr(base_) + ", ph), 0)";
                out.push_back("        " + pyname("pv:" + sym) + " = (" + value_ + " & ~" + mask_ + ") | " + val_ + "  # certified known bits of this iteration");
            }
            for (const auto& name : S.names)
            {
                const Node& node = M.nodes.at({name, ph0});
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
                    if (S.counter)
                    {
                        std::map<std::string, unsigned> ivw(S.iter_vars.begin(), S.iter_vars.end());
                        for (unsigned qb = 0; qb < S.counter->bits.size(); ++qb)
                            if (ivw.at(S.counter->bits[qb].first) == 1) alias["iter:" + S.counter->bits[qb].first] = {"r", qb};
                    }
                    Json node_id = Json::array({name, "loop" + idx + (multi ? "." + std::to_string(q) : "")});
                    lifted_code = lift_hook(S.templates.at(name), env, 8, [](const std::string& s) {
                        const auto k = partition_colon(s).first;
                        return k == "iter" || k == "imask" || k == "ival";
                    }, "new_" + name, alias, node_id, "ph", name);
                }
                Printer pr(env);
                const std::string code = pr.emit(S.templates.at(name));
                std::vector<std::string> plain;
                for (const auto& ln : pr.lines) plain.push_back("    " + ln);
                plain.push_back("        new_" + name + " = " + code);
                const bool lifted_chosen = lifted_code && lifted_code->size() <= plain.size();
                if (lifted && lift_note) lift_note(lifted_chosen, S.templates.at(name).get_sort().bv_size());
                const auto& chosen = lifted_chosen ? *lifted_code : plain;
                out.insert(out.end(), chosen.begin(), chosen.end());
            }
            for (const auto& name : S.names) out.push_back("        v[(" + py_str_repr(name) + ", ph)] = new_" + name);
            std::vector<std::string> lhs, rhs;
            for (const auto& nm : S.names)
                if (read_carried.count(nm)) { lhs.push_back("cur_" + nm); rhs.push_back("new_" + nm); }
            if (!lhs.empty()) out.push_back("        " + join(lhs, ", ") + " = " + join(rhs, ", "));
        }
        return out;
    }
}
