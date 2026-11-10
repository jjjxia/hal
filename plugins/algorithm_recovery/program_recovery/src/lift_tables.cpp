#include "lift_tables.h"
#include "event_graph.h"
#include <algorithm>
#include <numeric>
#include <unordered_set>

namespace program_recovery
{
    static const size_t MAX_TABLE = 8, MIN_TABLE_OUTPUTS = 2, MAX_SPLIT = 12, MAX_DECOMPOSE = 16;

    std::map<std::string, std::set<unsigned>> symbol_bits(const z3::expr& root)
    {
        std::map<std::string, std::set<unsigned>> res;
        std::unordered_set<unsigned> seen;
        std::vector<z3::expr> stack{root};
        while (!stack.empty())
        {
            z3::expr x = stack.back();
            stack.pop_back();
            if (!seen.insert(x.id()).second) continue;
            if (!x.is_app()) continue;
            if (x.decl().decl_kind() == Z3_OP_EXTRACT)
            {
                z3::expr a = x.arg(0);
                if (a.is_app() && a.num_args() == 0 && a.decl().decl_kind() == Z3_OP_UNINTERPRETED)
                {
                    const unsigned hi = static_cast<unsigned>(Z3_get_decl_int_parameter(x.ctx(), x.decl(), 0));
                    const unsigned lo = static_cast<unsigned>(Z3_get_decl_int_parameter(x.ctx(), x.decl(), 1));
                    auto& s = res[sym_name(a)];
                    for (unsigned k = lo; k <= hi; ++k) s.insert(k);
                    continue;
                }
            }
            if (x.num_args() == 0 && x.decl().decl_kind() == Z3_OP_UNINTERPRETED)
            {
                auto& s = res[sym_name(x)];
                if (x.is_bv()) for (unsigned k = 0; k < x.get_sort().bv_size(); ++k) s.insert(k);
                else s.insert(0);
                continue;
            }
            for (unsigned i = 0; i < x.num_args(); ++i) stack.push_back(x.arg(i));
        }
        return res;
    }

    std::vector<BitRef> sorted_support(const z3::expr& e)
    {
        std::vector<BitRef> out;
        for (const auto& [s, ks] : symbol_bits(e)) for (unsigned k : ks) out.emplace_back(s, k);
        std::sort(out.begin(), out.end());
        return out;
    }

    namespace
    {
        BigInt value(const ExprEval& ev, const std::vector<std::pair<BitRef, int>>& assignment)
        {
            std::map<std::string, BigInt> vals;
            for (const auto& [ref, b] : assignment) if (b) vals[ref.first] |= BigInt(1) << ref.second;
            return ev(vals);
        }

        // f = c0 ^ XOR(linear inputs) ^ XOR_C t_C(inputs of C), components C of the inputs that interact (a non-zero
        // second derivative joins two inputs); every component of at most MAX_TABLE inputs; checked on all 2^D points.
        struct Decomposition
        {
            std::vector<BitRef> linear;
            int c0 = 0;
            std::vector<std::pair<std::vector<BitRef>, std::vector<int>>> parts;
        };

