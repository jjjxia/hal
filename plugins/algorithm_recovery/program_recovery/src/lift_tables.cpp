#include "lift_tables.h"
#include "event_graph.h"
#include <algorithm>
#include <numeric>
#include <unordered_set>

namespace program_recovery
{
    static const size_t MAX_TABLE = 8, MIN_TABLE_OUTPUTS = 2, MAX_SPLIT = 12;

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
