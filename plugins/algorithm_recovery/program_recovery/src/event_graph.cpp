#include "event_graph.h"
#include <algorithm>
#include <sodium.h>
#include <unordered_set>

namespace program_recovery
{
    std::string sym_name(const z3::expr& e) { return e.decl().name().str(); }

    std::vector<z3::expr> z3_vars(const z3::expr& expr)
    {
        // Same traversal as decompile.py: DFS with an explicit stack, children pushed in order.
        std::unordered_set<unsigned> seen;
        std::vector<z3::expr> out, stack{expr};
        while (!stack.empty())
        {
            z3::expr e = stack.back();
            stack.pop_back();
            if (!seen.insert(e.id()).second) continue;
            if (e.is_app())
            {
                if (e.num_args() == 0 && e.decl().decl_kind() == Z3_OP_UNINTERPRETED) out.push_back(e);
                for (unsigned i = 0; i < e.num_args(); ++i) stack.push_back(e.arg(i));
            }
        }
        return out;
    }

    z3::expr const_bv(z3::context& ctx, const std::string& bits_lsb, unsigned w)
    {
        const std::string msb(bits_lsb.rbegin(), bits_lsb.rend());
        return ctx.bv_val(from_binary(msb).str().c_str(), w);
    }

    z3::expr substitute(const z3::expr& e, const std::vector<std::pair<z3::expr, z3::expr>>& subs)
    {
        if (subs.empty()) return e;
        z3::expr_vector from(e.ctx()), to(e.ctx());
        for (const auto& [a, b] : subs) { from.push_back(a); to.push_back(b); }
        z3::expr copy = e;
        return copy.substitute(from, to);
    }

    z3::expr concat_all(const std::vector<z3::expr>& parts)
    {
        require(!parts.empty(), "empty concat");
        z3::expr r = parts[0];
        for (size_t i = 1; i < parts.size(); ++i)
        {
            Z3_ast c = Z3_mk_concat(r.ctx(), r, parts[i]);
            r.ctx().check_error();
            r = z3::expr(r.ctx(), c);
        }
        return r;
    }

    std::string sha256_hex(const std::string& text)
    {
        require(sodium_init() >= 0, "libsodium initialisation failed");
        unsigned char digest[crypto_hash_sha256_BYTES];
        char hex[crypto_hash_sha256_BYTES * 2 + 1];
        crypto_hash_sha256(digest, reinterpret_cast<const unsigned char*>(text.data()), text.size());
        sodium_bin2hex(hex, sizeof(hex), digest, sizeof(digest));
        return hex;
    }

    z3::expr EventGraph::parse(const std::string& smt2) const
    {
        z3::expr_vector a = ctx.parse_string(smt2.c_str());
        z3::expr eq = a[0];
        require(eq.is_app() && eq.decl().decl_kind() == Z3_OP_EQ && sym_name(eq.arg(0)) == "__out", "unexpected equation form");
        return eq.arg(1);
    }

    z3::expr EventGraph::specialise(const z3::expr& expr, long long phase) const
    {
        std::vector<std::pair<z3::expr, z3::expr>> subs;
        for (const auto& v : z3_vars(expr))
        {
            const auto [kind, base] = partition_colon(sym_name(v));
            std::string known;
            if (kind == "word") known = words().at(base).at("certified_known_lsb_first").at(phase).get<std::string>();
            else if (kind == "input") known = inputs().at(base).at("certified_known_lsb_first").at(phase).get<std::string>();
            else continue;
            const unsigned w = v.get_sort().bv_size();
            if (known.find('x') == std::string::npos) subs.emplace_back(v, const_bv(ctx, known, w));
            else if (known.find_first_not_of('x') != std::string::npos) subs.emplace_back(v, masked_known(v, known));
        }
        return substitute(expr, subs).simplify();
    }

    z3::expr masked_known(const z3::expr& v, const std::string& known)
    {
        // Python: mask = const_bv(..); val = const_bv(..); (v & ~mask) | val -- AST creation order matters,
        // because simplify orders the arguments of commutative operators by AST id.
        const unsigned w = v.get_sort().bv_size();
        std::string m, val;
        for (char c : known) { m += c != 'x' ? '1' : '0'; val += c != 'x' ? c : '0'; }
        z3::expr mask_e = const_bv(v.ctx(), m, w);
        z3::expr val_e = const_bv(v.ctx(), val, w);
        z3::expr not_mask = ~mask_e;
        z3::expr anded = v & not_mask;
        return anded | val_e;
    }