        std::optional<Decomposition> xor_decompose(const ExprEval& ev, const std::vector<BitRef>& data)
        {
            const size_t D = data.size(), X = size_t(1) << D;
            std::vector<int> dt(X);
            for (size_t x = 0; x < X; ++x)
            {
                std::vector<std::pair<BitRef, int>> a;
                for (size_t q = 0; q < D; ++q) a.emplace_back(data[q], static_cast<int>((x >> q) & 1));
                dt[x] = static_cast<int>(value(ev, a));
            }
            Decomposition d;
            d.c0 = dt[0];
            size_t linmask = 0;
            std::vector<size_t> rest;
            for (size_t q = 0; q < D; ++q)
            {
                bool lin = true;
                for (size_t x = 0; lin && x < X; ++x) lin = (dt[x] ^ dt[x ^ (size_t(1) << q)]) == 1;
                if (lin) { d.linear.push_back(data[q]); linmask |= size_t(1) << q; }
                else rest.push_back(q);
            }
            std::vector<size_t> parent(D);
            std::iota(parent.begin(), parent.end(), size_t(0));
            std::function<size_t(size_t)> find = [&](size_t u) { return parent[u] == u ? u : parent[u] = find(parent[u]); };
            for (size_t i = 0; i < rest.size(); ++i)
                for (size_t j = i + 1; j < rest.size(); ++j)
                {
                    const size_t u = rest[i], w = rest[j];
                    if (find(u) == find(w)) continue;
                    const size_t eu = size_t(1) << u, ew = size_t(1) << w;
                    for (size_t x = 0; x < X; ++x)
                    {
                        if (x & (linmask | eu | ew)) continue;
                        if (dt[x] ^ dt[x ^ eu] ^ dt[x ^ ew] ^ dt[x ^ eu ^ ew]) { parent[find(u)] = find(w); break; }
                    }
                }
            std::map<size_t, std::vector<size_t>> comps;
            for (size_t q : rest) comps[find(q)].push_back(q);
            std::vector<std::pair<std::vector<size_t>, std::vector<int>>> tabs;
            for (const auto& [_, C] : comps)
            {
                if (C.size() > MAX_TABLE) return std::nullopt;
                std::vector<int> t(size_t(1) << C.size());
                for (size_t z = 0; z < t.size(); ++z)
                {
                    size_t x = 0;
                    for (size_t j = 0; j < C.size(); ++j) if ((z >> j) & 1) x |= size_t(1) << C[j];
                    t[z] = dt[x] ^ dt[0];
                }
                tabs.emplace_back(C, std::move(t));
            }
            for (size_t x = 0; x < X; ++x)
            {
                int v = d.c0;
                for (size_t q = 0; q < D; ++q) if (((linmask >> q) & 1) && ((x >> q) & 1)) v ^= 1;
                for (const auto& [C, t] : tabs)
                {
                    size_t z = 0;
                    for (size_t j = 0; j < C.size(); ++j) if ((x >> C[j]) & 1) z |= size_t(1) << j;
                    v ^= t[z];
                }
                if (v != dt[x]) return std::nullopt;
            }
            for (const auto& [C, t] : tabs)
            {
                std::vector<BitRef> ins;
                for (size_t q : C) ins.push_back(data[q]);
                d.parts.emplace_back(std::move(ins), t);
            }
            return d;
        }
    }

