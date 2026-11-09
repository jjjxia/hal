// Folded-RTL backend: certified event graph -> cycle-accurate Verilog with recovered word registers, validated
// with Icarus against the original-gate trace.
#include "event_graph.h"
#include "python_printer.h"
#include "python_json.h"
#include <cstdlib>
#include <filesystem>
#include <fstream>
#include <iostream>
#include <sstream>
#include <unordered_map>
#include <unordered_set>
#include <set>
#include <algorithm>
#include <cctype>

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

        std::string hex_noprefix(const BigInt& v) { return py_hex(v).substr(2); }

        struct Rtl
        {
            z3::context ctx;
            Json G;
            long long P = 0, origin = 0;
            int par = 0;
            long long NC = 0;
            std::string out;

            z3::expr parse(const std::string& smt2) { return ctx.parse_string(smt2.c_str())[0].arg(1); }

            const Json& table(const std::string& kind, const std::string& base) const
            {
                return (kind == "word" ? G.at("words") : G.at("inputs")).at(base).at("certified_known_lsb_first");
            }

            z3::expr subst_known(const z3::expr& expr, const std::function<std::string(const std::string&, const std::string&)>& known_of)
            {
                std::vector<std::pair<z3::expr, z3::expr>> subs;
                for (const auto& v : z3_vars(expr))
                {
                    const auto [kind, base] = partition_colon(sym_name(v));
                    if (kind != "word" && kind != "input") continue;
                    const std::string known = known_of(kind, base);
                    const unsigned w = v.get_sort().bv_size();
                    if (known.find('x') == std::string::npos) subs.emplace_back(v, const_bv(ctx, known, w));
                    else if (known.find_first_not_of('x') != std::string::npos) subs.emplace_back(v, masked_known(v, known));
                }
                return substitute(expr, subs).simplify();
            }

            z3::expr specialise(const z3::expr& e, long long phase)
            {
                return subst_known(e, [&](const std::string& kind, const std::string& base) { return table(kind, base).at(phase).get<std::string>(); });
            }

            z3::expr specialise_common(const z3::expr& e, const std::vector<long long>& phases)
            {
                return subst_known(e, [&](const std::string& kind, const std::string& base) {
                    std::vector<std::string> rows;
                    for (long long ph : phases) rows.push_back(table(kind, base).at(ph).get<std::string>());
                    std::string known;
                    for (size_t c = 0; c < rows[0].size(); ++c)
                    {
                        bool same = true;
                        for (const auto& r : rows) same &= r[c] == rows[0][c];
                        known += same ? rows[0][c] : 'x';
                    }
                    return known;
                });
            }
        };

        class VPrinter
        {
        public:
            VPrinter(const std::string& prefix, std::vector<std::string>& decls, const z3::expr& root) : m_prefix(prefix), m_decls(decls)
            {
                std::unordered_set<unsigned> seen;
                std::vector<z3::expr> st{root};
                while (!st.empty())
                {
                    z3::expr x = st.back();
                    st.pop_back();
                    for (unsigned i = 0; i < x.num_args(); ++i) m_refs[x.arg(i).id()] += 1;
                    if (seen.insert(x.id()).second) for (unsigned i = 0; i < x.num_args(); ++i) st.push_back(x.arg(i));
                }
            }

            std::string emit(const z3::expr& e, bool need_name = false)
            {
                if (auto it = m_memo.find(e.id()); it != m_memo.end()) return it->second;
                if (e.num_args() == 0)
                {
                    std::string code = leaf(e);
                    if (need_name && std::isdigit(static_cast<unsigned char>(code[0]))) code = wire(e, code);
                    m_memo[e.id()] = code;
                    return code;
                }
                const Z3_decl_kind k = e.decl().decl_kind();
                const unsigned w = e.is_bool() ? 1 : e.get_sort().bv_size();
                const bool nn = k == Z3_OP_EXTRACT || k == Z3_OP_SIGN_EXT || k == Z3_OP_ROTATE_LEFT || k == Z3_OP_ROTATE_RIGHT;
                std::vector<std::string> a;
                for (unsigned i = 0; i < e.num_args(); ++i) a.push_back(emit(e.arg(i), nn));
                const unsigned cw = e.arg(0).is_bv() ? e.arg(0).get_sort().bv_size() : 1;
                auto param = [&](unsigned i) { return static_cast<unsigned>(Z3_get_decl_int_parameter(e.ctx(), e.decl(), i)); };
                std::string code;
                static const std::map<Z3_decl_kind, std::string> binop = {{Z3_OP_BAND, "&"}, {Z3_OP_BOR, "|"}, {Z3_OP_BXOR, "^"}, {Z3_OP_BADD, "+"},
                    {Z3_OP_BMUL, "*"}, {Z3_OP_AND, "&&"}, {Z3_OP_OR, "||"}, {Z3_OP_XOR, "^"}};
                if (auto it = binop.find(k); it != binop.end()) code = join(a, " " + it->second + " ");
                else if (k == Z3_OP_BSUB) code = a[0] + " - " + a[1];
                else if (k == Z3_OP_BNOT) code = "~" + a[0];
                else if (k == Z3_OP_BNEG) code = "-" + a[0];
                else if (k == Z3_OP_NOT) code = "!" + a[0];
                else if (k == Z3_OP_EXTRACT)
                {
                    const unsigned hi = param(0), lo = param(1);
                    code = hi == lo ? a[0] + "[" + std::to_string(hi) + "]" : a[0] + "[" + std::to_string(hi) + ":" + std::to_string(lo) + "]";
                    if (cw == 1) code = a[0];
                }
                else if (k == Z3_OP_CONCAT) code = "{" + join(a, ", ") + "}";
                else if (k == Z3_OP_ZERO_EXT) code = "{{" + std::to_string(param(0)) + "{1'b0}}, " + a[0] + "}";
                else if (k == Z3_OP_SIGN_EXT) code = "{{" + std::to_string(param(0)) + "{" + a[0] + "[" + std::to_string(cw - 1) + "]}}, " + a[0] + "}";
                else if (k == Z3_OP_ITE) code = a[0] + " ? " + a[1] + " : " + a[2];
                else if (k == Z3_OP_EQ) code = a[0] + " == " + a[1];
                else if (k == Z3_OP_DISTINCT) code = a[0] + " != " + a[1];
                else if (k == Z3_OP_ULT) code = a[0] + " < " + a[1];
                else if (k == Z3_OP_ULEQ) code = a[0] + " <= " + a[1];
                else if (k == Z3_OP_UGT) code = a[0] + " > " + a[1];
                else if (k == Z3_OP_UGEQ) code = a[0] + " >= " + a[1];
                else if (k == Z3_OP_SLT || k == Z3_OP_SLEQ || k == Z3_OP_SGT || k == Z3_OP_SGEQ)
                {
                    const std::string op = k == Z3_OP_SLT ? "<" : k == Z3_OP_SLEQ ? "<=" : k == Z3_OP_SGT ? ">" : ">=";
                    code = "$signed(" + a[0] + ") " + op + " $signed(" + a[1] + ")";
                }
                else if (k == Z3_OP_BSHL) code = a[0] + " << " + a[1];
                else if (k == Z3_OP_BLSHR) code = a[0] + " >> " + a[1];
                else if (k == Z3_OP_BASHR) code = "$signed(" + a[0] + ") >>> " + a[1];
                else if (k == Z3_OP_ROTATE_LEFT || k == Z3_OP_ROTATE_RIGHT)
                {
                    unsigned r = param(0) % w;
                    if (k == Z3_OP_ROTATE_RIGHT) r = (w - r) % w;
                    code = r == 0 ? a[0] : "{" + a[0] + "[" + std::to_string(w - r - 1) + ":0], " + a[0] + "[" + std::to_string(w - 1) + ":" + std::to_string(w - r) + "]}";
                }
                else throw std::runtime_error("operator " + e.decl().name().str());
                if (m_refs[e.id()] <= 1 && !need_name) return "(" + code + ")";
                const std::string name = wire(e, code);
                m_memo[e.id()] = name;
                return name;
            }

        private:
            std::string m_prefix;
            std::vector<std::string>& m_decls;
            std::unordered_map<unsigned, std::string> m_memo;
            std::unordered_map<unsigned, unsigned> m_refs;
            unsigned m_n = 0;

            static std::string leaf(const z3::expr& e)
            {
                if (e.is_true()) return "1'b1";
                if (e.is_false()) return "1'b0";
                if (e.is_app() && e.decl().decl_kind() == Z3_OP_BNUM)
                    return std::to_string(e.get_sort().bv_size()) + "'h" + hex_noprefix(numeral_value(e));
                const auto [kind, base] = partition_colon(sym_name(e));
                return kind == "word" ? "w_" + base : base;
            }

            std::string wire(const z3::expr& e, const std::string& code)
            {
                ++m_n;
                const std::string name = m_prefix + "_" + std::to_string(m_n);
                const unsigned w = e.is_bool() ? 1 : e.get_sort().bv_size();
                m_decls.push_back("wire " + (w == 1 ? std::string() : "[" + std::to_string(w - 1) + ":0] ") + name + " = " + code + ";");
                return name;
            }
        };

        int pad_number(const std::string& s) { return std::stoi(s.substr(3)); }
    }

    int rtl_main(int argc, char** argv)
    {
        if (argc < 5)
        {
            std::cerr << "usage: recover-program rtl GRAPH_JSON OUT_DIR TRACE_DIR [TRACE_DIR ...]\n";
            return 2;
        }
        Rtl R;
        const std::string graph_path = argv[2];
        R.out = argv[3];
        std::vector<std::string> trace_dirs(argv + 4, argv + argc);
        require(!std::filesystem::exists(R.out), "output directory exists: " + R.out);
        std::filesystem::create_directories(R.out);
        R.G = load_json(graph_path);
        const Json& G = R.G;
        R.P = G.at("period").get<long long>();
        R.origin = G.at("origin").get<long long>();
        require(G.at("available").get<bool>() && G.at("certificate").at("certified").get<bool>() && G.at("certificate").at("phases_aligned").get<bool>(),
                "needs an available, certified, aligned graph");
        std::set<long long> parities;
        for (const auto& n : G.at("nodes")) parities.insert(n.at("phase").get<long long>() % 2);
        require(parities.size() == 1 && R.P % 2 == 0, "writes must fall on one edge parity of a two-event clock");
        R.par = static_cast<int>(*parities.begin());
        R.NC = R.P / 2;
        const long long NC = R.NC;
        const int par = R.par;

        // word -> (smt2 -> phases), insertion order
        std::vector<std::pair<std::string, std::vector<std::pair<std::string, std::vector<long long>>>>> raw_by_word;
        for (const auto& n : G.at("nodes"))
        {
            const std::string name = n.at("name").get<std::string>(), smt2 = n.at("smt2").get<std::string>();
            auto wi = std::find_if(raw_by_word.begin(), raw_by_word.end(), [&](const auto& x) { return x.first == name; });
            if (wi == raw_by_word.end()) { raw_by_word.push_back({name, {}}); wi = std::prev(raw_by_word.end()); }
            auto gi = std::find_if(wi->second.begin(), wi->second.end(), [&](const auto& x) { return x.first == smt2; });
            if (gi == wi->second.end()) { wi->second.push_back({smt2, {}}); gi = std::prev(wi->second.end()); }
            gi->second.push_back(n.at("phase").get<long long>());
        }
        std::map<std::string, std::vector<std::pair<std::vector<long long>, z3::expr>>> rules;
        for (const auto& [wname, groups] : raw_by_word)
        {
            std::vector<std::pair<std::vector<long long>, z3::expr>> rs;
            for (const auto& [smt2, phases] : groups)
            {
                std::vector<long long> cyc;
                for (long long ph : phases) cyc.push_back((ph - par) / 2);
                std::sort(cyc.begin(), cyc.end());
                rs.emplace_back(cyc, R.specialise_common(R.parse(smt2), phases));
            }
            rules.emplace(wname, rs);
        }
        std::vector<std::string> written;
        for (const auto& [w, _] : rules) written.push_back(w);
        std::map<std::string, std::tuple<std::string, std::string, unsigned>> bit_owner;
        for (const auto& [name, w] : G.at("words").items())
        {
            unsigned i = 0;
            for (const auto& b : w.at("bits")) bit_owner[b.get<std::string>()] = {"word", name, i++};
        }
        for (const auto& [name, inp] : G.at("inputs").items())
        {
            unsigned i = 0;
            for (const auto& b : inp.at("bits")) bit_owner[b.get<std::string>()] = {"input", name, i++};
        }
        auto leaf_substitute = [&](const z3::expr& expr, long long phase) {
            std::vector<std::pair<z3::expr, z3::expr>> subs;
            for (const auto& v : z3_vars(expr))
            {
                const auto& [kind, name, i] = bit_owner.at(sym_name(v));
                const unsigned width = static_cast<unsigned>((kind == "word" ? G.at("words") : G.at("inputs")).at(name).at("bits").size());
                subs.emplace_back(v, R.ctx.bv_const((kind + ":" + name).c_str(), width).extract(i, i));
            }
            return R.specialise(substitute(expr, subs), phase);
        };
        // port -> [(bit index, physical net, per-cycle exprs)], insertion order
        std::vector<std::pair<std::string, std::vector<std::tuple<unsigned, long long, std::vector<z3::expr>>>>> outputs;
        for (const auto& [port, entries] : G.at("outputs").items())
        {
            unsigned i = 0;
            for (const auto& e : entries)
            {
                const unsigned bit_index = i++;
                if (!e.is_object() || (e.contains("tristate") && e.at("tristate").get<bool>())) continue;
                z3::expr b = R.parse(e.at("smt2").get<std::string>());
                z3::expr bv = b;
                if (b.is_bool())
                {
                    z3::expr one = R.ctx.bv_val(1, 1), zero = R.ctx.bv_val(0, 1);
                    bv = z3::ite(b, one, zero);
                }
                std::vector<z3::expr> per;
                for (long long k = 0; k < NC; ++k) per.push_back(leaf_substitute(bv, 2 * k + par));
                auto pi = std::find_if(outputs.begin(), outputs.end(), [&](const auto& x) { return x.first == port; });
                if (pi == outputs.end()) { outputs.push_back({port, {}}); pi = std::prev(outputs.end()); }
                pi->second.emplace_back(bit_index, e.at("bit").get<long long>(), per);
            }
        }
        auto sorted_outputs = outputs;
        std::sort(sorted_outputs.begin(), sorted_outputs.end(), [](const auto& a, const auto& b) { return a.first < b.first; });

        std::set<std::string> pads_set;
        for (const auto& [_, rs] : rules)
            for (const auto& [__, e] : rs)
                for (const auto& v : z3_vars(e)) { const auto n = sym_name(v); if (n.rfind("input:", 0) == 0) pads_set.insert(partition_colon(n).second); }
        for (const auto& [_, bits] : outputs)
            for (const auto& [__, ___, es] : bits)
                for (const auto& e : es)
                    for (const auto& v : z3_vars(e)) { const auto n = sym_name(v); if (n.rfind("input:", 0) == 0) pads_set.insert(partition_colon(n).second); }
        std::vector<std::string> data_pads(pads_set.begin(), pads_set.end());
        std::stable_sort(data_pads.begin(), data_pads.end(), [](const std::string& a, const std::string& b) { return pad_number(a) < pad_number(b); });

        std::vector<std::string> decls, body;
        unsigned cbits = 0;
        for (long long x = NC - 1; x > 0; x >>= 1) ++cbits;
        cbits = std::max(1u, cbits);
        for (const auto& wname : written)
        {
            body.push_back("  case (cyc)");
            auto rs = rules.at(wname);
            std::stable_sort(rs.begin(), rs.end(), [](const auto& a, const auto& b) { return a.first < b.first; });
            for (size_t j = 0; j < rs.size(); ++j)
            {
                VPrinter vp("u_" + wname + "_" + std::to_string(j), decls, rs[j].second);
                const std::string code = vp.emit(rs[j].second);
                std::vector<std::string> cs;
                for (long long c : rs[j].first) cs.push_back(std::to_string(c));
                body.push_back("    " + join(cs, ", ") + ": w_" + wname + " <= " + code + ";");
            }
            body.push_back("    default: ;\n  endcase");
        }
        std::vector<std::string> out_lines;
        for (const auto& [port, bits] : sorted_outputs)
        {
            unsigned width = 0;
            for (const auto& [i, _, __] : bits) width = std::max(width, i + 1);
            out_lines.push_back("always @(*) begin " + port + " = " + std::to_string(width) + "'h0;");
            for (const auto& [i, _, es] : bits)
            {
                std::vector<std::pair<unsigned, std::pair<z3::expr, std::vector<long long>>>> groups;
                for (size_t k = 0; k < es.size(); ++k)
                {
                    auto gi = std::find_if(groups.begin(), groups.end(), [&](const auto& g) { return g.first == es[k].id(); });
                    if (gi == groups.end()) groups.push_back({es[k].id(), {es[k], {static_cast<long long>(k)}}});
                    else gi->second.second.push_back(static_cast<long long>(k));
                }
                const std::string idx = port + "[" + std::to_string(i) + "]";
                if (groups.size() == 1)
                {
                    VPrinter vp("o_" + port + "_" + std::to_string(i), decls, groups[0].second.first);
                    out_lines.push_back("  " + idx + " = " + vp.emit(groups[0].second.first) + ";");
                    continue;
                }
                out_lines.push_back("  case (cyc)");
                for (size_t j = 0; j < groups.size(); ++j)
                {
                    std::vector<std::string> ks;
                    for (long long k : groups[j].second.second) ks.push_back(std::to_string(k));
                    VPrinter vp("o_" + port + "_" + std::to_string(i) + "_" + std::to_string(j), decls, groups[j].second.first);
                    out_lines.push_back("    " + join(ks, ", ") + ": " + idx + " = " + vp.emit(groups[j].second.first) + ";");
                }
                out_lines.push_back("    default: ;\n  endcase");
            }
            out_lines.push_back("end");
        }
        std::vector<std::string> ports{"input clk"};
        for (const auto& p : data_pads) ports.push_back("input " + p);
        for (const auto& [p, b] : sorted_outputs)
        {
            unsigned mx = 0;
            for (const auto& [i, _, __] : b) mx = std::max(mx, i);
            ports.push_back("output reg [" + std::to_string(mx) + ":0] " + p);
        }
        std::vector<std::string> module{
            "// Recovered folded RTL (recover-program rtl): " + std::to_string(written.size()) + " word registers, cycle counter of " + std::to_string(NC) + " cycles",
            "// (certified schedule, graph period " + std::to_string(R.P) + " events, origin " + std::to_string(R.origin) + "); valid from the origin under the protocol.",
            "module recovered(" + join(ports, ", ") + ");",
            "reg [" + std::to_string(cbits - 1) + ":0] cyc;"};
        for (const auto& w : written) module.push_back("reg [" + std::to_string(G.at("words").at(w).at("bits").size() - 1) + ":0] w_" + w + ";");
        module.insert(module.end(), decls.begin(), decls.end());
        module.insert(module.end(), out_lines.begin(), out_lines.end());
        module.push_back("always @(posedge clk) begin");
        module.push_back("  cyc <= (cyc == " + std::to_string(NC - 1) + ") ? 0 : cyc + 1;");
        module.insert(module.end(), body.begin(), body.end());
        module.push_back("end");
        module.push_back("endmodule");
        const std::string verilog = join(module, "\n") + "\n";
        {
            std::ofstream f(R.out + "/recovered.v", std::ios::binary);
            f << verilog;
        }

        // ---- validation with Icarus
        auto validate = [&](const std::string& directory, const std::string& tag) {
            std::ifstream mf(directory + "/native_trace.json");
            const Json meta = Json::parse(mf);
            std::map<long long, size_t> pos;
            size_t pi = 0;
            for (const auto& b : meta.at("observed")) pos[b.get<long long>()] = pi++;
            std::vector<std::pair<std::string, std::string>> rows;
            {
                std::ifstream tf(directory + "/native_trace.txt");
                std::string line;
                while (std::getline(tf, line)) { std::istringstream ss(line); std::string a, b; ss >> a >> b; rows.emplace_back(a, b); }
            }
            auto bit = [&](long long b, long long t, bool post) -> int {
                const std::string& r = post ? rows.at(static_cast<size_t>(t)).second : rows.at(static_cast<size_t>(t)).first;
                const char c = r.at(r.size() - 1 - pos.at(b));
                return c == '0' ? 0 : c == '1' ? 1 : -1;
            };
            auto wordval = [&](const std::string& name, long long t, bool post) -> std::optional<BigInt> {
                BigInt v = 0;
                unsigned i = 0;
                for (const auto& b : G.at("words").at(name).at("bits"))
                {
                    const int x = bit(std::stoll(b.get<std::string>().substr(1)), t, post);
                    if (x < 0) return std::nullopt;
                    v |= BigInt(x) << i++;
                }
                return v;
            };
            const long long cycles = (static_cast<long long>(rows.size()) - 1 - R.origin) / 2;
            const std::string w = R.out + "/" + tag;
            std::filesystem::create_directories(w);
            std::map<std::string, std::optional<BigInt>> init;
            for (const auto& n : written) init[n] = wordval(n, R.origin + par, false);
            {
                std::ofstream sf(w + "/stim.txt");
                for (long long k = 0; k < cycles; ++k)
                {
                    const long long e = R.origin + 2 * k + par;
                    std::string s;
                    for (auto it = data_pads.rbegin(); it != data_pads.rend(); ++it)
                    {
                        const int x = bit(std::stoll(G.at("inputs").at(*it).at("bits").at(0).get<std::string>().substr(1)), e, false);
                        s += x == 1 ? '1' : '0';
                    }
                    sf << (s.empty() ? "0" : s) << (k + 1 < cycles ? "\n" : "\n");
                }
            }
            std::vector<std::string> tb{"`timescale 1ns/1ps", "module tb;", "reg clk = 0;"};
            for (const auto& p : data_pads) tb.push_back("reg " + p + " = 0;");
            std::vector<std::string> conn{".clk(clk)"};
            for (const auto& [p, b] : sorted_outputs)
            {
                unsigned mx = 0;
                for (const auto& [i, _, __] : b) mx = std::max(mx, i);
                tb.push_back("wire [" + std::to_string(mx) + ":0] " + p + ";");
            }
            for (const auto& p : data_pads) conn.push_back("." + p + "(" + p + ")");
            for (const auto& [p, _] : sorted_outputs) conn.push_back("." + p + "(" + p + ")");
            tb.push_back("recovered dut(" + join(conn, ", ") + ");");
            tb.push_back("reg [" + std::to_string(std::max<size_t>(1, data_pads.size()) - 1) + ":0] s [0:" + std::to_string(cycles - 1) + "];");
            tb.push_back("integer k, fd;");
            tb.push_back("initial begin");
            tb.push_back("$readmemb(\"" + w + "/stim.txt\", s); fd = $fopen(\"" + w + "/sim.txt\", \"w\"); dut.cyc = 0;");
            for (const auto& n : written)
                tb.push_back("dut.w_" + n + " = " + std::to_string(G.at("words").at(n).at("bits").size()) + "'d" + (init[n] ? init[n]->str() : "0") + ";");
            tb.push_back("for (k = 0; k < " + std::to_string(cycles) + "; k = k + 1) begin");
            for (size_t j = 0; j < data_pads.size(); ++j) tb.push_back("  " + data_pads[j] + " = s[k][" + std::to_string(j) + "];");
            std::string ofmt = "  $fwrite(fd, \"O", oargs;
            for (const auto& [p, _] : sorted_outputs) { ofmt += " %b"; oargs += ", " + p; }
            std::string rfmt = "  $fwrite(fd, \"R", rargs;
            for (const auto& n : written) { rfmt += " %b"; rargs += ", dut.w_" + n; }
            tb.push_back("  #1;");
            tb.push_back(ofmt + "\\n\"" + oargs + ");");
            tb.push_back("  clk = 1; #1;");
            tb.push_back(rfmt + "\\n\"" + rargs + ");");
            for (const char* l : {"  clk = 0; #1;", "end", "$fclose(fd); $finish;", "end", "endmodule"}) tb.push_back(l);
            {
                std::ofstream tf(w + "/tb.v");
                tf << join(tb, "\n") + "\n";
            }
            const std::string cmd1 = "iverilog -g2012 -o '" + w + "/sim.vvp' '" + w + "/tb.v' '" + R.out + "/recovered.v' > '" + w + "/iverilog.log' 2>&1";
            require(std::system(cmd1.c_str()) == 0, "iverilog failed (see " + w + "/iverilog.log)");
            const std::string cmd2 = "timeout 3600 vvp -n '" + w + "/sim.vvp' > '" + w + "/vvp.log' 2>&1";
            require(std::system(cmd2.c_str()) == 0, "vvp failed (see " + w + "/vvp.log)");
            std::vector<std::vector<std::string>> o_lines, r_lines;
            {
                std::ifstream sf(w + "/sim.txt");
                std::string line;
                while (std::getline(sf, line))
                {
                    std::istringstream ss(line);
                    std::string tag_, tok;
                    ss >> tag_;
                    std::vector<std::string> toks;
                    while (ss >> tok) toks.push_back(tok);
                    if (line.rfind("O", 0) == 0) o_lines.push_back(toks);
                    else if (line.rfind("R", 0) == 0) r_lines.push_back(toks);
                }
            }
            std::vector<std::pair<std::string, long long>> res;
            auto inc = [&](const std::string& k, long long by = 1) { for (auto& [kk, v] : res) if (kk == k) { v += by; return; } res.emplace_back(k, by); };
            PJ first;
            long long xinit = 0;
            for (const auto& [_, v] : init) xinit += !v;
            if (xinit) inc("x_initial_words", xinit);
            for (long long k = 0; k < cycles; ++k)
            {
                const long long e = R.origin + 2 * k + par;
                for (size_t q = 0; q < written.size() && q < r_lines.at(static_cast<size_t>(k)).size(); ++q)
                {
                    const std::string& val = r_lines[static_cast<size_t>(k)][q];
                    const auto exp = wordval(written[q], e, true);
                    if (!exp || val.find_first_of("xz") != std::string::npos) { inc("reg_unknown"); continue; }
                    inc("reg_compared");
                    const BigInt got = from_binary(val);
                    if (got != *exp)
                    {
                        inc("reg_mismatch");
                        if (first.kind == PJ::Kind::null)
                        {
                            first = PJ::dict();
                            first.set("cycle", k); first.set("word", written[q]); first.set("rtl", got); first.set("trace", *exp);
                        }
                    }
                }
                for (size_t q = 0; q < sorted_outputs.size() && q < o_lines.at(static_cast<size_t>(k)).size(); ++q)
                {
                    const std::string& val = o_lines[static_cast<size_t>(k)][q];
                    for (const auto& [i, net, _] : sorted_outputs[q].second)
                    {
                        const int exp = bit(net, e, false);
                        const char c = val.at(val.size() - 1 - i);
                        if (exp < 0 || (c != '0' && c != '1')) { inc("out_unknown"); continue; }
                        inc("out_compared");
                        if ((c - '0') != exp)
                        {
                            inc("out_mismatch");
                            if (first.kind == PJ::Kind::null)
                            {
                                first = PJ::dict();
                                first.set("cycle", k); first.set("port", sorted_outputs[q].first); first.set("bit", i);
                                first.set("rtl", std::string(1, c)); first.set("trace", exp);
                            }
                        }
                    }
                }
            }
            PJ r = PJ::dict();
            r.set("trace", directory);
            r.set("cycles", cycles);
            PJ rs = PJ::dict();
            for (const auto& [k, v] : res) rs.set(k, v);
            r.set("results", rs);
            r.set("first_mismatch", first);
            return r;
        };

        PJ report = PJ::dict();
        report.set("graph", graph_path);
        report.set("period_events", R.P);
        report.set("cycles_per_period", NC);
        report.set("origin", R.origin);
        report.set("word_registers", static_cast<unsigned long long>(written.size()));
        report.set("data_inputs", static_cast<unsigned long long>(data_pads.size()));
        PJ outs = PJ::dict();
        for (const auto& [p, b] : outputs) outs.set(p, static_cast<unsigned long long>(b.size()));
        report.set("outputs", outs);
        report.set("verilog_lines", static_cast<long long>(std::count(verilog.begin(), verilog.end(), '\n')));
        PJ vals = PJ::list();
        for (size_t i = 0; i < trace_dirs.size(); ++i) vals.push(validate(trace_dirs[i], "sim" + std::to_string(i)));
        report.set("validation", vals);
        {
            std::ofstream f(R.out + "/report.json", std::ios::binary);
            f << report.dump(1);
        }
        std::cout << report.dump(1) << '\n';
        return 0;
    }
}
