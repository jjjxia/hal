// End-to-end translation validation of recovered programs against the certified periodic equations
// (port of program_recovery_python/close_chain.py). The reference is the certified word equations specialised with the
// certificate's per-phase constants, executed with register semantics over one protocol transaction;
// the graph's producer resolution ("sources") is not used. The program text is executed symbolically.
#include "event_graph.h"
#include "python_interpreter.h"
#include "python_json.h"
#include <chrono>
#include <cmath>
#include <fstream>
#include <iostream>
#include <set>

namespace program_recovery
{
    namespace
    {
        Json load_json(const std::string& path)
        {
            std::ifstream in(path);
            require(bool(in), "cannot read " + path);
            return Json::parse(in);
        }

        std::string dump_compact(const PJ& v)
        {
            // json.dumps(obj) without indent: separators ", " and ": "
            std::string s = v.dump(0);
            std::string out;
            bool in_str = false;
            for (size_t i = 0; i < s.size(); ++i)
            {
                const char c = s[i];
                if (in_str) { out += c; if (c == '\\') { out += s[++i]; } else if (c == '"') in_str = false; continue; }
                if (c == '"') { in_str = true; out += c; continue; }
                if (c == '\n') { if (!out.empty() && out.back() == ',') out += ' '; continue; }
                out += c;
            }
            return out;
        }

        double round1(double x) { return std::round(x * 10.0) / 10.0; }

        struct Checker
        {
            z3::context ctx;
            Json G, contract, observe;
            long long P = 0, origin = 0, shift = 0;
            std::map<std::string, unsigned> width, in_width;
            std::map<std::string, std::tuple<std::string, std::string, unsigned>> bit_owner;
            std::map<long long, std::vector<std::pair<std::string, z3::expr>>> upd;   // phase -> (word, eq), insertion order
            std::map<std::string, long long> last_phase;
            std::map<long long, std::map<std::string, z3::expr>> reg_at;
            std::vector<std::pair<std::pair<std::string, long long>, z3::expr>> ref_node; // insertion order
            std::map<std::pair<std::string, long long>, size_t> ref_index;
            std::vector<std::pair<std::pair<std::string, long long>, z3::expr>> ref_out;
            std::map<std::string, std::pair<z3::expr, z3::expr>> cut_def;
            std::map<std::string, std::vector<Json>> ports;

            z3::expr parse(const std::string& smt2)
            {
                z3::expr_vector a = ctx.parse_string(smt2.c_str());
                z3::expr eq = a[0];
                require(eq.is_app() && eq.decl().decl_kind() == Z3_OP_EQ && sym_name(eq.arg(0)) == "__out", "unexpected equation form");
                return eq.arg(1);
            }

            z3::expr specialise(const z3::expr& expr, long long phase)
            {
                std::vector<std::pair<z3::expr, z3::expr>> subs;
                for (const auto& v : z3_vars(expr))
                {
                    const auto [kind, base] = partition_colon(sym_name(v));
                    std::string known;
                    if (kind == "word") known = G.at("words").at(base).at("certified_known_lsb_first").at(phase).get<std::string>();
                    else if (kind == "input") known = G.at("inputs").at(base).at("certified_known_lsb_first").at(phase).get<std::string>();
                    else continue;
                    const unsigned w = v.get_sort().bv_size();
                    if (known.find('x') == std::string::npos) subs.emplace_back(v, const_bv(ctx, known, w));
                    else if (known.find_first_not_of('x') != std::string::npos) subs.emplace_back(v, masked_known(v, known));
                }
                return substitute(expr, subs).simplify();
            }

            z3::expr cut(const std::string& w, long long ph) { return ctx.bv_const(("c|" + w + "|" + std::to_string(ph)).c_str(), width.at(w)); }
            z3::expr pad(const std::string& n, long long ph) { return ctx.bv_const(("p|" + n + "|" + std::to_string(ph)).c_str(), in_width.at(n)); }
            z3::expr st(const std::string& w) { return ctx.bv_const(("s|" + w).c_str(), width.at(w)); }

