#include "lift_arithmetic.h"
#include "event_graph.h"
#include <algorithm>
#include <numeric>

namespace program_recovery
{
    namespace
    {
        // Python's x & ((1 << m) - 1) for possibly negative x
        BigInt pm(const BigInt& x, unsigned m)
        {
            const BigInt mod = BigInt(1) << m;
            BigInt r = x % mod;
            if (r < 0) r += mod;
            return r;
        }
        // Python's (x >> k) & 1 for possibly negative x
        int bit_of(const BigInt& x, unsigned k) { return static_cast<int>(pm(x, k + 1) >> k); }

        using Assign = std::vector<std::pair<BitRef, BigInt>>;

        BigInt eval_assign(const ExprEval& f, const Assign& a)
        {
            std::map<std::string, BigInt> vals;
            for (const auto& [ref, b] : a) if (b != 0) vals[ref.first] |= BigInt(1) << ref.second;
            return f(vals);
        }

        BigInt z_of(const ExprEval& f, const std::vector<unsigned>& outs, const Assign& a)
        {
            const BigInt y = eval_assign(f, a);
            BigInt z = 0;
            for (size_t q = 0; q < outs.size(); ++q) z |= ((y >> outs[q]) & 1) << q;
            return z;
        }

        std::vector<std::vector<BitRef>> levels_of(const Chain& chain)
        {
            std::vector<std::vector<BitRef>> levels;
            Support prev;
            for (const auto& [i, s] : chain)
            {
                std::vector<BitRef> nw;
                for (const auto& b : s) if (!prev.count(b)) nw.push_back(b);
                levels.push_back(nw);
                prev = s;
            }
            return levels;
        }

        const std::map<std::vector<BigInt>, std::string>& bitwise_names()
        {
            static const std::map<std::vector<BigInt>, std::string> names = [] {
                std::map<std::vector<BigInt>, std::string> m;
                auto table = [](auto fn) {
                    std::vector<BigInt> t;
                    for (int x = 0; x < 8; ++x) t.push_back(fn(x & 1, (x >> 1) & 1, (x >> 2) & 1));
                    return t;
                };
                // Python dict insertion order: ch, parity, maj (later keys overwrite equal tables)
                m[table([](int b, int c, int d) { return (b & c) | ((1 - b) & d); })] = "ch";
                m[table([](int b, int c, int d) { return b ^ c ^ d; })] = "parity";
                m[table([](int b, int c, int d) { return (b & c) | (b & d) | (c & d); })] = "maj";
                return m;
            }();
            return names;
        }
    }