    EventGraph::EventGraph(z3::context& c, const Json& graph, const Json& contract_, const Json& observe_all, const std::string& case_name_)
        : ctx(c), G(graph), contract(contract_), case_name(case_name_)
    {
        observe = observe_all.at(case_name);
        P = G.at("period").get<long long>();
        origin = G.at("origin").get<long long>();
        require(G.at("available").get<bool>(), "no event graph");
        for (const auto& [name, reason] : G.at("excluded").items())
        {
            bool ok = reason.get<std::string>() == "NO_WRITES_IN_WINDOW";
            for (const auto& row : words().at(name).at("certified_known_lsb_first"))
                ok = ok && row.get<std::string>().find('x') == std::string::npos;
            require(ok, "program recovery needs a closed graph: " + name + " " + reason.get<std::string>());
        }
        require(G.at("certificate").at("certified").get<bool>() && G.at("certificate").at("phases_aligned").get<bool>(),
                "needs a certificate aligned with the graph period");

        for (const auto& [name, w] : words().items())
        {
            width[name] = static_cast<unsigned>(w.at("bits").size());
            unsigned i = 0;
            for (const auto& b : w.at("bits")) bit_owner[b.get<std::string>()] = {"word", name, i++};
        }
        for (const auto& [name, inp] : inputs().items())
        {
            unsigned i = 0;
            for (const auto& b : inp.at("bits")) bit_owner[b.get<std::string>()] = {"input", name, i++};
        }
        for (const auto& [name, mem] : G.at("memories").items())
        {
            unsigned i = 0;
            for (const auto& b : mem.at("read_bits")) bit_owner[b.get<std::string>()] = {"ram", name, i++};
        }
        require(G.at("memories").empty(), "RAM-backed graphs are not handled by this first version");

        // nodes, specialised at their own (graph) phase
        std::map<Key, Node> raw_nodes;
        for (const auto& n : G.at("nodes"))
        {
            const std::string smt2 = n.at("smt2").get<std::string>();
            z3::expr e = parse(smt2);
            Node node{specialise(e, n.at("phase").get<long long>()), e};
            for (const auto& s : n.at("sources"))
                node.sources.items.push_back({s.at("symbol").get<std::string>(), s.at("kind").get<std::string>(), s.at("name").get<std::string>(),
                                              s.at("phase").get<long long>(), s.at("delay").get<long long>()});
            node.width = n.at("width").get<unsigned>();
            node.raw_size = smt2.size();
            node.raw_sha256 = sha256_hex(smt2);
            raw_nodes.emplace(Key{n.at("name").get<std::string>(), n.at("phase").get<long long>()}, std::move(node));
        }
        // retiming to transaction coordinates (Theorem: origin change is a retiming)
        const long long first = contract.at("transactions").at(0).at("first_event").get<long long>();
        const long long period_events = 2 * contract.at("period_cycles").get<long long>();
        require(period_events == P, "graph period differs from the transaction length");
        shift = ((first - origin) % P + P) % P;
        auto rho = [&](long long phase) { return phase < shift ? -1LL : 0LL; };
        auto mod = [&](long long x) { return ((x % P) + P) % P; };
        for (auto& [key, node] : raw_nodes)
        {
            const auto& [name, phase] = key;
            Sources remapped;
            for (const auto& src : node.sources.items)
            {
                Source s = src;
                if (src.kind == "signal")
                {
                    s.delay = src.delay + rho(phase) - rho(src.phase);
                    require(s.delay >= 0, "negative delay after retiming");
                }
                s.phase = mod(src.phase - shift);
                remapped.items.push_back(s);
            }
            Node n2{node.expr, node.raw, remapped, node.width, node.raw_size, node.raw_sha256, phase};
            nodes.emplace(Key{name, mod(phase - shift)}, std::move(n2));
        }
        for (const auto& [key, node] : nodes) writes[key.first].push_back(key.second);
        for (auto& [name, phs] : writes) std::sort(phs.begin(), phs.end());

        // observations
        for (const auto& p : contract.at("port_bits"))
            if (p.at("direction").get<std::string>() == "output") ports[p.at("name").get<std::string>()].push_back(p);
        for (auto& [name, v] : ports)
            std::stable_sort(v.begin(), v.end(), [](const Json& a, const Json& b) { return a.at("offset").get<long long>() < b.at("offset").get<long long>(); });
        for (const auto& ob : observe)
        {
            std::vector<long long> phases;
            if (ob.contains("cycle"))
            {
                const long long offset = 2 * ob.at("cycle").get<long long>();
                require(0 <= offset && offset <= P, "observation must lie within the transaction or at its end");
                phases.push_back(offset);
            }
            else
            {
                for (long long ph = 0; ph < P; ++ph)
                {
                    z3::expr s = leaf_substitute(output_expr(ob.at("strobe").get<std::string>()), ph);
                    require(s.is_app() && s.decl().decl_kind() == Z3_OP_BNUM, "strobe is not certified constant");
                    if (std::string(Z3_get_numeral_string(ctx, s)) == "1") phases.push_back(ph);
                }
            }
            for (long long ph : phases)
                observations.push_back({ob.at("port").get<std::string>(), ph, leaf_substitute(output_expr(ob.at("port").get<std::string>()), ph)});
        }
        require(!observations.empty(), "no observation phases");

        // liveness from observations
        std::vector<Key> stack;
        for (const auto& ob : observations) visit_symbols(ob.expr, ob.phase, nullptr, stack);
        while (!stack.empty())
        {
            const Key key = stack.back();
            stack.pop_back();
            const Node& node = nodes.at(key);
            visit_symbols(node.expr, key.second, &node.sources, stack);
        }
        order.assign(live.begin(), live.end());
        std::sort(order.begin(), order.end(), [](const Key& a, const Key& b) {
            return std::tie(a.second, a.first) < std::tie(b.second, b.first);
        });
    }