            z3::expr observation(const std::string& port_name, long long ph)
            {
                std::vector<z3::expr> bits;
                for (const auto& p : ports.at(port_name))
                {
                    const Json& spec = G.at("outputs").at(p.at("anonymous_port").get<std::string>()).at(0);
                    if (spec.is_number_integer()) { bits.push_back(ctx.bv_val(spec.get<long long>() == -2 ? 1 : 0, 1)); continue; }
                    z3::expr b = parse(spec.at("smt2").get<std::string>());
                    std::vector<std::pair<z3::expr, z3::expr>> subs;
                    for (const auto& v : z3_vars(b))
                    {
                        const auto& [kind, base, i] = bit_owner.at(sym_name(v));
                        z3::expr sym = ctx.bv_const((kind + ":" + base).c_str(), kind == "word" ? width.at(base) : in_width.at(base));
                        subs.emplace_back(v, sym.extract(i, i));
                    }
                    b = specialise(substitute(b, subs), ((ph + shift) % P + P) % P);
                    std::vector<std::pair<z3::expr, z3::expr>> subs2;
                    for (const auto& v : z3_vars(b))
                    {
                        const std::string n = sym_name(v);
                        const auto [kind, base] = partition_colon(n);
                        subs2.emplace_back(v, n.rfind("word:", 0) == 0 ? reg_at.at(ph).at(base) : pad(base, ph));
                    }
                    if (!subs2.empty()) b = substitute(b, subs2);
                    if (b.is_bool())
                    {
                        z3::expr one = ctx.bv_val(1, 1), zero = ctx.bv_val(0, 1);
                        bits.push_back(z3::ite(b, one, zero));
                    }
                    else bits.push_back(b);
                }
                if (bits.size() > 1) return concat_all(std::vector<z3::expr>(bits.rbegin(), bits.rend())).simplify();
                return bits[0].simplify();
            }

            Checker(const std::string& graph_path, const std::string& contract_path, const std::string& observe_path, const std::string& case_name)
            {
                G = load_json(graph_path);
                contract = load_json(contract_path);
                observe = load_json(observe_path).at(case_name);
                P = G.at("period").get<long long>();
                origin = G.at("origin").get<long long>();
                require(G.at("available").get<bool>() && G.at("certificate").at("certified").get<bool>() && G.at("certificate").at("phases_aligned").get<bool>(),
                        "needs an available, certified, aligned graph");
                require(G.at("memories").empty(), "RAM-backed graphs are not handled");
                for (const auto& [n, w] : G.at("words").items())
                {
                    width[n] = static_cast<unsigned>(w.at("bits").size());
                    unsigned i = 0;
                    for (const auto& b : w.at("bits")) bit_owner[b.get<std::string>()] = {"word", n, i++};
                }
                for (const auto& [n, inp] : G.at("inputs").items())
                {
                    in_width[n] = static_cast<unsigned>(inp.at("bits").size());
                    unsigned i = 0;
                    for (const auto& b : inp.at("bits")) bit_owner[b.get<std::string>()] = {"input", n, i++};
                }
                const long long first = contract.at("transactions").at(0).at("first_event").get<long long>();
                require(2 * contract.at("period_cycles").get<long long>() == P, "graph period differs from the transaction length");
                shift = ((first - origin) % P + P) % P;
                for (const auto& n : G.at("nodes"))
                {
                    const long long ph = n.at("phase").get<long long>();
                    upd[((ph - shift) % P + P) % P].emplace_back(n.at("name").get<std::string>(), specialise(parse(n.at("smt2").get<std::string>()), ph));
                }
                for (const auto& [ph, ws] : upd) for (const auto& [w, _] : ws) last_phase[w] = ph;
                std::map<std::string, z3::expr> reg;
                for (const auto& [w, _] : G.at("words").items()) reg.insert_or_assign(w, st(w));
                for (long long ph = 0; ph <= P; ++ph)
                {
                    reg_at.insert_or_assign(ph, reg);
                    if (ph == P) break;
                    auto it = upd.find(ph);
                    if (it == upd.end()) continue;
                    for (const auto& [w, e] : it->second)
                    {
                        std::vector<std::pair<z3::expr, z3::expr>> subs;
                        for (const auto& v : z3_vars(e))
                        {
                            const auto [kind, base] = partition_colon(sym_name(v));
                            subs.emplace_back(v, kind == "word" ? reg.at(base) : pad(base, ph));
                        }
                        ref_index[{w, ph}] = ref_node.size();
                        ref_node.push_back({{w, ph}, subs.empty() ? e : substitute(e, subs)});
                    }
                    for (const auto& [w, _] : it->second) reg.insert_or_assign(w, cut(w, ph));
                }
                for (const auto& p : contract.at("port_bits"))
                    if (p.at("direction").get<std::string>() == "output") ports[p.at("name").get<std::string>()].push_back(p);
                for (auto& [n, v] : ports)
                    std::stable_sort(v.begin(), v.end(), [](const Json& a, const Json& b) { return a.at("offset").get<long long>() < b.at("offset").get<long long>(); });
                for (const auto& ob : observe)
                {
                    std::vector<long long> phases;
                    if (ob.contains("cycle")) phases.push_back(2 * ob.at("cycle").get<long long>());
                    else
                        for (long long ph = 0; ph < P; ++ph)
                        {
                            z3::expr s = observation(ob.at("strobe").get<std::string>(), ph);
                            require(s.is_app() && s.decl().decl_kind() == Z3_OP_BNUM, "strobe not certified constant");
                            if (std::string(Z3_get_numeral_string(ctx, s)) == "1") phases.push_back(ph);
                        }
                    for (long long ph : phases) ref_out.push_back({{ob.at("port").get<std::string>(), ph}, observation(ob.at("port").get<std::string>(), ph)});
                }
                for (const auto& [k, e] : ref_node) cut_def.insert_or_assign(sym_name(cut(k.first, k.second)), std::make_pair(cut(k.first, k.second), e));
            }

