#include "expr_eval.h"
#include "event_graph.h"
#include "python_printer.h"
#include <unordered_map>

namespace program_recovery
{
    ExprEval::ExprEval(const z3::expr& root)
    {
        std::unordered_map<unsigned, unsigned> index;
        std::map<std::string, int> sym_index;
        // iterative post-order over the DAG
        std::vector<std::pair<z3::expr, bool>> stack{{root, false}};
        while (!stack.empty())
        {
            auto [e, expanded] = stack.back();
            stack.pop_back();
            if (index.count(e.id())) continue;
            require(e.is_app(), "evaluator: not an application");
            if (!expanded)
            {
                stack.push_back({e, true});
                for (int i = static_cast<int>(e.num_args()) - 1; i >= 0; --i)
                    if (!index.count(e.arg(i).id())) stack.push_back({e.arg(i), false});
                continue;
            }
            Step s;
            s.op = e.decl().decl_kind();
            s.width = e.is_bv() ? e.get_sort().bv_size() : 1;
            for (unsigned i = 0; i < e.num_args(); ++i) s.args.push_back(index.at(e.arg(i).id()));
            switch (s.op)
            {
                case Z3_OP_BNUM: s.literal = numeral_value(e); break;
                case Z3_OP_TRUE: case Z3_OP_FALSE: break;
                case Z3_OP_UNINTERPRETED:
                {
                    require(e.num_args() == 0, "evaluator: uninterpreted function");
                    const std::string name = sym_name(e);
                    auto it = sym_index.find(name);
                    if (it == sym_index.end())
                    {
                        it = sym_index.emplace(name, static_cast<int>(m_symbols.size())).first;
                        m_symbols.push_back(name);
                    }
                    s.symbol = it->second;
                    break;
                }
                case Z3_OP_EXTRACT:
                    s.p0 = static_cast<unsigned>(Z3_get_decl_int_parameter(e.ctx(), e.decl(), 0));
                    s.p1 = static_cast<unsigned>(Z3_get_decl_int_parameter(e.ctx(), e.decl(), 1));
                    break;
                case Z3_OP_SIGN_EXT: case Z3_OP_ZERO_EXT:
                    s.p0 = static_cast<unsigned>(Z3_get_decl_int_parameter(e.ctx(), e.decl(), 0));
                    s.p1 = e.arg(0).get_sort().bv_size();
                    break;
                case Z3_OP_SLEQ: case Z3_OP_SLT:
                    s.p1 = e.arg(0).get_sort().bv_size();
                    break;
                case Z3_OP_CONCAT: case Z3_OP_BNOT: case Z3_OP_BAND: case Z3_OP_BOR: case Z3_OP_BXOR:
                case Z3_OP_BADD: case Z3_OP_BSUB: case Z3_OP_BMUL: case Z3_OP_BNEG: case Z3_OP_BSHL:
                case Z3_OP_BLSHR: case Z3_OP_BASHR: case Z3_OP_ITE: case Z3_OP_EQ: case Z3_OP_NOT:
                case Z3_OP_AND: case Z3_OP_OR: case Z3_OP_ULEQ: case Z3_OP_ULT:
                    break;
                default: throw std::runtime_error("evaluator: operator " + e.decl().name().str());
            }
            index[e.id()] = static_cast<unsigned>(m_steps.size());
            m_steps.push_back(std::move(s));
        }
        m_width = root.is_bv() ? root.get_sort().bv_size() : 1;
    }

