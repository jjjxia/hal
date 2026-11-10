#include "lifted_program.h"
#include <algorithm>
#include <fstream>

namespace program_recovery
{
    static const char* PY_BENCHMARK_NAME = "benchmark generated from python API";

    PJ to_pj(const Json& j)
    {
        if (j.is_null()) return PJ();
        if (j.is_boolean()) return PJ(j.get<bool>());
        if (j.is_number_integer()) return PJ(j.get<long long>());
        if (j.is_number_unsigned()) return PJ(j.get<unsigned long long>());
        if (j.is_number_float()) return PJ(j.get<double>());
        if (j.is_string()) return PJ(j.get<std::string>());
        if (j.is_array()) { PJ l = PJ::list(); for (const auto& e : j) l.push(to_pj(e)); return l; }
        PJ d = PJ::dict();
        for (const auto& [k, v] : j.items()) d.set(k, to_pj(v));
        return d;
    }

    std::optional<std::string> base_word(const std::string& sym)
    {
        std::vector<std::string> parts;
        size_t start = 0;
        while (true)
        {
            const auto p = sym.find(':', start);
            parts.push_back(sym.substr(start, p == std::string::npos ? std::string::npos : p - start));
            if (p == std::string::npos) break;
            start = p + 1;
        }
        for (size_t i = 0; i + 1 < parts.size(); ++i) if (parts[i] == "word") return parts.back();
        return std::nullopt;
    }

    std::string Registry::constant(const std::string& prefix, const Py& value)
    {
        const auto key = std::make_pair(prefix, value.repr());
        if (auto it = names.find(key); it != names.end()) return it->second;
        size_t count = 0;
        for (const auto& [k, _] : names) if (k.first == prefix) ++count;
        const std::string name = prefix + std::to_string(count);
        names[key] = name;
        consts.emplace_back(name, value);
        return name;
    }

    std::tuple<std::string, std::vector<unsigned>, std::vector<unsigned>> Registry::classify(const std::vector<BigInt>& T, unsigned k, unsigned m)
    {
        auto it = std::find_if(classes.begin(), classes.end(), [&](const auto& c) { return c.first == std::make_pair(k, m); });
        if (it != classes.end())
        {
            for (const auto& [name, S] : it->second)
            {
                if (auto match = match_tables(S, T, k, m))
                {
                    uses[name] += 1;
                    return {name, match->first, match->second};
                }
            }
        }
        size_t total = 0;
        for (const auto& [_, v] : classes) total += v.size();
        const std::string name = "TABLE" + std::to_string(total);
        if (it == classes.end()) { classes.push_back({{k, m}, {}}); it = std::prev(classes.end()); }
        it->second.emplace_back(name, T);
        std::vector<Py> items;
        for (const auto& v : T) items.push_back(py(v));
        consts.emplace_back(name, Py::list(items));
        uses[name] += 1;
        std::vector<unsigned> in(k), out(m);
        for (unsigned i = 0; i < k; ++i) in[i] = i;
        for (unsigned i = 0; i < m; ++i) out[i] = i;
        return {name, in, out};
    }

    std::vector<std::string> Registry::lines() const
    {
        std::vector<std::string> out;
        for (const auto& [name, value] : consts) out.push_back(name + " = " + value.repr());
        return out;
    }

    void ProgramLifter::note_choice(bool lifted_chosen, unsigned width)
    {
        stat("program_bits", width);
        if (lifted_chosen) stat("program_lifted_bits", width - last_kept_bits);
    }

    void ProgramLifter::stat(const std::string& key, const BigInt& add)
    {
        for (auto& [k, v] : lift_stats) if (k == key) { v += add; return; }
        lift_stats.emplace_back(key, add);
    }

    void ProgramLifter::learn_order(const std::string& word, const std::vector<unsigned>& order, const std::string& how)
    {
        if (word.empty() || word_order.count(word)) return;
        if (!D.M.words().contains(word) || order.size() != D.M.words().at(word).at("bits").size()) return;
        std::vector<unsigned> s = order;
        std::sort(s.begin(), s.end());
        for (unsigned i = 0; i < s.size(); ++i) if (s[i] != i) return;
        word_order[word] = order;
        order_sources[word] = how;
    }

    namespace
    {
        Py bitref_tuple(const std::vector<std::pair<unsigned, unsigned>>& pairs)
        {
            std::vector<Py> items;
            for (const auto& [a, b] : pairs) items.push_back(Py::tuple({py(a), py(b)}));
            return Py::tuple(items);
        }
        Py uint_tuple(const std::vector<unsigned>& v)
        {
            std::vector<Py> items;
            for (unsigned x : v) items.push_back(py(x));
            return Py::tuple(items);
        }
        std::string signed_residue(const BigInt& c, unsigned m)
        {
            return ((c >> (m - 1)) != 0) ? BigInt(c - (BigInt(1) << m)).str() : c.str();
        }
        BigInt signed_residue_value(const BigInt& c, unsigned m) { return ((c >> (m - 1)) != 0) ? BigInt(c - (BigInt(1) << m)) : c; }
    }