            std::set<std::string> cut_symbols(const std::vector<z3::expr>& terms)
            {
                std::set<std::string> out;
                for (const auto& t : terms) for (const auto& v : z3_vars(t)) { const auto n = sym_name(v); if (n.rfind("c|", 0) == 0) out.insert(n); }
                return out;
            }

            std::pair<std::string, PJ> prove(const z3::expr& lhs, const z3::expr& rhs, unsigned timeout_ms, int max_depth = 4)
            {
                z3::expr eq = (lhs == rhs).simplify();
                if (eq.is_true()) return {"proved", PJ(0)};
                std::set<std::string> assumed, frontier = cut_symbols({eq});
                for (int depth = 0; depth <= max_depth; ++depth)
                {
                    z3::solver s(ctx, "QF_BV");
                    z3::params p(ctx);
                    p.set("timeout", timeout_ms);
                    s.set(p);
                    for (const auto& name : assumed) { const auto& [c, e] = cut_def.at(name); s.add(c == e); }
                    s.add(!eq);
                    const z3::check_result r = s.check();
                    if (r == z3::unsat) return {"proved", PJ(depth)};
                    if (r == z3::unknown) return {"unknown", PJ(s.reason_unknown())};
                    std::set<std::string> nw;
                    for (const auto& n : frontier) if (cut_def.count(n) && !assumed.count(n)) nw.insert(n);
                    if (nw.empty())
                    {
                        std::ostringstream ss;
                        ss << s.get_model();
                        return {"refuted", PJ(ss.str().substr(0, 400))};
                    }
                    assumed.insert(nw.begin(), nw.end());
                    std::vector<z3::expr> defs;
                    for (const auto& n : nw) defs.push_back(cut_def.at(n).second);
                    frontier = cut_symbols(defs);
                }
                return {"refuted_at_depth", PJ(max_depth)};
            }