    std::pair<std::vector<Chain>, std::vector<unsigned>> chains(const z3::expr& expr)
    {
        const unsigned n = expr.get_sort().bv_size();
        std::vector<Support> sup(n);
        for (unsigned i = 0; i < n; ++i)
        {
            const auto sb = symbol_bits(expr.extract(i, i).simplify());
            for (const auto& [s, ks] : sb) for (unsigned k : ks) sup[i].insert({s, k});
        }
        std::vector<unsigned> parent(n);
        std::iota(parent.begin(), parent.end(), 0u);
        auto find = [&](unsigned x) {
            while (parent[x] != x) { parent[x] = parent[parent[x]]; x = parent[x]; }
            return x;
        };
        std::map<BitRef, unsigned> owner;
        for (unsigned i = 0; i < n; ++i)
        {
            if (sup[i].size() <= 1) continue;
            for (const auto& b : sup[i])
            {
                auto it = owner.find(b);
                if (it != owner.end()) parent[find(i)] = find(it->second);
                else owner[b] = i;
            }
        }
        std::vector<std::pair<unsigned, std::vector<unsigned>>> comps; // insertion order of roots
        for (unsigned i = 0; i < n; ++i)
        {
            if (sup[i].size() <= 1) continue;
            const unsigned r = find(i);
            auto it = std::find_if(comps.begin(), comps.end(), [&](const auto& c) { return c.first == r; });
            if (it == comps.end()) comps.push_back({r, {i}});
            else it->second.push_back(i);
        }
        std::vector<Chain> result;
        std::vector<unsigned> leftover;
        for (unsigned i = 0; i < n; ++i) if (sup[i].size() <= 1) leftover.push_back(i);
        for (auto& [root, members] : comps)
        {
            std::stable_sort(members.begin(), members.end(), [&](unsigned a, unsigned b) {
                return std::make_pair(sup[a].size(), a) < std::make_pair(sup[b].size(), b);
            });
            std::vector<std::vector<unsigned>> cs;
            bool ok = true;
            for (unsigned i : members)
            {
                std::vector<size_t> same, fits;
                for (size_t c = 0; c < cs.size(); ++c) if (sup[cs[c].back()] == sup[i]) same.push_back(c);
                if (!same.empty()) fits = same;
                else
                    for (size_t c = 0; c < cs.size(); ++c)
                    {
                        const Support& a = sup[cs[c].back()];
                        if (a.size() < sup[i].size() && std::includes(sup[i].begin(), sup[i].end(), a.begin(), a.end())) fits.push_back(c);
                    }
                if (fits.size() > 1) { ok = false; break; }
                if (!fits.empty()) cs[fits[0]].push_back(i);
                else cs.push_back({i});
            }
            bool long_enough = true;
            for (const auto& c : cs) long_enough &= c.size() >= 4;
            if (ok && long_enough)
            {
                for (const auto& c : cs)
                {
                    Chain ch;
                    for (unsigned i : c) ch.emplace_back(i, sup[i]);
                    result.push_back(ch);
                }
            }
            else leftover.insert(leftover.end(), members.begin(), members.end());
        }
        std::sort(leftover.begin(), leftover.end());
        return {result, leftover};
    }