    std::pair<long long, long long> EventGraph::last_write(const std::string& name, long long phase) const
    {
        const auto& w = writes.at(name);
        long long best = -1;
        bool found = false;
        for (long long p : w) if (p < phase) { best = p; found = true; }
        if (found) return {best, 0};
        return {w.back(), 1};
    }

    z3::expr EventGraph::output_expr(const std::string& port_name) const
    {
        std::vector<z3::expr> bits;
        for (const auto& p : ports.at(port_name))
        {
            const Json& spec = G.at("outputs").at(p.at("anonymous_port").get<std::string>()).at(0);
            if (spec.is_number_integer()) bits.push_back(ctx.bv_val(spec.get<long long>() == -2 ? 1 : 0, 1));
            else
            {
                z3::expr b = parse(spec.at("smt2").get<std::string>());
                z3::expr one = ctx.bv_val(1, 1);
                z3::expr zero = ctx.bv_val(0, 1);
                bits.push_back(z3::ite(b, one, zero));
            }
        }
        if (bits.size() > 1)
        {
            std::vector<z3::expr> rev(bits.rbegin(), bits.rend());
            return concat_all(rev).simplify();
        }
        return bits[0].simplify();
    }

    z3::expr EventGraph::leaf_substitute(const z3::expr& expr, long long phase) const
    {
        std::vector<std::pair<z3::expr, z3::expr>> subs;
        for (const auto& v : z3_vars(expr))
        {
            const auto& [kind, base, i] = bit_owner.at(sym_name(v));
            if (kind == "word")
                subs.emplace_back(v, ctx.bv_const(("word:" + base).c_str(), width.at(base)).extract(i, i));
            else if (kind == "input")
                subs.emplace_back(v, ctx.bv_const(("input:" + base).c_str(), static_cast<unsigned>(inputs().at(base).at("bits").size())).extract(i, i));
        }
        return specialise(substitute(expr, subs), ((phase + shift) % P + P) % P);
    }

    const std::string& EventGraph::known_pattern(const std::string& sym, long long ph) const
    {
        const auto [kind, base] = partition_colon(sym);
        const Json& table = kind == "word" ? words().at(base).at("certified_known_lsb_first") : inputs().at(base).at("certified_known_lsb_first");
        return table.at(((ph + shift) % P + P) % P).get_ref<const std::string&>();
    }

    void EventGraph::visit_symbols(const z3::expr& expr, long long phase, const Sources* sources, std::vector<Key>& stack)
    {
        for (const auto& v : z3_vars(expr))
        {
            const std::string name = sym_name(v);
            const auto [kind, base] = partition_colon(name);
            if (kind == "word")
            {
                long long ph, d;
                if (sources != nullptr && !sources->empty())
                {
                    const Source& s = sources->at(name);
                    ph = s.phase; d = s.delay;
                }
                else std::tie(ph, d) = last_write(base, phase);
                if (d != 0) state_reads.insert({base, ph, d});
                if (live.insert({base, ph}).second) stack.push_back({base, ph});
            }
            else if (kind == "input") pad_reads.insert({base, phase});
        }
    }
}
