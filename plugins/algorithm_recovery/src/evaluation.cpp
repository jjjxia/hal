#include "algorithm_recovery/evaluation.h"
#include <functional>
#include <set>
#include <stdexcept>

namespace hal::algorithm_recovery
{
    namespace
    {
        void require(bool value, const std::string& why) { if (!value) throw std::runtime_error(why); }
        Integer wrap(const Integer& value, unsigned width)
        {
            require(width > 0, "zero-width bit vector");
            const Integer modulus = Integer(1) << width; Integer result = value % modulus;
            if (result < 0) result += modulus;
            return result;
        }
        Integer signed_value(const Integer& value, unsigned width)
        {
            Integer result = wrap(value, width);
            if (boost::multiprecision::bit_test(result, width - 1)) result -= Integer(1) << width;
            return result;
        }
        Integer floor_div(const Integer& numerator, const Integer& denominator)
        {
            require(denominator > 0, "floor division requires a positive denominator");
            Integer q = numerator / denominator;
            if (numerator < 0 && numerator % denominator != 0) --q;
            return q;
        }
    }
    BitVectorProgram::BitVectorProgram(const z3::expr& expression)
    {
        std::map<unsigned, unsigned> visited;
        std::map<std::string, unsigned> variables;
        std::function<unsigned(const z3::expr&)> visit = [&](const z3::expr& e) -> unsigned {
            if (visited.count(e.id())) return visited.at(e.id());
            require(e.is_app() && (e.is_bv() || e.is_bool()), "unsupported evaluator expression/sort");
            Step step{e.decl().decl_kind(), e.is_bv() ? e.get_sort().bv_size() : 0};
            step.mask = step.width ? (Integer(1) << step.width) - 1 : Integer(1);
            for (unsigned i = 0; i < e.num_args(); ++i) step.args.push_back(visit(e.arg(i)));
            switch (step.op)
            {
                case Z3_OP_BNUM: step.literal = Integer(Z3_get_numeral_string(e.ctx(), e)); break;
                case Z3_OP_UNINTERPRETED:
                    require(e.num_args() == 0, "uninterpreted functions are not executable");
                    { const auto [it, added] = variables.emplace(e.decl().name().str(), step.width);
                      require(added || it->second == step.width, "ambiguous evaluator input sort"); }
                    break;
                case Z3_OP_EXTRACT: step.parameter = Z3_get_decl_int_parameter(e.ctx(), e.decl(), 1); break;
                case Z3_OP_REPEAT: case Z3_OP_ROTATE_LEFT: case Z3_OP_ROTATE_RIGHT:
                    step.parameter = Z3_get_decl_int_parameter(e.ctx(), e.decl(), 0); break;
                case Z3_OP_TRUE: case Z3_OP_FALSE: case Z3_OP_NOT: case Z3_OP_AND: case Z3_OP_OR: case Z3_OP_XOR:
                case Z3_OP_IMPLIES: case Z3_OP_EQ: case Z3_OP_DISTINCT: case Z3_OP_ITE:
                case Z3_OP_BNOT: case Z3_OP_BAND: case Z3_OP_BOR: case Z3_OP_BXOR:
                case Z3_OP_BNAND: case Z3_OP_BNOR: case Z3_OP_BXNOR: case Z3_OP_BCOMP:
                case Z3_OP_BADD: case Z3_OP_BSUB: case Z3_OP_BMUL: case Z3_OP_BNEG:
                case Z3_OP_BSHL: case Z3_OP_BLSHR: case Z3_OP_BASHR:
                case Z3_OP_CONCAT: case Z3_OP_ZERO_EXT: case Z3_OP_SIGN_EXT:
                case Z3_OP_EXT_ROTATE_LEFT: case Z3_OP_EXT_ROTATE_RIGHT:
                case Z3_OP_ULT: case Z3_OP_ULEQ: case Z3_OP_UGT: case Z3_OP_UGEQ:
                case Z3_OP_SLT: case Z3_OP_SLEQ: case Z3_OP_SGT: case Z3_OP_SGEQ:
                case Z3_OP_BUDIV: case Z3_OP_BUREM: case Z3_OP_BSDIV: case Z3_OP_BSREM: case Z3_OP_BSMOD:
                case Z3_OP_BUDIV_I: case Z3_OP_BUREM_I: case Z3_OP_BSDIV_I: case Z3_OP_BSREM_I: case Z3_OP_BSMOD_I:
                    break;
                default: throw std::runtime_error("unsupported evaluator operation: " + e.decl().name().str());
            }
            const unsigned result = m_steps.size(); m_steps.push_back(std::move(step)); visited.emplace(e.id(), result); return result;
        };
        visit(expression);
        for (const auto& variable : variables) m_inputs.push_back(variable);
        std::map<std::string, unsigned> indices;
        for (unsigned i = 0; i < m_inputs.size(); ++i) indices.emplace(m_inputs[i].first, i);
        // The DAG's original AST IDs locate variable steps without name guesses.
        std::function<void(const z3::expr&)> assign = [&](const z3::expr& e) {
            auto& step = m_steps.at(visited.at(e.id()));
            if (step.op == Z3_OP_UNINTERPRETED) step.parameter = indices.at(e.decl().name().str());
        };
        std::set<unsigned> seen; std::vector<z3::expr> pending{expression};
        while (!pending.empty())
        {
            auto e = pending.back(); pending.pop_back(); if (!seen.insert(e.id()).second) continue;
            assign(e); for (unsigned i = 0; i < e.num_args(); ++i) pending.push_back(e.arg(i));
        }
    }
    Integer BitVectorProgram::evaluate(const std::map<std::string, Integer>& inputs) const
    {
        require(inputs.size() == m_inputs.size(), "wrong evaluator input inventory");
        std::vector<Integer> values; for (const auto& [name, width] : m_inputs) values.push_back(inputs.at(name));
        return evaluate(values);
    }
    Integer BitVectorProgram::evaluate(const std::vector<Integer>& inputs) const
    {
        require(inputs.size() == m_inputs.size(), "wrong evaluator input count");
        std::vector<Integer> values; values.reserve(m_steps.size());
        for (const auto& s : m_steps)
        {
            auto a = [&](unsigned i) -> const Integer& { return values.at(s.args.at(i)); };
            auto sw = [&](unsigned i) { return m_steps.at(s.args.at(i)).width; };
            auto sign = [&](unsigned i) { return signed_value(a(i), sw(i)); };
            Integer v = 0;
            switch (s.op)
            {
                case Z3_OP_BNUM: v = s.literal; break;
                case Z3_OP_UNINTERPRETED:
                    v = inputs.at(s.parameter); if (s.width) v = wrap(v, s.width);
                    else require(v == 0 || v == 1, "invalid Boolean input"); break;
                case Z3_OP_TRUE: v = 1; break;
                case Z3_OP_FALSE: v = 0; break;
                case Z3_OP_NOT: v = a(0) == 0; break;
                case Z3_OP_AND: v = 1; for (auto i : s.args) v = v != 0 && values[i] != 0; break;
                case Z3_OP_OR: for (auto i : s.args) v = v != 0 || values[i] != 0; break;
                case Z3_OP_XOR: for (auto i : s.args) v = (v != 0) != (values[i] != 0); break;
                case Z3_OP_IMPLIES: v = a(0) == 0 || a(1) != 0; break;
                case Z3_OP_EQ: case Z3_OP_BCOMP: v = a(0) == a(1); break;
                case Z3_OP_DISTINCT: { std::set<Integer> all; for (auto i : s.args) all.insert(values[i]); v = all.size() == s.args.size(); break; }
                case Z3_OP_ITE: v = a(0) != 0 ? a(1) : a(2); break;
                case Z3_OP_BNOT: v = a(0) ^ s.mask; break;
                case Z3_OP_BAND: case Z3_OP_BNAND:
                    v = s.mask; for (auto i : s.args) v &= values[i]; if (s.op == Z3_OP_BNAND) v ^= s.mask; break;
                case Z3_OP_BOR: case Z3_OP_BNOR:
                    for (auto i : s.args) v |= values[i]; if (s.op == Z3_OP_BNOR) v ^= s.mask; break;
                case Z3_OP_BXOR: case Z3_OP_BXNOR:
                    for (auto i : s.args) v ^= values[i]; if (s.op == Z3_OP_BXNOR) v ^= s.mask; break;
                case Z3_OP_BADD: for (auto i : s.args) v += values[i]; break;
                case Z3_OP_BSUB: v = a(0) - a(1); break;
                case Z3_OP_BNEG: v = -a(0); break;
                case Z3_OP_BMUL: v = 1; for (auto i : s.args) v *= values[i]; break;
                case Z3_OP_BSHL: if (a(1) < s.width) v = a(0) << a(1).convert_to<unsigned>(); break;
                case Z3_OP_BLSHR: if (a(1) < s.width) v = a(0) >> a(1).convert_to<unsigned>(); break;
                case Z3_OP_BASHR:
                    if (a(1) >= s.width) v = sign(0) < 0 ? s.mask : Integer(0);
                    else v = floor_div(sign(0), Integer(1) << a(1).convert_to<unsigned>()); break;
                case Z3_OP_CONCAT: for (auto i : s.args) { v <<= m_steps.at(i).width; v |= values[i]; } break;
                case Z3_OP_EXTRACT: v = a(0) >> s.parameter; break;
                case Z3_OP_ZERO_EXT: v = a(0); break;
                case Z3_OP_SIGN_EXT: v = sign(0); break;
                case Z3_OP_REPEAT: for (unsigned i = 0; i < s.parameter; ++i) { v <<= sw(0); v |= a(0); } break;
                case Z3_OP_ROTATE_LEFT: case Z3_OP_ROTATE_RIGHT: case Z3_OP_EXT_ROTATE_LEFT: case Z3_OP_EXT_ROTATE_RIGHT:
                {
                    unsigned amount = s.args.size() == 2 ? (a(1) % s.width).convert_to<unsigned>() : s.parameter % s.width;
                    if (s.op == Z3_OP_ROTATE_RIGHT || s.op == Z3_OP_EXT_ROTATE_RIGHT) amount = (s.width - amount) % s.width;
                    v = (a(0) << amount) | (a(0) >> (s.width - amount)); break;
                }
                case Z3_OP_ULT: v = a(0) < a(1); break;
                case Z3_OP_ULEQ: v = a(0) <= a(1); break;
                case Z3_OP_UGT: v = a(0) > a(1); break;
                case Z3_OP_UGEQ: v = a(0) >= a(1); break;
                case Z3_OP_SLT: v = sign(0) < sign(1); break;
                case Z3_OP_SLEQ: v = sign(0) <= sign(1); break;
                case Z3_OP_SGT: v = sign(0) > sign(1); break;
                case Z3_OP_SGEQ: v = sign(0) >= sign(1); break;
                case Z3_OP_BUDIV: case Z3_OP_BUDIV_I: if (a(1) == 0) v = s.mask; else v = a(0) / a(1); break;
                case Z3_OP_BUREM: case Z3_OP_BUREM_I: if (a(1) == 0) v = a(0); else v = a(0) % a(1); break;
                case Z3_OP_BSDIV: case Z3_OP_BSDIV_I:
                { const auto x = sign(0), y = sign(1); if (y == 0) v = x < 0 ? Integer(1) : s.mask; else v = x / y; break; }
                case Z3_OP_BSREM: case Z3_OP_BSREM_I: case Z3_OP_BSMOD: case Z3_OP_BSMOD_I:
                {
                    const auto x = sign(0), y = sign(1); if (y == 0) v = x;
                    else { v = x % y; if ((s.op == Z3_OP_BSMOD || s.op == Z3_OP_BSMOD_I) && v != 0 && ((v < 0) != (y < 0))) v += y; }
                    break;
                }
                default: throw std::runtime_error("uncompiled evaluator operation");
            }
            // Avoid signed host overflow: all intermediates are mathematical
            // integers, and every BV instruction truncates to its own width.
            if (s.width) v = wrap(v, s.width);
            values.push_back(std::move(v));
        }
        return values.back();
    }
}