    std::optional<Candidate> fit_chain(const ExprEval& f, const Chain& chain, PyRandom& rng)
    {
        const auto levels = levels_of(chain);
        const size_t r = levels[0].size();
        if (r < 1 || r > 3) return std::nullopt;
        for (const auto& l : levels) if (l.size() > r) return std::nullopt;
        std::vector<unsigned> outs;
        for (const auto& [i, _] : chain) outs.push_back(i);
        const unsigned m = static_cast<unsigned>(outs.size());
        auto z = [&](const Assign& a) { return z_of(f, outs, a); };
        auto inter = [&](const BitRef& u, const BitRef& v) {
            return pm(z({{u, 1}, {v, 1}}) - z({{u, 1}}) - z({{v, 1}}) + z({}), m);
        };
        const auto& base = levels[0];
        std::vector<std::vector<bool>> P(r, std::vector<bool>(r));
        for (size_t g = 0; g < r; ++g)
            for (size_t h = 0; h < r; ++h) P[g][h] = h == g ? false : inter(base[g], base[h]) != 0;
        std::vector<std::vector<BitRef>> groups;
        for (const auto& u : base) groups.push_back({u});
        for (size_t li = 1; li < levels.size(); ++li)
        {
            std::set<size_t> used;
            for (const auto& v : levels[li])
            {
                std::vector<bool> sv;
                for (const auto& w : base) sv.push_back(inter(v, w) != 0);
                std::optional<size_t> cand;
                for (size_t g = 0; g < r && !cand; ++g) if (!used.count(g) && P[g] == sv) cand = g;
                if (!cand) return std::nullopt;
                used.insert(*cand);
                groups[*cand].push_back(v);
            }
        }
        auto point = [&](const std::vector<BigInt>& vals) {
            Assign a;
            for (size_t g = 0; g < r; ++g)
                for (size_t i = 0; i < groups[g].size(); ++i) a.emplace_back(groups[g][i], (vals[g] >> i) & 1);
            return z(a);
        };
        const BigInt zero = point(std::vector<BigInt>(r, 0));
        std::vector<BigInt> c(r);
        for (size_t g = 0; g < r; ++g)
        {
            std::vector<BigInt> unit(r, 0);
            unit[g] = 1;
            c[g] = pm(point(unit) - zero, m);
        }
        std::vector<std::pair<std::pair<unsigned, unsigned>, BigInt>> d;
        for (size_t g = 0; g < r; ++g)
            for (size_t h = g + 1; h < r; ++h)
            {
                std::vector<BigInt> two(r, 0);
                two[g] = 1; two[h] = 1;
                d.push_back({{static_cast<unsigned>(g), static_cast<unsigned>(h)}, pm(point(two) - zero - c[g] - c[h], m)});
            }
        std::vector<std::vector<BigInt>> tests(64, std::vector<BigInt>(r));
        for (auto& A : tests) for (size_t g = 0; g < r; ++g) A[g] = rng.getrandbits(static_cast<unsigned>(groups[g].size()));
        std::vector<BigInt> observed;
        for (const auto& A : tests) observed.push_back(point(A));
        for (size_t mask = 0; mask < (size_t(1) << r); ++mask)
        {
            std::vector<bool> sgn(r);
            for (size_t g = 0; g < r; ++g) sgn[g] = (mask >> (r - 1 - g)) & 1; // itertools.product order
            auto val = [&](const std::vector<BigInt>& A, size_t g) {
                const size_t w = groups[g].size();
                return (sgn[g] && w > 1) ? BigInt(A[g] - (BigInt(1) << w) * ((A[g] >> (w - 1)) & 1)) : A[g];
            };
            bool all = true;
            for (size_t t = 0; all && t < tests.size(); ++t)
            {
                BigInt p = zero;
                for (size_t g = 0; g < r; ++g) p += c[g] * val(tests[t], g);
                for (const auto& [gh, dv] : d) p += dv * val(tests[t], gh.first) * val(tests[t], gh.second);
                all = pm(p, m) == observed[t];
            }
            if (all)
            {
                Candidate cand;
                cand.outputs = outs;
                for (const auto& g : groups)
                {
                    std::vector<std::optional<BitRef>> op;
                    for (const auto& b : g) op.emplace_back(b);
                    cand.operands.push_back(op);
                }
                cand.cnst = zero;
                cand.linear = c;
                cand.bilinear = d;
                cand.width = m;
                for (size_t g = 0; g < r; ++g) cand.is_signed.push_back(sgn[g] && groups[g].size() > 1);
                cand.order_ambiguous = false;
                for (const auto& l : levels) cand.order_ambiguous |= l.empty();
                return cand;
            }
        }
        return std::nullopt;
    }