    LiftPlan lift_plan(const z3::expr& expr, const IsParam& is_param)
    {
        const unsigned n = expr.get_sort().bv_size();
        LiftPlan plan;
        plan.width = n;
        std::vector<z3::expr> bit_exprs;
        for (unsigned i = 0; i < n; ++i) bit_exprs.push_back(expr.extract(i, i).simplify());
        // groups: OrderedDict keyed by the input tuple
        std::vector<std::pair<std::vector<BitRef>, std::vector<std::pair<unsigned, std::vector<int>>>>> groups;
        auto group_add = [&](const std::vector<BitRef>& key, unsigned i, std::vector<int> dt) {
            for (auto& [k, members] : groups) if (k == key) { members.emplace_back(i, std::move(dt)); return; }
            groups.push_back({key, {{i, std::move(dt)}}});
        };
        std::vector<std::pair<unsigned, Decomposition>> decomposed;
        for (unsigned i = 0; i < n; ++i)
        {
            const z3::expr& b = bit_exprs[i];
            const std::vector<BitRef> sup = sorted_support(b);
            const ExprEval ev(b);
            if (sup.empty())
            {
                plan.cnst |= value(ev, {}) << i;
                continue;
            }
            std::vector<BitRef> data, params;
            for (const auto& x : sup) (is_param(x.first) ? params : data).push_back(x);
            if (data.size() > MAX_TABLE || (!params.empty() && sup.size() > MAX_SPLIT))
            {
                if (params.empty() && data.size() <= MAX_DECOMPOSE)
                    if (auto d = xor_decompose(ev, data)) { decomposed.emplace_back(i, std::move(*d)); continue; }
                plan.keep.push_back(i);
                continue;
            }
            const int f00 = static_cast<int>(value(ev, {}));
            if (!params.empty())
            {
                bool ok = true;
                const size_t S = sup.size();
                std::set<BitRef> data_set(data.begin(), data.end());
                for (size_t x = 0; ok && x < (size_t(1) << S); ++x)
                {
                    std::vector<std::pair<BitRef, int>> a, ad, ap;
                    for (size_t q = 0; q < S; ++q)
                    {
                        // itertools.product order: first element slowest; only the all() result matters
                        const int bit = static_cast<int>((x >> (S - 1 - q)) & 1);
                        a.emplace_back(sup[q], bit);
                        (data_set.count(sup[q]) ? ad : ap).emplace_back(sup[q], bit);
                    }
                    if (value(ev, a) != (value(ev, ad) ^ value(ev, ap) ^ BigInt(f00))) ok = false;
                }
                if (!ok)
                {
                    if (S <= MAX_TABLE)
                    {
                        std::vector<int> dt;
                        for (size_t x = 0; x < (size_t(1) << S); ++x)
                        {
                            std::vector<std::pair<BitRef, int>> a;
                            for (size_t q = 0; q < S; ++q) a.emplace_back(sup[q], static_cast<int>((x >> q) & 1));
                            dt.push_back(static_cast<int>(value(ev, a)));
                        }
                        group_add(sup, i, dt);
                    }
                    else plan.keep.push_back(i);
                    continue;
                }
                std::vector<int> g;
                bool any = false;
                for (size_t x = 0; x < (size_t(1) << params.size()); ++x)
                {
                    std::vector<std::pair<BitRef, int>> a;
                    for (size_t q = 0; q < params.size(); ++q) a.emplace_back(params[q], static_cast<int>((x >> q) & 1));
                    g.push_back(static_cast<int>(value(ev, a)) ^ f00);
                    any |= g.back() != 0;
                }
                if (any) plan.param_route.emplace_back(i, params, g);
            }
            if (data.empty())
            {
                plan.cnst |= BigInt(f00) << i;
                continue;
            }
            const size_t D = data.size();
            std::vector<int> dt;
            for (size_t x = 0; x < (size_t(1) << D); ++x)
            {
                std::vector<std::pair<BitRef, int>> a;
                for (size_t q = 0; q < D; ++q) a.emplace_back(data[q], static_cast<int>((x >> q) & 1));
                dt.push_back(static_cast<int>(value(ev, a)));
            }
            const int c0 = dt[0];
            std::vector<int> coeff;
            for (size_t q = 0; q < D; ++q) coeff.push_back(dt[size_t(1) << q] ^ c0);
            bool affine = true;
            for (size_t x = 0; affine && x < (size_t(1) << D); ++x)
            {
                int acc = 0;
                for (size_t q = 0; q < D; ++q) if ((x >> q) & 1) acc += coeff[q];
                affine = dt[x] == (c0 ^ (acc & 1));
            }
            if (affine)
            {
                std::vector<BitRef> terms;
                for (size_t q = 0; q < D; ++q) if (coeff[q]) terms.push_back(data[q]);
                if (terms.empty()) plan.cnst |= BigInt(c0) << i;
                else if (terms.size() == 1) plan.route.emplace_back(i, terms[0], c0);
                else plan.xr.emplace_back(i, terms, c0);
                continue;
            }
            group_add(data, i, dt);
        }
        for (const auto& [data, members] : groups)
        {
            if (members.size() < MIN_TABLE_OUTPUTS)
            {
                for (const auto& [i, _] : members) plan.keep.push_back(i);
                continue;
            }
            const size_t k = data.size();
            LiftTable t;
            t.inputs = data;
            for (const auto& [i, _] : members) t.outputs.push_back(i);
            for (size_t x = 0; x < (size_t(1) << k); ++x)
            {
                BigInt v = 0;
                for (size_t q = 0; q < members.size(); ++q) v += BigInt(members[q].second[x]) << q;
                t.table.push_back(v);
            }
            plan.tables.push_back(std::move(t));
        }
        // XOR decompositions. Components of different bits that share an input are merged (a whole S-box column becomes
        // one group); a bit's parts in a group of at most MAX_TABLE inputs become one table over the group's inputs (the
        // XOR of the parts), parts in a larger group stay as they are.
        {
            std::map<BitRef, BitRef> par;
            std::function<BitRef(const BitRef&)> root = [&](const BitRef& x) { return par.at(x) == x ? x : par[x] = root(par.at(x)); };
            for (const auto& [i, d] : decomposed)
                for (const auto& [ins, _] : d.parts)
                {
                    for (const auto& x : ins) par.emplace(x, x);
                    for (size_t k = 1; k < ins.size(); ++k)
                    {
                        const BitRef a = root(ins[0]), b = root(ins[k]);
                        if (a != b) par[a] = b;
                    }
                }
            std::map<BitRef, std::vector<BitRef>> members;
            for (const auto& [x, _] : par) members[root(x)].push_back(x);
            for (auto& [r, v] : members) std::sort(v.begin(), v.end());
            for (auto& [i, d] : decomposed)
            {
                std::map<BitRef, std::vector<size_t>> by_group;
                for (size_t p = 0; p < d.parts.size(); ++p) by_group[root(d.parts[p].first[0])].push_back(p);
                std::vector<std::pair<std::vector<BitRef>, std::vector<int>>> merged;
                for (const auto& [r, ps] : by_group)
                {
                    const auto& G = members.at(r);
                    if (G.size() > MAX_TABLE || (ps.size() == 1 && d.parts[ps[0]].first == G))
                    {
                        for (size_t p : ps) merged.push_back(d.parts[p]);
                        continue;
                    }
                    std::vector<int> t(size_t(1) << G.size(), 0);
                    for (size_t z = 0; z < t.size(); ++z)
                        for (size_t p : ps)
                        {
                            const auto& [ins, tp] = d.parts[p];
                            size_t zp = 0;
                            for (size_t j = 0; j < ins.size(); ++j)
                            {
                                const size_t at = static_cast<size_t>(std::lower_bound(G.begin(), G.end(), ins[j]) - G.begin());
                                if ((z >> at) & 1) zp |= size_t(1) << j;
                            }
                            t[z] ^= tp[zp];
                        }
                    merged.emplace_back(G, std::move(t));
                }
                d.parts = std::move(merged);
            }
        }
        // a table needs MIN_TABLE_OUTPUTS bits sharing its inputs, else its bits stay bit-level
        std::map<std::vector<BitRef>, std::set<unsigned>> users;
        for (const auto& [i, d] : decomposed)
            for (const auto& [ins, _] : d.parts) users[ins].insert(i);
        std::set<unsigned> dropped;
        for (bool changed = true; changed;)
        {
            changed = false;
            for (const auto& [i, d] : decomposed)
            {
                if (dropped.count(i)) continue;
                for (const auto& [ins, _] : d.parts)
                    if (users[ins].size() < MIN_TABLE_OUTPUTS)
                    {
                        dropped.insert(i);
                        for (const auto& [ins2, _2] : d.parts) users[ins2].erase(i);
                        changed = true;
                        break;
                    }
            }
        }
        std::map<std::vector<BitRef>, std::vector<std::pair<unsigned, const std::vector<int>*>>> shared;
        for (const auto& [i, d] : decomposed)
        {
            if (dropped.count(i)) { plan.keep.push_back(i); continue; }
            if (d.parts.empty())
            {
                if (d.linear.size() == 1) plan.route.emplace_back(i, d.linear[0], d.c0);
                else plan.xr.emplace_back(i, d.linear, d.c0);
                continue;
            }
            plan.dsd_linear.emplace_back(i, d.linear, d.c0);
            for (const auto& [ins, t] : d.parts) shared[ins].emplace_back(i, &t);
        }
        for (const auto& [ins, members] : shared)
        {
            LiftTable t;
            t.inputs = ins;
            for (const auto& [i, _] : members) t.outputs.push_back(i);
            for (size_t x = 0; x < (size_t(1) << ins.size()); ++x)
            {
                BigInt v = 0;
                for (size_t q = 0; q < members.size(); ++q) v += BigInt((*members[q].second)[x]) << q;
                t.table.push_back(v);
            }
            plan.dsd_tables.push_back(std::move(t));
        }
        std::sort(plan.keep.begin(), plan.keep.end());
        return plan;
    }