    BigInt ExprEval::operator()(const std::map<std::string, BigInt>& values) const
    {
        std::vector<BigInt> slot(m_symbols.size(), 0);
        for (size_t i = 0; i < m_symbols.size(); ++i)
            if (auto it = values.find(m_symbols[i]); it != values.end()) slot[i] = it->second;
        std::vector<BigInt> v(m_steps.size());
        for (size_t k = 0; k < m_steps.size(); ++k)
        {
            const Step& s = m_steps[k];
            const auto A = [&](unsigned i) -> const BigInt& { return v[s.args[i]]; };
            const BigInt mask = mask_of(s.width);
            BigInt r = 0;
            switch (s.op)
            {
                case Z3_OP_BNUM: r = s.literal; break;
                case Z3_OP_TRUE: r = 1; break;
                case Z3_OP_FALSE: r = 0; break;
                case Z3_OP_UNINTERPRETED: r = slot[static_cast<size_t>(s.symbol)]; break;
                case Z3_OP_EXTRACT: r = (A(0) >> s.p1) & mask_of(s.p0 - s.p1 + 1); break;
                case Z3_OP_CONCAT:
                {
                    // args are most significant first; z3 concat is binary but may be n-ary after simplify
                    r = 0;
                    for (unsigned i = 0; i < s.args.size(); ++i)
                    {
                        const unsigned w = m_steps[s.args[i]].width;
                        r = (r << w) | A(i);
                    }
                    break;
                }
                case Z3_OP_BNOT: r = A(0) ^ mask; break;
                case Z3_OP_BAND: r = A(0); for (unsigned i = 1; i < s.args.size(); ++i) r &= A(i); break;
                case Z3_OP_BOR: r = A(0); for (unsigned i = 1; i < s.args.size(); ++i) r |= A(i); break;
                case Z3_OP_BXOR: r = A(0); for (unsigned i = 1; i < s.args.size(); ++i) r ^= A(i); break;
                case Z3_OP_BADD: r = 0; for (unsigned i = 0; i < s.args.size(); ++i) r += A(i); r &= mask; break;
                case Z3_OP_BSUB: r = A(0) - A(1); if (r < 0) r += mask + 1; r &= mask; break;
                case Z3_OP_BMUL: r = 1; for (unsigned i = 0; i < s.args.size(); ++i) r = (r * A(i)) & mask; break;
                case Z3_OP_BNEG: r = (mask + 1 - A(0)) & mask; break;
                case Z3_OP_BSHL: r = A(1) < s.width ? BigInt((A(0) << static_cast<unsigned>(A(1))) & mask) : BigInt(0); break;
                case Z3_OP_BLSHR: r = A(1) < s.width ? BigInt(A(0) >> static_cast<unsigned>(A(1))) : BigInt(0); break;
                case Z3_OP_BASHR:
                {
                    const unsigned sh = A(1) < s.width ? static_cast<unsigned>(A(1)) : s.width;
                    const bool neg = ((A(0) >> (s.width - 1)) & 1) != 0;
                    r = A(0) >> sh;
                    if (neg) r |= mask ^ (mask >> sh);
                    r &= mask;
                    break;
                }
                case Z3_OP_ITE: r = A(0) != 0 ? A(1) : A(2); break;
                case Z3_OP_EQ: r = A(0) == A(1) ? 1 : 0; break;
                case Z3_OP_NOT: r = A(0) != 0 ? 0 : 1; break;
                case Z3_OP_AND: r = 1; for (unsigned i = 0; i < s.args.size(); ++i) if (A(i) == 0) { r = 0; break; } break;
                case Z3_OP_OR: r = 0; for (unsigned i = 0; i < s.args.size(); ++i) if (A(i) != 0) { r = 1; break; } break;
                case Z3_OP_ULEQ: r = A(0) <= A(1) ? 1 : 0; break;
                case Z3_OP_ULT: r = A(0) < A(1) ? 1 : 0; break;
                case Z3_OP_SLEQ: case Z3_OP_SLT:
                {
                    const unsigned w = s.p1;
                    auto sg = [&](const BigInt& x) { return ((x >> (w - 1)) & 1) != 0 ? BigInt(x - (BigInt(1) << w)) : x; };
                    r = (s.op == Z3_OP_SLEQ ? sg(A(0)) <= sg(A(1)) : sg(A(0)) < sg(A(1))) ? 1 : 0;
                    break;
                }
                case Z3_OP_SIGN_EXT:
                {
                    const unsigned sw = s.p1;
                    BigInt x = A(0);
                    if (((x >> (sw - 1)) & 1) != 0) x |= mask_of(sw + s.p0) ^ mask_of(sw);
                    r = x;
                    break;
                }
                case Z3_OP_ZERO_EXT: r = A(0); break;
                default: throw std::runtime_error("evaluator: unsupported step");
            }
            v[k] = r;
        }
        return v.back();
    }
}