    std::optional<Candidate> fit_chain_arx(const ExprEval& f, const Chain& chain, PyRandom& rng, size_t max_linear)
    {
        const auto levels = levels_of(chain);
        std::vector<unsigned> outs;
        for (const auto& [i, _] : chain) outs.push_back(i);
        const unsigned m = static_cast<unsigned>(outs.size());
        for (const auto& l : levels) if (l.empty()) return std::nullopt;
        if (levels[0].size() > max_linear + 3) return std::nullopt;
        auto z = [&](const Assign& a) { return z_of(f, outs, a); };
        const BigInt z0 = z({});
        auto inter = [&](const BitRef& u, const BitRef& w) { return pm(z({{u, 1}, {w, 1}}) - z({{u, 1}}) - z({{w, 1}}) + z0, m); };
        std::optional<std::vector<std::vector<BitRef>>> lin_ops, roles;
        std::optional<std::vector<BigInt>> table;
        for (size_t i = 0; i < levels.size(); ++i)
        {
            const auto& lvl = levels[i];
            std::set<BitRef> nls;
            for (const auto& u : lvl)
                for (const auto& w : lvl)
                    if (u < w && inter(u, w) != 0) { nls.insert(u); nls.insert(w); }
            const std::vector<BitRef> nl(nls.begin(), nls.end());
            std::vector<BitRef> lin;
            for (const auto& u : lvl) if (!nls.count(u)) lin.push_back(u);
            if (nl.empty() && table && roles)
            {
                const size_t k = roles->size();
                const int t0 = static_cast<int>((*table)[0] & 1);
                bool affine2 = true;
                for (size_t x = 0; affine2 && x < (size_t(1) << k); ++x)
                {
                    int acc = 0;
                    for (size_t rr = 0; rr < k; ++rr) if ((x >> rr) & 1) acc += static_cast<int>(((*table)[size_t(1) << rr] & 1)) ^ t0;
                    affine2 = static_cast<int>((*table)[x] & 1) == (t0 ^ (acc & 1));
                }
                if (affine2 && lvl.size() == k + lin_ops->size())
                {
                    std::vector<BitRef> ordered = lvl;
                    std::sort(ordered.begin(), ordered.end());
                    for (size_t rr = 0; rr < k; ++rr) (*roles)[rr].push_back(ordered[rr]);
                    for (size_t g = 0; g + k < ordered.size(); ++g) (*lin_ops)[g].push_back(ordered[k + g]);
                    continue;
                }
            }
            if (!(2 <= nl.size() && nl.size() <= 3)) return std::nullopt;
            const size_t k = nl.size();
            std::vector<BigInt> diff(size_t(1) << k);
            for (size_t x = 0; x < (size_t(1) << k); ++x)
            {
                Assign a;
                for (size_t q = 0; q < k; ++q) a.emplace_back(nl[q], (x >> q) & 1);
                diff[x] = pm(z(a) - z0, m);
            }
            std::optional<std::pair<std::vector<size_t>, std::vector<BigInt>>> found;
            std::vector<size_t> perm(k);
            std::iota(perm.begin(), perm.end(), size_t(0));
            do
            {
                std::vector<BigInt> T(size_t(1) << k);
                for (size_t x = 0; x < (size_t(1) << k); ++x)
                {
                    size_t idx = 0;
                    for (size_t rr = 0; rr < k; ++rr) idx |= ((x >> perm[rr]) & 1) << rr;
                    T[idx] = diff[x];
                }
                const BigInt unit = BigInt(1) << i;
                if (!table)
                {
                    bool ok = true;
                    std::vector<int> t;
                    for (const auto& v : T)
                    {
                        if (v == 0) t.push_back(0);
                        else if (v == pm(unit, m)) t.push_back(1);
                        else if (v == pm(-unit, m)) t.push_back(-1);
                        else { ok = false; break; }
                    }
                    if (!ok) continue;
                    const int base = std::find(t.begin(), t.end(), -1) != t.end() ? 1 : 0;
                    std::vector<BigInt> cand_table;
                    for (int dd : t) cand_table.push_back(base + dd);
                    found = std::make_pair(perm, cand_table);
                    break;
                }
                bool all = true;
                for (size_t ix = 0; all && ix < (size_t(1) << k); ++ix)
                {
                    require(ix < table->size(), "IndexError in fit_chain_arx");
                    all = pm(((*table)[ix] - (*table)[0]) * unit - T[ix], m) == 0;
                }
                if (all) { found = std::make_pair(perm, *table); break; }
            } while (std::next_permutation(perm.begin(), perm.end()));
            if (!found) return std::nullopt;
            const auto& [fperm, fT] = *found;
            if (!table)
            {
                table = fT;
                roles = std::vector<std::vector<BitRef>>(k);
                lin_ops = std::vector<std::vector<BitRef>>(lin.size());
            }
            if (lin.size() > lin_ops->size()) return std::nullopt;
            require(k <= roles->size(), "IndexError in fit_chain_arx roles");
            for (size_t rr = 0; rr < k; ++rr) (*roles)[rr].push_back(nl[fperm[rr]]);
            std::vector<BitRef> sl = lin;
            std::sort(sl.begin(), sl.end());
            for (size_t g = 0; g < sl.size(); ++g) (*lin_ops)[g].push_back(sl[g]);
        }
        // name the bitwise layer up to a permutation of its roles
        const size_t k_ = roles->size();
        std::vector<size_t> perm(k_);
        std::iota(perm.begin(), perm.end(), size_t(0));
        do
        {
            std::vector<BigInt> Tn;
            for (size_t x = 0; x < (size_t(1) << k_); ++x)
            {
                size_t idx = 0;
                for (size_t rr = 0; rr < k_; ++rr) idx |= ((x >> rr) & 1) << perm[rr];
                Tn.push_back((*table)[idx]);
            }
            if (bitwise_names().count(Tn))
            {
                std::vector<std::vector<BitRef>> nr;
                for (size_t rr = 0; rr < k_; ++rr) nr.push_back((*roles)[perm[rr]]);
                table = Tn;
                roles = nr;
                break;
            }
        } while (std::next_permutation(perm.begin(), perm.end()));
        std::vector<BigInt> c;
        for (const auto& op : *lin_ops)
        {
            require(!op.empty(), "IndexError: empty linear operand");
            c.push_back(pm(z({{op[0], 1}}) - z0, m));
        }
        BigInt F0 = 0;
        for (unsigned i = 0; i < m; ++i) F0 += (*table)[0] << i;
        F0 = pm(F0, m);
        const BigInt cnst = pm(z0 - F0, m);
        auto predict = [&](const std::map<BitRef, BigInt>& assign) {
            auto get = [&](const BitRef& b) { auto it = assign.find(b); return it == assign.end() ? BigInt(0) : it->second; };
            BigInt tot = cnst;
            for (size_t g = 0; g < lin_ops->size(); ++g)
            {
                BigInt s = 0;
                for (size_t i = 0; i < (*lin_ops)[g].size(); ++i) s += get((*lin_ops)[g][i]) << i;
                tot += c[g] * s;
            }
            for (unsigned i = 0; i < m; ++i)
            {
                size_t x = 0;
                for (size_t rr = 0; rr < roles->size(); ++rr)
                    if (i < (*roles)[rr].size()) x += static_cast<size_t>(get((*roles)[rr][i])) << rr;
                tot += (*table)[x] << i;
            }
            return pm(tot, m);
        };
        for (int rep = 0; rep < 64; ++rep)
        {
            std::map<BitRef, BigInt> assign;
            Assign as_list;
            for (const auto& lvl : levels)
                for (const auto& b : lvl)
                {
                    const BigInt bit = rng.getrandbits(1);
                    assign[b] = bit;
                }
            for (const auto& [b, v] : assign) as_list.emplace_back(b, v);
            if (z(as_list) != predict(assign)) return std::nullopt;
        }
        Candidate cand;
        cand.outputs = outs;
        for (const auto& op : *lin_ops)
        {
            std::vector<std::optional<BitRef>> o;
            for (const auto& b : op) o.emplace_back(b);
            cand.operands.push_back(o);
        }
        cand.cnst = cnst;
        cand.linear = c;
        cand.width = m;
        cand.is_signed.assign(lin_ops->size(), false);
        Bitwise bw;
        bw.table = *table;
        bw.roles = *roles;
        if (auto it = bitwise_names().find(*table); it != bitwise_names().end()) bw.name = it->second;
        cand.bitwise = bw;
        cand.method = "arx_chain";
        cand.order_ambiguous = false;
        return cand;
    }