            PJ check_program(const std::string& path, unsigned timeout_ms = 120000)
            {
                using namespace interp;
                std::ifstream in(path, std::ios::binary);
                require(bool(in), "cannot read " + path);
                std::string src((std::istreambuf_iterator<char>(in)), std::istreambuf_iterator<char>());
                const Module mod = parse_module(src);
                BigInt maxw = 1;
                for (const auto& [_, w] : width) if (BigInt(w) > maxw) maxw = w;
                for (const auto& [_, w] : in_width) if (BigInt(w) > maxw) maxw = w;
                if (mod.max_literal_bits > maxw) maxw = mod.max_literal_bits;
                const unsigned W = static_cast<unsigned>(2 * maxw + 8);
                SymbolicContext S;
                S.ctx = &ctx;
                S.W = W;
                std::vector<std::pair<std::pair<std::string, long long>, z3::expr>> rec;
                std::map<std::pair<std::string, long long>, size_t> rec_index;
                std::set<std::tuple<std::string, long long, long long>> reads;
                Value vals;
                vals.k = VK::dict;
                vals.dict = std::make_shared<DictObj>();
                vals.dict->kind = DictKind::values;
                auto key2 = [](const Value& key) { return std::make_pair((*key.items)[0].s, static_cast<long long>((*key.items)[1].i)); };
                S.pad_missing = [&](const Value& key) {
                    const auto [name, ph] = key2(key);
                    Value v;
                    v.k = VK::sym;
                    v.sym = std::make_shared<SymBox>(SymBox{z3::zext(pad(name, ph), W - in_width.at(name))});
                    return v;
                };
                S.state_missing = [&](const Value& key) {
                    const std::string w = (*key.items)[0].s;
                    reads.insert({w, static_cast<long long>((*key.items)[1].i), static_cast<long long>((*key.items)[2].i)});
                    Value v;
                    v.k = VK::sym;
                    v.sym = std::make_shared<SymBox>(SymBox{z3::zext(st(w), W - width.at(w))});
                    return v;
                };
                Interpreter I(mod, &S);
                S.record_write = [&](const Value& key, Value& val) {
                    const auto k = key2(key);
                    require(!rec_index.count(k), "double write");
                    rec_index[k] = rec.size();
                    rec.push_back({k, val.k == VK::sym ? val.sym->t : I.bv(val)});
                    Value c;
                    c.k = VK::sym;
                    c.sym = std::make_shared<SymBox>(SymBox{z3::zext(cut(k.first, k.second), W - width.at(k.first))});
                    if (val.k == VK::sym) val.sym->t = c.sym->t;
                    vals.dict->set(key, c);
                };
                Value pads, state;
                pads.k = VK::dict; pads.dict = std::make_shared<DictObj>(); pads.dict->kind = DictKind::pads;
                state.k = VK::dict; state.dict = std::make_shared<DictObj>(); state.dict->kind = DictKind::state;
                const auto t0 = std::chrono::steady_clock::now();
                const auto [outputs, new_state] = I.transaction(pads, state, vals);
                const double t_exec = std::chrono::duration<double>(std::chrono::steady_clock::now() - t0).count();
                std::vector<std::pair<std::string, long long>> res;
                auto inc = [&](const std::string& k) { for (auto& [kk, v] : res) if (kk == k) { ++v; return; } res.emplace_back(k, 1); };
                std::vector<std::tuple<std::string, PJ, PJ>> fails;
                auto keypj = [](const std::pair<std::string, long long>& k) { return PJ::list({PJ(k.first), PJ(k.second)}); };
                for (const auto& [k, _] : rec)
                    if (!ref_index.count(k)) { inc("node_not_in_reference"); fails.emplace_back("node_not_in_reference", keypj(k), PJ()); }
                const auto t1 = std::chrono::steady_clock::now();
                for (const auto& [k, term] : rec)
                {
                    auto it = ref_index.find(k);
                    if (it == ref_index.end()) continue;
                    const unsigned w = width.at(k.first);
                    const auto [r, why] = prove(term, z3::zext(ref_node[it->second].second, W - w), timeout_ms);
                    inc("node_" + r);
                    if (r == "proved") inc("node_depth_" + why.i.str());
                    else fails.emplace_back("node_" + r, keypj(k), why);
                }
                std::map<std::string, Value> out_map;
                for (const auto& [key, val] : outputs.dict->entries) out_map[key.key()] = val;
                for (const auto& [k, ref] : ref_out)
                {
                    const Value key = Value::tuple({Value::string(k.first), Value::integer(k.second)});
                    auto it = out_map.find(key.key());
                    if (it == out_map.end()) { inc("output_missing"); fails.emplace_back("output_missing", keypj(k), PJ()); continue; }
                    const auto [r, why] = prove(I.bv(it->second), z3::zext(ref, W - ref.get_sort().bv_size()), timeout_ms);
                    inc("output_" + r);
                    if (r != "proved") fails.emplace_back("output_" + r, keypj(k), why);
                }
                std::set<std::string> ref_out_keys;
                for (const auto& [k, _] : ref_out) ref_out_keys.insert(Value::tuple({Value::string(k.first), Value::integer(k.second)}).key());
                for (const auto& [key, _] : outputs.dict->entries)
                    if (!ref_out_keys.count(key.key()))
                    {
                        inc("output_not_in_reference");
                        fails.emplace_back("output_not_in_reference", PJ::list({PJ((*key.items)[0].s), PJ((*key.items)[1].i)}), PJ());
                    }
                std::set<std::tuple<std::string, long long, long long>> out_state;
                for (const auto& [key, val] : new_state.dict->entries)
                {
                    const std::string w = (*key.items)[0].s;
                    const long long ph = static_cast<long long>((*key.items)[1].i), d = static_cast<long long>((*key.items)[2].i);
                    out_state.insert({w, ph, d});
                    bool ok = d == 1 && last_phase.count(w) && last_phase.at(w) == ph && rec_index.count({w, ph});
                    if (ok)
                    {
                        const auto [r, why] = prove(I.bv(val), z3::zext(cut(w, ph), W - width.at(w)), timeout_ms);
                        ok = r == "proved";
                    }
                    inc(ok ? "state_out_ok" : "state_out_bad");
                    if (!ok) fails.emplace_back("state_out_bad", PJ::list({PJ(w), PJ(ph), PJ(d)}), PJ());
                }
                for (const auto& [w, ph, d] : reads)
                {
                    const bool ok = d == 1 && last_phase.count(w) && last_phase.at(w) == ph;
                    inc(ok ? "state_in_ok" : "state_in_bad");
                    if (!ok) fails.emplace_back("state_in_bad", PJ::list({PJ(w), PJ(ph), PJ(d)}), PJ());
                }
                if (reads != out_state)
                {
                    inc("state_interface_mismatch");
                    PJ diff = PJ::list();
                    std::vector<std::tuple<std::string, long long, long long>> sym;
                    std::set_symmetric_difference(reads.begin(), reads.end(), out_state.begin(), out_state.end(), std::back_inserter(sym));
                    for (size_t i = 0; i < sym.size() && i < 5; ++i) diff.push(PJ::list({PJ(std::get<0>(sym[i])), PJ(std::get<1>(sym[i])), PJ(std::get<2>(sym[i]))}));
                    fails.emplace_back("state_interface_mismatch", diff, PJ());
                }
                const double t_prove = std::chrono::duration<double>(std::chrono::steady_clock::now() - t1).count();
                PJ r = PJ::dict();
                r.set("program", path);
                r.set("width", W);
                r.set("exec_s", round1(t_exec));
                r.set("prove_s", round1(t_prove));
                r.set("writes", static_cast<unsigned long long>(rec.size()));
                r.set("reference_nodes", static_cast<unsigned long long>(ref_node.size()));
                r.set("outputs", static_cast<unsigned long long>(ref_out.size()));
                PJ rs = PJ::dict();
                for (const auto& [k, v] : res) rs.set(k, v);
                r.set("results", rs);
                PJ fl = PJ::list();
                for (size_t i = 0; i < fails.size() && i < 20; ++i)
                    fl.push(PJ::list({PJ(std::get<0>(fails[i])), std::get<1>(fails[i]), std::get<2>(fails[i])}));
                r.set("failures", fl);
                r.set("closed", fails.empty());
                if (!I.helper_problems.empty())
                {
                    PJ hp = PJ::list();
                    for (const auto& h : I.helper_problems) hp.push(h);
                    r.set("helper_problems", hp);
                    r.set("closed", false);
                }
                return r;
            }
        };
    }

    int check_main(int argc, char** argv)
    {
        if (argc < 8)
        {
            std::cerr << "usage: recover-program check GRAPH_JSON CONTRACT_JSON OBSERVE_JSON CASE OUT_JSON PROGRAM_PY [PROGRAM_PY ...]\n";
            return 2;
        }
        const std::string graph_path = argv[2], contract_path = argv[3], observe_path = argv[4], case_name = argv[5], out_path = argv[6];
        Checker C(graph_path, contract_path, observe_path, case_name);
        PJ report = PJ::dict();
        report.set("case", case_name);
        report.set("graph", graph_path);
        report.set("period_events", C.P);
        report.set("transaction_shift", C.shift);
        PJ programs = PJ::list();
        for (int i = 7; i < argc; ++i)
        {
            PJ r = C.check_program(argv[i]);
            programs.push(r);
            PJ line = PJ::dict();
            for (const char* k : {"program", "writes", "outputs", "results", "closed", "exec_s", "prove_s"}) line.set(k, *r.get(k));
            std::cout << dump_compact(line) << std::endl;
        }
        report.set("programs", programs);
        std::ofstream out(out_path, std::ios::binary);
        out << report.dump(1);
        require(bool(out), "write failed " + out_path);
        return 0;
    }
}