    std::pair<std::set<unsigned>, std::vector<std::string>> ProgramLifter::arith_part(const z3::expr& expr, const std::map<std::string, z3::expr>& variables,
        const Json& node_id, const std::string& phase_code, const std::function<std::pair<std::string, unsigned>(const std::string&, unsigned)>& src,
        std::vector<std::string>& out, const std::string& ind, const std::string& writes, const IsParam& is_param)
    {
        const unsigned n = expr.get_sort().bv_size();
        if (n < 4) return {{}, {}};
        const auto [chains_, _left] = chains(expr);
        const ExprEval f(expr);
        PyRandom rng(20261106);
        std::set<unsigned> used;
        std::vector<std::string> terms;

        auto process = [&](const Candidate& cand) {
            for (unsigned o : cand.outputs) if (used.count(o)) return;
            bool any_bil = false;
            for (const auto& [_, v] : cand.bilinear) any_bil |= v != 0;
            const std::string kind = any_bil ? "bilinear" : (cand.bitwise ? "arx" : "linear");
            const unsigned timeout = kind == "linear" ? 60000u : kind == "bilinear" ? 20000u : 120000u;
            z3::expr z = candidate_expr(cand, variables);
            z3::solver solver(D.M.ctx);
            z3::params p(D.M.ctx);
            p.set("timeout", timeout);
            solver.set(p);
            std::vector<Z3_ast> terms_or;
            std::vector<z3::expr> keep;
            for (size_t q = 0; q < cand.outputs.size(); ++q)
            {
                z3::expr a = expr.extract(cand.outputs[q], cand.outputs[q]);
                z3::expr b = z.extract(static_cast<unsigned>(q), static_cast<unsigned>(q));
                z3::expr ne = a != b;
                keep.push_back(ne);
                terms_or.push_back(ne);
            }
            Z3_ast orr = Z3_mk_or(D.M.ctx, static_cast<unsigned>(terms_or.size()), terms_or.data());
            D.M.ctx.check_error();
            solver.add(z3::expr(D.M.ctx, orr));
            const std::string proofs = m_out + "/proofs";
            std::filesystem::create_directories(proofs);
            const std::string qfile = proofs + "/arith_" + std::to_string(arith_log.items.size()) + ".smt2";
            z3::expr_vector as = solver.assertions();
            std::vector<Z3_ast> v;
            for (unsigned i = 0; i + 1 < as.size(); ++i) v.push_back(as[i]);
            Z3_ast fml = as.size() > 0 ? static_cast<Z3_ast>(as[as.size() - 1]) : static_cast<Z3_ast>(D.M.ctx.bool_val(true));
            const std::string smt2 = Z3_benchmark_to_smtlib_string(D.M.ctx, PY_BENCHMARK_NAME, "", "unknown", "", static_cast<unsigned>(v.size()), v.data(), fml);
            {
                std::ofstream q(qfile, std::ios::binary);
                q << smt2;
            }
            const z3::check_result r = solver.check();
            const std::string status = r == z3::unsat ? "unsat" : r == z3::sat ? "sat" : "unknown";
            PJ entry = PJ::dict();
            entry.set("node", to_pj(node_id));
            entry.set("kind", kind);
            entry.set("width", cand.width);
            PJ ow = PJ::list();
            for (const auto& g : cand.operands) ow.push(PJ(static_cast<unsigned long long>(g.size())));
            entry.set("operand_widths", ow);
            PJ sg = PJ::list();
            for (bool b : cand.is_signed) sg.push(PJ(b));
            entry.set("signed", sg);
            PJ oo = PJ::list();
            for (unsigned o : cand.outputs) oo.push(PJ(o));
            entry.set("output_order", oo);
            if (cand.bitwise && cand.bitwise->name) entry.set("bitwise", *cand.bitwise->name);
            else if (cand.bitwise) entry.set("bitwise", true);
            else entry.set("bitwise", PJ());
            entry.set("const", cand.cnst);
            PJ lin = PJ::list();
            for (const auto& c : cand.linear) lin.push(c);
            entry.set("linear", lin);
            PJ bil = PJ::dict();
            for (const auto& [gh, dv] : cand.bilinear) bil.set(std::to_string(gh.first) + "," + std::to_string(gh.second), dv);
            entry.set("bilinear", bil);
            entry.set("random_tests", 64);
            std::string up = status;
            for (auto& ch : up) ch = static_cast<char>(std::toupper(static_cast<unsigned char>(ch)));
            entry.set("proof", up);
            entry.set("query", qfile);
            entry.set("sha256", sha256_hex(smt2));
            entry.set("method", cand.method.empty() ? "nested_support_chain" : cand.method);
            if (status != "unsat") { arith_log.push(entry); return; }
            entry.set("order_ambiguous", cand.order_ambiguous);
            arith_log.push(entry);
            if (!writes.empty() && cand.outputs.size() == n && !cand.order_ambiguous) learn_order(writes, cand.outputs, "proven " + kind + " output");
            for (const auto& grp : cand.operands)
            {
                std::set<std::string> syms_;
                bool has_none = false;
                for (const auto& b : grp) { if (b) syms_.insert(b->first); else has_none = true; }
                if (!has_none && syms_.size() == 1)
                {
                    if (auto bw = base_word(*syms_.begin()))
                    {
                        std::vector<unsigned> order;
                        for (const auto& b : grp) order.push_back(b->second);
                        learn_order(*bw, order, "proven " + kind + " operand");
                    }
                }
            }
            const unsigned m = cand.width;
            std::vector<std::string> names;
            for (size_t gi = 0; gi < cand.operands.size(); ++gi)
            {
                const auto& grp = cand.operands[gi];
                std::set<std::string> syms_;
                bool has_none = false;
                for (const auto& b : grp) { if (b) syms_.insert(b->first); else has_none = true; }
                const std::string nm = "a" + std::to_string(out.size()) + "_" + std::to_string(gi);
                bool all_pads = true;
                for (const auto& x : syms_) all_pads &= x.rfind("input:", 0) == 0 && variables.at(x).get_sort().bv_size() == 1;
                if (all_pads && !has_none)
                {
                    std::vector<Py> padnames;
                    for (const auto& b : grp) padnames.push_back(py(partition_colon(b->first).second));
                    out.push_back(ind + nm + " = padword(pads, " + phase_code + ", " + registry.constant("PADS", Py::tuple(padnames)) + ")");
                }
                else
                {
                    std::vector<std::optional<std::pair<std::string, unsigned>>> codes;
                    for (const auto& b : grp) codes.push_back(b ? std::optional(src(b->first, b->second)) : std::nullopt);
                    std::set<std::string> bases;
                    for (const auto& c : codes) if (c) bases.insert(c->first);
                    if (bases.size() == 1)
                    {
                        std::vector<Py> idx;
                        for (const auto& c : codes) idx.push_back(c ? py(c->second) : Py::none());
                        out.push_back(ind + nm + " = bits(" + *bases.begin() + ", *" + registry.constant("ORD", Py::tuple(idx)) + ")");
                    }
                    else
                    {
                        std::vector<std::string> ps;
                        for (const auto& c : codes) ps.push_back(c ? c->first + ", " + std::to_string(c->second) : "0, None");
                        out.push_back(ind + nm + " = pick(" + join(ps, ", ") + ")");
                    }
                }
                if (gi < cand.is_signed.size() && cand.is_signed[gi])
                    out.push_back(ind + nm + " = sgn(" + nm + ", " + std::to_string(grp.size()) + ")  # two's complement operand");
                names.push_back(nm);
            }
            std::vector<std::string> poly;
            if (cand.cnst != 0) poly.push_back(cand.cnst.str());
            for (size_t g = 0; g < cand.linear.size(); ++g)
            {
                const BigInt& cg = cand.linear[g];
                if (cg == 0) continue;
                const BigInt sc = signed_residue_value(cg, m);
                poly.push_back(sc == 1 ? names[g] : sc == -1 ? "-" + names[g] : "(" + signed_residue(cg, m) + ") * " + names[g]);
            }
            for (const auto& [gh, dv] : cand.bilinear)
            {
                if (dv == 0) continue;
                const BigInt sc = signed_residue_value(dv, m);
                poly.push_back(sc == 1 ? names[gh.first] + " * " + names[gh.second] : "(" + signed_residue(dv, m) + ") * " + names[gh.first] + " * " + names[gh.second]);
            }
            if (cand.bitwise)
            {
                const auto& bw = *cand.bitwise;
                std::vector<std::string> rn;
                for (size_t r = 0; r < bw.roles.size(); ++r)
                {
                    std::vector<std::pair<std::string, unsigned>> codes;
                    for (const auto& b : bw.roles[r]) codes.push_back(src(b.first, b.second));
                    std::set<std::string> bases;
                    for (const auto& c : codes) bases.insert(c.first);
                    const std::string nm = "w" + std::to_string(out.size()) + "_" + std::to_string(r);
                    if (bases.size() == 1)
                    {
                        std::vector<Py> idx;
                        for (const auto& c : codes) idx.push_back(py(c.second));
                        out.push_back(ind + nm + " = bits(" + *bases.begin() + ", *" + registry.constant("ORD", Py::tuple(idx)) + ")");
                    }
                    else
                    {
                        std::vector<std::string> ps;
                        for (const auto& c : codes) ps.push_back(c.first + ", " + std::to_string(c.second));
                        out.push_back(ind + nm + " = pick(" + join(ps, ", ") + ")");
                    }
                    rn.push_back(nm);
                }
                const std::string M_ = py_hex(mask_of(m));
                std::string fexpr;
                const std::string bname = bw.name.value_or("");
                if (bname == "ch" && rn.size() == 3) fexpr = "(" + rn[0] + " & " + rn[1] + ") | (~" + rn[0] + " & " + rn[2] + ")";
                else if (bname == "parity" && rn.size() == 3) fexpr = rn[0] + " ^ " + rn[1] + " ^ " + rn[2];
                else if (bname == "maj" && rn.size() == 3) fexpr = "(" + rn[0] + " & " + rn[1] + ") | (" + rn[0] + " & " + rn[2] + ") | (" + rn[1] + " & " + rn[2] + ")";
                else
                {
                    std::vector<std::string> terms_;
                    for (size_t x = 0; x < bw.table.size(); ++x)
                    {
                        if (bw.table[x] == 0) continue;
                        std::vector<std::string> fs;
                        for (size_t r = 0; r < rn.size(); ++r) fs.push_back((x >> r) & 1 ? rn[r] : "~" + rn[r]);
                        terms_.push_back("(" + join(fs, " & ") + ")");
                    }
                    fexpr = terms_.empty() ? "0" : join(terms_, " | ");
                }
                const std::string fname = "f" + std::to_string(out.size());
                out.push_back(ind + fname + " = (" + fexpr + ") & " + M_ + "  # bitwise layer" + (bw.name ? " (" + *bw.name + ")" : ""));
                poly.push_back(fname);
            }
            const std::string zname = "z" + std::to_string(out.size());
            out.push_back(ind + zname + " = (" + (poly.empty() ? "0" : join(poly, " + ")) + ") & " + py_hex(mask_of(m)) + "  # proven: " + kind + " word arithmetic");
            terms.push_back("place(" + zname + ", *" + registry.constant("OUT", uint_tuple(cand.outputs)) + ")");
            for (unsigned o : cand.outputs) used.insert(o);
            stat("arith_" + kind + "_bits", m);
        };

        for (const auto& chain : chains_)
        {
            auto cand = fit_chain(f, chain, rng);
            if (!cand) cand = fit_chain_arx(f, chain, rng);
            if (cand) process(*cand);
        }
        // known_order_candidates (evaluated after all chain candidates, with the current `used`)
        std::vector<unsigned> rest;
        for (unsigned i = 0; i < n; ++i) if (!used.count(i)) rest.push_back(i);
        if (rest.size() >= 4)
        {
            std::set<std::string> syms_all;
            std::set<BitRef> support;
            for (unsigned i : rest)
            {
                const auto sb = symbol_bits(expr.extract(i, i).simplify());
                for (const auto& [s, ks] : sb) syms_all.insert(s);
            }
            std::vector<std::string> syms;
            for (const auto& s : syms_all) if (!is_param(s)) syms.push_back(s);
            bool ok = 1 <= syms.size() && syms.size() <= 3;
            for (const auto& s : syms) { const auto bw = base_word(s); ok = ok && bw && word_order.count(*bw); }
            if (ok)
            {
                for (unsigned i : rest)
                {
                    const auto sb = symbol_bits(expr.extract(i, i).simplify());
                    for (const auto& [s, ks] : sb) for (unsigned k : ks) support.insert({s, k});
                }
                std::vector<std::vector<std::optional<BitRef>>> operands;
                for (const auto& x : syms)
                {
                    std::vector<std::optional<BitRef>> op;
                    for (unsigned k : word_order.at(*base_word(x)))
                        op.push_back(support.count({x, k}) ? std::optional<BitRef>(BitRef{x, k}) : std::nullopt);
                    operands.push_back(op);
                }
                if (auto cand = fit_known(f, n, operands, rng, rest)) process(*cand);
            }
        }
        return {used, terms};
    }