    std::optional<Whitened> whiten(const LiftTable& tab, const IsParam& is_param)
    {
        const auto& ins = tab.inputs;
        std::set<std::string> symset;
        for (const auto& [s, _] : ins) symset.insert(s);
        const std::vector<std::string> syms(symset.begin(), symset.end());
        std::vector<std::pair<std::vector<size_t>, std::vector<size_t>>> splits;
        if (syms.size() == 2)
        {
            std::vector<size_t> a, b;
            for (size_t q = 0; q < ins.size(); ++q) (ins[q].first == syms[0] ? a : b).push_back(q);
            splits.emplace_back(a, b);
        }
        std::vector<size_t> A, B;
        for (size_t q = 0; q < ins.size(); ++q) (is_param(ins[q].first) ? B : A).push_back(q);
        if (!A.empty() && !B.empty()) splits.emplace_back(A, B);
        const auto& T = tab.table;
        for (const auto& [SA, SB] : splits)
        {
            if (SA.size() != SB.size() || SA.size() > 6) continue;
            std::vector<size_t> pi(SB.size());
            std::iota(pi.begin(), pi.end(), size_t(0));
            do
            {
                bool ok = true;
                for (size_t x = 0; ok && x < (size_t(1) << ins.size()); ++x)
                {
                    size_t y = x;
                    for (size_t r = 0; r < SA.size(); ++r)
                    {
                        const size_t qa = SA[r], qb = SB[pi[r]];
                        if ((x >> qb) & 1) y ^= size_t(1) << qa;
                        y &= ~(size_t(1) << qb);
                    }
                    if (T[x] != T[y]) ok = false;
                }
                if (ok)
                {
                    Whitened w;
                    for (size_t u = 0; u < (size_t(1) << SA.size()); ++u)
                    {
                        size_t idx = 0;
                        for (size_t i = 0; i < SA.size(); ++i) idx |= ((u >> i) & 1) << SA[i];
                        w.table.push_back(T[idx]);
                    }
                    for (size_t q : SA) w.a.push_back(ins[q]);
                    for (size_t p : pi) w.b.push_back(ins[SB[p]]);
                    return w;
                }
            } while (std::next_permutation(pi.begin(), pi.end()));
        }
        return std::nullopt;
    }