    std::optional<Candidate> fit_known(const ExprEval& f, unsigned n_out, const std::vector<std::vector<std::optional<BitRef>>>& operands,
                                       PyRandom& rng, const std::vector<unsigned>& outputs_in, unsigned samples)
    {
        const size_t r = operands.size();
        if (!(1 <= r && r <= 3)) return std::nullopt;
        std::vector<unsigned> outputs = outputs_in;
        if (outputs.empty()) { outputs.resize(n_out); std::iota(outputs.begin(), outputs.end(), 0u); }
        const unsigned m = static_cast<unsigned>(outputs.size());
        std::vector<std::pair<size_t, size_t>> pairs;
        for (size_t g = 0; g < r; ++g) for (size_t h = g + 1; h < r; ++h) pairs.emplace_back(g, h);
        std::vector<std::vector<BigInt>> tests(samples, std::vector<BigInt>(r));
        for (auto& A : tests) for (size_t g = 0; g < r; ++g) A[g] = rng.getrandbits(static_cast<unsigned>(operands[g].size()));
        std::vector<BigInt> present(r, 0);
        for (size_t g = 0; g < r; ++g)
            for (size_t i = 0; i < operands[g].size(); ++i) if (operands[g][i]) present[g] |= BigInt(1) << i;
        for (auto& A : tests) for (size_t g = 0; g < r; ++g) A[g] &= present[g];
        std::vector<BigInt> ys;
        for (const auto& A : tests)
        {
            std::map<std::string, BigInt> vals;
            for (size_t g = 0; g < r; ++g)
                for (size_t i = 0; i < operands[g].size(); ++i)
                    if (operands[g][i] && ((A[g] >> i) & 1) != 0) vals[operands[g][i]->first] |= BigInt(1) << operands[g][i]->second;
            ys.push_back(f(vals));
        }
        std::map<unsigned, std::vector<int>> col;
        for (unsigned o : outputs)
        {
            std::vector<int> c;
            for (const auto& y : ys) c.push_back(static_cast<int>((y >> o) & 1));
            col[o] = c;
        }
        std::vector<size_t> t(r);
        for (size_t g = 0; g < r; ++g)
        {
            t[g] = m;
            for (size_t i = 0; i < operands[g].size(); ++i) if (operands[g][i]) { t[g] = i; break; }
        }
        for (size_t smask = 0; smask < (size_t(1) << r); ++smask)
        {
            std::vector<bool> sgn(r);
            for (size_t g = 0; g < r; ++g) sgn[g] = (smask >> (r - 1 - g)) & 1;
            std::vector<std::vector<BigInt>> val;
            for (const auto& A : tests)
            {
                std::vector<BigInt> row;
                for (size_t g = 0; g < r; ++g)
                {
                    const size_t w = operands[g].size();
                    row.push_back(sgn[g] && w > 1 ? BigInt(A[g] - (BigInt(1) << w) * ((A[g] >> (w - 1)) & 1)) : A[g]);
                }
                val.push_back(row);
            }
            BigInt cnst = 0;
            std::vector<BigInt> lin(r, 0);
            std::vector<BigInt> bil(pairs.size(), 0);
            std::vector<unsigned> order;
            std::set<unsigned> fre(outputs.begin(), outputs.end());
            bool ok = true, ambiguous = false;
            const size_t nb = 1 + r + pairs.size();
            for (unsigned k = 0; k < m; ++k)
            {
                bool matched = false;
                for (size_t idx = 0; idx < (size_t(1) << nb); ++idx)
                {
                    auto bit = [&](size_t j) { return static_cast<int>((idx >> (nb - 1 - j)) & 1); };
                    const BigInt c_ = cnst | (BigInt(bit(0)) << k);
                    std::vector<BigInt> l_(r);
                    for (size_t g = 0; g < r; ++g) l_[g] = lin[g] | (k >= t[g] ? BigInt(BigInt(bit(1 + g)) << (k - t[g])) : BigInt(0));
                    std::vector<BigInt> b_(pairs.size());
                    for (size_t q = 0; q < pairs.size(); ++q)
                    {
                        const size_t tt = t[pairs[q].first] + t[pairs[q].second];
                        b_[q] = bil[q] | (k >= tt ? BigInt(BigInt(bit(1 + r + q)) << (k - tt)) : BigInt(0));
                    }
                    std::vector<int> pred;
                    for (const auto& V : val)
                    {
                        BigInt s = c_;
                        for (size_t g = 0; g < r; ++g) s += l_[g] * V[g];
                        for (size_t q = 0; q < pairs.size(); ++q) s += b_[q] * V[pairs[q].first] * V[pairs[q].second];
                        pred.push_back(bit_of(s, k));
                    }
                    std::vector<unsigned> hits;
                    for (unsigned o : fre) if (col[o] == pred) hits.push_back(o);
                    if (!hits.empty())
                    {
                        ambiguous |= hits.size() > 1;
                        order.push_back(hits[0]);
                        fre.erase(hits[0]);
                        cnst = c_; lin = l_; bil = b_;
                        matched = true;
                        break;
                    }
                }
                if (!matched) { ok = false; break; }
            }
            if (ok)
            {
                Candidate cand;
                cand.outputs = order;
                cand.operands = operands;
                cand.cnst = cnst;
                cand.linear = lin;
                for (size_t q = 0; q < pairs.size(); ++q)
                    cand.bilinear.push_back({{static_cast<unsigned>(pairs[q].first), static_cast<unsigned>(pairs[q].second)}, bil[q]});
                cand.width = m;
                for (size_t g = 0; g < r; ++g) cand.is_signed.push_back(sgn[g] && operands[g].size() > 1);
                cand.method = "known_input_order";
                cand.order_ambiguous = ambiguous;
                return cand;
            }
        }
        return std::nullopt;
    }