    std::optional<std::vector<std::string>> ProgramLifter::emit_lifted(const z3::expr& expr, const Printer::Env& env, unsigned indent, const IsParam& is_param,
                                                                const std::string& target, const std::map<std::string, std::pair<std::string, unsigned>>& alias,
                                                                const Json& node_id, const std::string& phase_code, const std::string& writes)
    {
        last_kept_bits = 0;
        std::map<std::string, z3::expr> variables;
        for (const auto& x : z3_vars(expr)) variables.insert_or_assign(sym_name(x), x);
        const std::string ind(indent, ' ');
        std::vector<std::string> out;
        std::vector<std::pair<std::string, std::string>> local; // insertion order
        auto src = [&](const std::string& sname, unsigned k) -> std::pair<std::string, unsigned> {
            if (auto it = alias.find(sname); it != alias.end()) return it->second;
            for (const auto& [s, code] : local) if (s == sname) return {code, k};
            const std::string code = "s" + std::to_string(local.size());
            local.emplace_back(sname, code);
            out.push_back(ind + code + " = " + env(sname));
            return {code, k};
        };
        const unsigned n = expr.get_sort().bv_size();
        auto [abits, aterms] = arith_part(expr, variables, node_id, phase_code, src, out, ind, writes, is_param);
        z3::expr rest = expr;
        if (!abits.empty())
        {
            BigInt m = mask_of(n);
            for (unsigned i : abits) m ^= BigInt(1) << i;
            z3::expr mv = expr.ctx().bv_val(m.str().c_str(), n);
            z3::expr anded = expr & mv;
            rest = anded.simplify();
        }
        const LiftPlan plan = lift_plan(rest, is_param);
        if (aterms.empty() && plan.route.empty() && plan.xr.empty() && plan.tables.empty() && plan.param_route.empty() && plan.dsd_linear.empty())
            return std::nullopt;

        auto gather = [&](const std::vector<BitRef>& items) {
            std::vector<std::pair<std::string, unsigned>> codes;
            for (const auto& [x, k] : items) codes.push_back(src(x, k));
            std::set<std::string> cs;
            for (const auto& c : codes) cs.insert(c.first);
            if (cs.size() == 1)
            {
                std::vector<Py> idx;
                for (const auto& c : codes) idx.push_back(py(c.second));
                return "bits(" + codes[0].first + ", *" + Py::tuple(idx).repr() + ")";
            }
            std::vector<std::string> ps;
            for (const auto& c : codes) ps.push_back(c.first + ", " + std::to_string(c.second));
            return "pick(" + join(ps, ", ") + ")";
        };
        auto routes = [&](const std::vector<std::pair<std::string, std::vector<std::pair<unsigned, unsigned>>>>& by, const std::string& prefix) {
            auto sorted_by = by;
            std::sort(sorted_by.begin(), sorted_by.end());
            std::vector<std::string> terms;
            for (auto& [code, pairs] : sorted_by)
            {
                std::sort(pairs.begin(), pairs.end());
                terms.push_back("route(" + code + ", " + registry.constant(prefix, bitref_tuple(pairs)) + ")");
            }
            return terms;
        };
        auto by_add = [](std::vector<std::pair<std::string, std::vector<std::pair<unsigned, unsigned>>>>& by, const std::string& code, unsigned o, unsigned b) {
            for (auto& [c, v] : by) if (c == code) { v.emplace_back(o, b); return; }
            by.push_back({code, {{o, b}}});
        };
        std::vector<std::string> parts = aterms, xor_terms;
        BigInt inv = 0;
        std::vector<std::pair<std::string, std::vector<std::pair<unsigned, unsigned>>>> by_code;
        std::vector<std::pair<unsigned, std::string>> pin_pairs;
        for (const auto& [o, ref, flag] : plan.route)
        {
            inv |= BigInt(flag) << o;
            if (variables.at(ref.first).get_sort().bv_size() == 1 && !alias.count(ref.first)) pin_pairs.emplace_back(o, src(ref.first, ref.second).first);
            else { const auto [code, b] = src(ref.first, ref.second); by_add(by_code, code, o, b); }
        }
        for (const auto& t : routes(by_code, "R")) parts.push_back(t);
        if (!pin_pairs.empty())
        {
            std::sort(pin_pairs.begin(), pin_pairs.end());
            std::vector<std::string> ps;
            for (const auto& [o, c] : pin_pairs) ps.push_back("(" + std::to_string(o) + ", " + c + ")");
            parts.push_back("pins(" + join(ps, ", ") + ")");
        }
        stat("route_bits", plan.route.size());
        if (!plan.xr.empty())
        {
            // layer (code, t) -> code -> pairs
            std::map<std::pair<std::string, size_t>, std::vector<std::pair<std::string, std::vector<std::pair<unsigned, unsigned>>>>> layers;
            for (const auto& [o, terms, c0] : plan.xr)
            {
                inv |= BigInt(c0) << o;
                std::vector<BitRef> keyed = terms;
                auto keyf = [&](const BitRef& xk) {
                    const long long w = variables.at(xk.first).get_sort().bv_size();
                    return std::make_pair(xk.first, ((static_cast<long long>(xk.second) - static_cast<long long>(o)) % w + w) % w);
                };
                std::stable_sort(keyed.begin(), keyed.end(), [&](const BitRef& a, const BitRef& b) { return keyf(a) < keyf(b); });
                std::map<std::string, size_t> seen;
                for (const auto& [x, k] : keyed)
                {
                    const auto [code, b] = src(x, k);
                    const auto layer = std::make_pair(code, seen[code]);
                    seen[code] += 1;
                    by_add(layers[layer], code, o, b);
                }
            }
            std::vector<std::string> xs;
            for (const auto& [layer, by] : layers) for (const auto& t : routes(by, "X")) xs.push_back(t);
            parts.push_back("(" + join(xs, " ^ ") + ")");
            stat("xor_bits", plan.xr.size());
        }
        // tables
        struct Inst { std::vector<unsigned> ia, ib; std::vector<unsigned> outs; };
        std::vector<std::pair<std::tuple<std::string, std::string, std::string, std::string>, std::vector<Inst>>> loops_;
        auto loops_add = [&](const std::tuple<std::string, std::string, std::string, std::string>& key, Inst inst) {
            for (auto& [k, v] : loops_) if (k == key) { v.push_back(std::move(inst)); return; }
            loops_.push_back({key, {std::move(inst)}});
        };
        for (const auto& t : plan.tables)
        {
            if (auto w = whiten(t, is_param))
            {
                const auto [name, in_order, out_order] = registry.classify(w->table, static_cast<unsigned>(w->a.size()), static_cast<unsigned>(t.outputs.size()));
                std::vector<BitRef> a, b;
                for (unsigned q : in_order) { a.push_back(w->a[q]); b.push_back(w->b[q]); }
                std::vector<unsigned> outs;
                for (unsigned q : out_order) outs.push_back(t.outputs[q]);
                std::set<std::string> asym, bsym;
                for (const auto& [x, _] : a) asym.insert(x);
                for (const auto& [x, _] : b) bsym.insert(x);
                bool aliased = false;
                for (const auto& s : asym) aliased |= alias.count(s) > 0;
                for (const auto& s : bsym) aliased |= alias.count(s) > 0;
                if (asym.size() == 1 && bsym.size() == 1 && !aliased)
                {
                    Inst inst;
                    for (const auto& [_, k] : a) inst.ia.push_back(k);
                    for (const auto& [_, k] : b) inst.ib.push_back(k);
                    inst.outs = outs;
                    loops_add({name, "xor", *asym.begin(), *bsym.begin()}, inst);
                }
                else
                {
                    const std::string ga = gather(a);   // Python evaluates the f-string left to right
                    const std::string gb = gather(b);
                    parts.push_back("place(" + name + "[" + ga + " ^ " + gb + "], *" + uint_tuple(outs).repr() + ")");
                }
                stat("xor_table_bits", t.outputs.size());
            }
            else
            {
                const auto [name, in_order, out_order] = registry.classify(t.table, static_cast<unsigned>(t.inputs.size()), static_cast<unsigned>(t.outputs.size()));
                std::vector<BitRef> ins;
                for (unsigned q : in_order) ins.push_back(t.inputs[q]);
                std::vector<unsigned> outs;
                for (unsigned q : out_order) outs.push_back(t.outputs[q]);
                std::set<std::string> isym;
                for (const auto& [x, _] : ins) isym.insert(x);
                bool aliased = false;
                for (const auto& s : isym) aliased |= alias.count(s) > 0;
                if (isym.size() == 1 && !aliased)
                {
                    Inst inst;
                    for (const auto& [_, k] : ins) inst.ia.push_back(k);
                    inst.outs = outs;
                    loops_add({name, "plain", *isym.begin(), ""}, inst);
                }
                else parts.push_back("place(" + name + "[" + gather(ins) + "], *" + uint_tuple(outs).repr() + ")");
                stat("table_bits", t.outputs.size());
            }
            stat("tables", 1);
        }
        for (const auto& [key, inst] : loops_)
        {
            const auto& [name, kind, A, B] = key;
            const std::string ca = src(A, 0).first;
            if (inst.size() == 1)
            {
                std::string idx = "bits(" + ca + ", *" + uint_tuple(inst[0].ia).repr() + ")";
                if (kind == "xor") idx += " ^ bits(" + src(B, 0).first + ", *" + uint_tuple(inst[0].ib).repr() + ")";
                parts.push_back("place(" + name + "[" + idx + "], *" + uint_tuple(inst[0].outs).repr() + ")");
                continue;
            }
            std::vector<Py> ga, go;
            for (const auto& i : inst) { ga.push_back(uint_tuple(i.ia)); go.push_back(uint_tuple(i.outs)); }
            const std::string GA = registry.constant("IN", Py::tuple(ga));
            const std::string OUT = registry.constant("OUT", Py::tuple(go));
            std::string idx = "bits(" + ca + ", *" + GA + "[j])";
            if (kind == "xor")
            {
                std::vector<Py> gb;
                for (const auto& i : inst) gb.push_back(uint_tuple(i.ib));
                const std::string GB = registry.constant("IN", Py::tuple(gb));
                idx += " ^ bits(" + src(B, 0).first + ", *" + GB + "[j])";
            }
            const std::string acc = "u" + std::to_string(out.size());
            out.push_back(ind + acc + " = 0");
            out.push_back(ind + "for j in range(" + std::to_string(inst.size()) + "):  # " + std::to_string(inst.size()) + " instances of " + name);
            out.push_back(ind + "    " + acc + " |= place(" + name + "[" + idx + "], *" + OUT + "[j])");
            parts.push_back(acc);
        }
        // XOR decompositions (lift_tables.cpp): per bit, tables XOR single bits; instances of one table read from one
        // symbol are XOR-accumulated in a loop
        if (!plan.dsd_linear.empty())
        {
            std::vector<std::string> xs;
            std::map<std::pair<std::string, size_t>, std::vector<std::pair<std::string, std::vector<std::pair<unsigned, unsigned>>>>> layers;
            for (const auto& [o, terms, c0] : plan.dsd_linear)
            {
                inv |= BigInt(c0) << o;
                std::map<std::string, size_t> seen;
                for (const auto& [x, k] : terms)
                {
                    const auto [code, b] = src(x, k);
                    const auto layer = std::make_pair(code, seen[code]);
                    seen[code] += 1;
                    by_add(layers[layer], code, o, b);
                }
            }
            for (const auto& [layer, by] : layers) for (const auto& t : routes(by, "X")) xs.push_back(t);
            std::vector<std::pair<std::pair<std::string, std::string>, std::vector<Inst>>> xl;
            for (const auto& t : plan.dsd_tables)
            {
                const auto [name, in_order, out_order] = registry.classify(t.table, static_cast<unsigned>(t.inputs.size()), static_cast<unsigned>(t.outputs.size()));
                std::vector<BitRef> ins;
                for (unsigned q : in_order) ins.push_back(t.inputs[q]);
                std::vector<unsigned> outs;
                for (unsigned q : out_order) outs.push_back(t.outputs[q]);
                std::set<std::string> isym;
                for (const auto& [x, _] : ins) isym.insert(x);
                bool aliased = false;
                for (const auto& s : isym) aliased |= alias.count(s) > 0;
                if (isym.size() == 1 && !aliased)
                {
                    Inst inst;
                    for (const auto& [_, k] : ins) inst.ia.push_back(k);
                    inst.outs = outs;
                    const auto key = std::make_pair(name, *isym.begin());
                    auto it = std::find_if(xl.begin(), xl.end(), [&](const auto& e) { return e.first == key; });
                    if (it == xl.end()) xl.push_back({key, {inst}});
                    else it->second.push_back(inst);
                }
                else xs.push_back("place(" + name + "[" + gather(ins) + "], *" + uint_tuple(outs).repr() + ")");
                stat("dsd_table_bits", t.outputs.size());
                stat("tables", 1);
            }
            for (const auto& [key, inst] : xl)
            {
                const auto& [name, A] = key;
                const std::string ca = src(A, 0).first;
                if (inst.size() == 1)
                {
                    xs.push_back("place(" + name + "[bits(" + ca + ", *" + uint_tuple(inst[0].ia).repr() + ")], *" + uint_tuple(inst[0].outs).repr() + ")");
                    continue;
                }
                std::vector<Py> ga, go;
                for (const auto& i : inst) { ga.push_back(uint_tuple(i.ia)); go.push_back(uint_tuple(i.outs)); }
                const std::string GA = registry.constant("IN", Py::tuple(ga));
                const std::string OUT = registry.constant("OUT", Py::tuple(go));
                const std::string acc = "u" + std::to_string(out.size());
                out.push_back(ind + acc + " = 0");
                out.push_back(ind + "for j in range(" + std::to_string(inst.size()) + "):  # " + std::to_string(inst.size()) + " instances of " + name);
                out.push_back(ind + "    " + acc + " ^= place(" + name + "[bits(" + ca + ", *" + GA + "[j])], *" + OUT + "[j])");
                xs.push_back(acc);
            }
            parts.push_back("(" + join(xs, " ^ ") + ")");
            stat("dsd_bits", plan.dsd_linear.size());
        }
        // parameter parts
        std::vector<std::pair<std::string, std::vector<std::pair<unsigned, unsigned>>>> pby;
        for (const auto& [o, params, g] : plan.param_route)
        {
            if (params.size() == 1 && g == std::vector<int>{0, 1})
            {
                const auto [code, b] = src(params[0].first, params[0].second);
                by_add(pby, code, o, b);
            }
            else
            {
                std::vector<Py> gv;
                for (int x : g) gv.push_back(py(x));
                const std::string pname = registry.constant("P", Py::tuple(gv)); // before gather(): f-string order
                const std::string gp = gather(params);
                xor_terms.push_back("(" + pname + "[" + gp + "] << " + std::to_string(o) + ")");
            }
            stat("param_bits", 1);
        }
        {
            auto r = routes(pby, "R");
            r.insert(r.end(), xor_terms.begin(), xor_terms.end());
            xor_terms = r;
        }
        if (!plan.keep.empty())
        {
            BigInt mask = 0;
            for (unsigned i : plan.keep) mask |= BigInt(1) << i;
            z3::expr mv = expr.ctx().bv_val(mask.str().c_str(), n);
            z3::expr anded = rest & mv;
            z3::expr kexpr = anded.simplify();
            Printer pr(env);
            const std::string code = pr.emit(kexpr);
            for (const auto& ln : pr.lines) out.push_back(ind + ln.substr(4));
            parts.push_back(code);
            stat("kept_bits", plan.keep.size());
            last_kept_bits = plan.keep.size();
        }
        std::string value = parts.empty() ? "0" : join(parts, " | ");
        const BigInt cnst = plan.cnst ^ inv;
        if (cnst != 0) value = "(" + value + ") ^ " + py_hex(cnst);
        if (!xor_terms.empty()) value = "(" + value + ") ^ " + join(xor_terms, " ^ ");
        out.push_back(ind + target + " = " + value);
        return out;
    }