    std::optional<std::pair<std::vector<unsigned>, std::vector<unsigned>>> match_tables(
        const std::vector<BigInt>& S, const std::vector<BigInt>& T, unsigned k, unsigned m)
    {
        const size_t X = size_t(1) << k;
        std::vector<unsigned> in_order(k);
        std::iota(in_order.begin(), in_order.end(), 0u);
        // column of output bit q of T over all x
        std::vector<std::vector<int>> tcol(m, std::vector<int>(X));
        for (unsigned q = 0; q < m; ++q) for (size_t x = 0; x < X; ++x) tcol[q][x] = static_cast<int>((T[x] >> q) & 1);
        // T must have no bits outside the m outputs for any scatter to reproduce it
        for (size_t x = 0; x < X; ++x) if ((T[x] >> m) != 0) return std::nullopt;
        do
        {
            std::vector<std::vector<int>> pcol(m, std::vector<int>(X));
            for (size_t x = 0; x < X; ++x)
            {
                size_t gx = 0;
                for (unsigned i = 0; i < k; ++i) gx |= ((x >> in_order[i]) & 1) << i;
                for (unsigned i = 0; i < m; ++i) pcol[i][x] = static_cast<int>((S[gx] >> i) & 1);
            }
            // lexicographically first out_order: position i takes the smallest unused q whose
            // column equals the column of bit i (feasibility is per position)
            std::vector<unsigned> out_order;
            std::vector<bool> used(m, false);
            std::function<bool(unsigned)> dfs = [&](unsigned i) -> bool {
                if (i == m) return true;
                for (unsigned q = 0; q < m; ++q)
                {
                    if (used[q] || pcol[i] != tcol[q]) continue;
                    used[q] = true; out_order.push_back(q);
                    if (dfs(i + 1)) return true;
                    used[q] = false; out_order.pop_back();
                }
                return false;
            };
            if (dfs(0)) return std::make_pair(in_order, out_order);
        } while (std::next_permutation(in_order.begin(), in_order.end()));
        return std::nullopt;
    }
}