    z3::expr candidate_expr(const Candidate& cand, const std::map<std::string, z3::expr>& variables)
    {
        z3::context& ctx = variables.begin()->second.ctx();
        const unsigned m = cand.width;
        std::vector<z3::expr> ops;
        for (size_t gi = 0; gi < cand.operands.size(); ++gi)
        {
            const auto& g = cand.operands[gi];
            std::vector<z3::expr> bits_;
            for (const auto& b : g)
            {
                if (!b) bits_.push_back(ctx.bv_val(0, 1));
                else bits_.push_back(variables.at(b->first).extract(b->second, b->second));
            }
            z3::expr word = bits_.size() > 1 ? concat_all(std::vector<z3::expr>(bits_.rbegin(), bits_.rend())) : bits_[0];
            const bool sg = gi < cand.is_signed.size() && cand.is_signed[gi];
            const int ext = static_cast<int>(m) - static_cast<int>(bits_.size());
            if (ext == 0) ops.push_back(word);
            else if (ext > 0) ops.push_back(sg ? z3::sext(word, static_cast<unsigned>(ext)) : z3::zext(word, static_cast<unsigned>(ext)));
            else ops.push_back(word.extract(m - 1, 0));
        }
        z3::expr z = ctx.bv_val(cand.cnst.str().c_str(), m);
        for (size_t g = 0; g < cand.linear.size(); ++g)
        {
            if (cand.linear[g] == 0) continue;
            z3::expr k = ctx.bv_val(cand.linear[g].str().c_str(), m);
            z3::expr prod = k * ops[g];
            z = z + prod;
        }
        for (const auto& [gh, dv] : cand.bilinear)
        {
            if (dv == 0) continue;
            z3::expr k = ctx.bv_val(dv.str().c_str(), m);
            z3::expr p1 = k * ops[gh.first];
            z3::expr p2 = p1 * ops[gh.second];
            z = z + p2;
        }
        if (cand.bitwise)
        {
            std::vector<z3::expr> words_;
            for (const auto& role : cand.bitwise->roles)
            {
                std::vector<z3::expr> bits_;
                for (const auto& [s_, k] : role) bits_.push_back(variables.at(s_).extract(k, k));
                words_.push_back(bits_.size() > 1 ? concat_all(std::vector<z3::expr>(bits_.rbegin(), bits_.rend())) : bits_[0]);
            }
            z3::expr F = ctx.bv_val(0, m);
            for (size_t x = 0; x < cand.bitwise->table.size(); ++x)
            {
                if (cand.bitwise->table[x] == 0) continue;
                z3::expr term = ctx.bv_val(mask_of(m).str().c_str(), m);
                for (size_t r = 0; r < words_.size(); ++r)
                {
                    if ((x >> r) & 1) term = term & words_[r];
                    else { z3::expr nw = ~words_[r]; term = term & nw; }
                }
                F = F | term;
            }
            z = z + F;
        }
        return z;
    }
}