    ProgramLifter::ProgramLifter(ProgramBuilder& dec, const std::string& out_arg) : D(dec), m_out(out_arg)
    {
        for (const auto& [w, d] : D.M.words().items())
        {
            if (d.contains("numeric_order_known") && d.at("numeric_order_known").get<bool>())
            {
                std::vector<unsigned> o(d.at("bits").size());
                for (unsigned i = 0; i < o.size(); ++i) o[i] = i;
                word_order[w] = o;
                order_sources[w] = "HAL";
            }
        }
        D.lift_note = [this](bool chosen, unsigned width) { note_choice(chosen, width); };
        D.lift_hook = [this](const z3::expr& e, const Printer::Env& env, unsigned indent, const IsParam& isp, const std::string& target,
                             const std::map<std::string, std::pair<std::string, unsigned>>& alias, const Json& node_id,
                             const std::string& phase_code, const std::string& writes) {
            return emit_lifted(e, env, indent, isp, target, alias, node_id, phase_code, writes);
        };
        std::map<Key, size_t> in_loop;
        for (size_t li = 0; li < D.loops.size(); ++li)
            for (long long ph : D.loops[li].phases)
                for (const auto& s : D.loops[li].body)
                    for (const auto& name : s.names) in_loop[{name, ph + s.offset}] = li;
        std::vector<std::string> lifted_lines;
        std::set<size_t> emitted;
        for (const auto& key : D.M.order)
        {
            auto it = in_loop.find(key);
            std::vector<std::string> part;
            if (it == in_loop.end())
            {
                const Node& node = D.M.nodes.at(key);
                const long long ph = key.second;
                Printer::Env env = [&](const std::string& sym) -> std::string {
                    const auto [kind, base] = partition_colon(sym);
                    if (kind == "word") return D.source_code(node, sym);
                    if (kind == "input") return "pads[" + tup(base, ph).repr() + "]";
                    throw std::runtime_error("KeyError " + sym);
                };
                auto lifted = emit_lifted(node.expr, env, 4, [](const std::string&) { return false; }, "v[" + key_py(key).repr() + "]", {},
                                          Json::array({key.first, key.second}), std::to_string(ph), key.first);
                auto plain = D.emit_flat_node(key);
                const bool chosen = lifted && lifted->size() <= plain.size();
                note_choice(chosen, node.expr.get_sort().bv_size());
                part = chosen ? *lifted : plain;
            }
            else if (emitted.insert(it->second).second) part = D.emit_loop(D.loops[it->second], true);
            lifted_lines.insert(lifted_lines.end(), part.begin(), part.end());
        }
        std::vector<std::string> tail_lifted{"    outputs = {}"};
        for (const auto& ob : D.M.observations)
        {
            const long long ph = ob.phase;
            Printer::Env env = [&](const std::string& sym) -> std::string {
                const auto [kind, base] = partition_colon(sym);
                if (kind == "word")
                {
                    const auto [p2, d] = D.M.last_write(base, ph);
                    return d == 0 ? "v[" + tup(base, p2).repr() + "]" : "state[" + tup(base, p2, d).repr() + "]";
                }
                if (kind == "input") return "pads[" + tup(base, ph).repr() + "]";
                throw std::runtime_error("KeyError " + sym);
            };
            const std::string target = "outputs[" + tup(ob.port, ob.phase).repr() + "]";
            auto code = emit_lifted(ob.expr, env, 4, [](const std::string&) { return false; }, target, {},
                                    Json::array({"output", ob.port, ob.phase}), std::to_string(ob.phase), "");
            Printer pr(env);
            const std::string pc = pr.emit(ob.expr);
            std::vector<std::string> plain = pr.lines;
            plain.push_back("    " + target + " = " + pc);
            const bool lifted_chosen = code && code->size() <= plain.size();
            note_choice(lifted_chosen, ob.expr.get_sort().bv_size());
            const auto& chosen = lifted_chosen ? *code : plain;
            tail_lifted.insert(tail_lifted.end(), chosen.begin(), chosen.end());
        }
        const auto ns = std::find(D.tail.begin(), D.tail.end(), "    new_state = {}");
        tail_lifted.insert(tail_lifted.end(), ns, D.tail.end());
        const auto cut = std::find(D.header.begin(), D.header.end(), "def transaction(pads, state):");
        std::vector<std::string> all(D.header.begin(), cut);
        const auto reg = registry.lines();
        all.insert(all.end(), reg.begin(), reg.end());
        all.push_back("");
        all.insert(all.end(), cut, D.header.end());
        all.insert(all.end(), lifted_lines.begin(), lifted_lines.end());
        all.insert(all.end(), tail_lifted.begin(), tail_lifted.end());
        program = join(all, "\n") + "\n";

        interface_json = PJ::dict();
        PJ pads = PJ::list();
        for (const auto& [base, ph] : D.M.pad_reads) pads.push(PJ::list({PJ(base), PJ(ph)}));
        interface_json.set("pad_reads", pads);
        PJ sk = PJ::list();
        for (const auto& [w, ph, d] : D.state_keys) sk.push(PJ::list({PJ(w), PJ(ph), PJ(d)}));
        interface_json.set("state_keys", sk);
        PJ obs = PJ::list();
        for (const auto& o : D.M.observations) obs.push(PJ::list({PJ(o.port), PJ(o.phase)}));
        interface_json.set("observations", obs);
    }
}
