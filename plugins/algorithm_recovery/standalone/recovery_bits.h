#pragma once
// Standalone fixed-width operations used by generated C++ functions.
// No HAL, Z3, Python interpreter or external model is needed at runtime.
#include <boost/multiprecision/cpp_int.hpp>
#include <initializer_list>
#include <set>
#include <stdexcept>
#include <string>
#include <vector>

namespace hal_recovered
{
    using Integer = boost::multiprecision::cpp_int;
    inline void require(bool yes, const std::string& why) { if (!yes) throw std::runtime_error(why); }
    inline Integer wrap(const Integer& value, unsigned width)
    {
        require(width > 0, "zero-width bit vector");
        const Integer modulus = Integer(1) << width; Integer result = value % modulus;
        if (result < 0) result += modulus;
        return result;
    }
    inline Integer signed_value(const Integer& value, unsigned width)
    {
        Integer result = wrap(value, width);
        if (boost::multiprecision::bit_test(result, width - 1)) result -= Integer(1) << width;
        return result;
    }
    inline Integer floor_div(const Integer& numerator, const Integer& denominator)
    {
        require(denominator > 0, "floor division requires a positive denominator");
        Integer q = numerator / denominator;
        if (numerator < 0 && numerator % denominator != 0) --q;
        return q;
    }

    enum class Op { BNUM, UNINTERPRETED, EXTRACT, REPEAT, ROTATE_LEFT, ROTATE_RIGHT, TRUE, FALSE, NOT, AND, OR, XOR, IMPLIES, EQ, DISTINCT, ITE, BNOT, BAND, BOR, BXOR, BNAND, BNOR, BXNOR, BCOMP, BADD, BSUB, BMUL, BNEG, BSHL, BLSHR, BASHR, CONCAT, ZERO_EXT, SIGN_EXT, EXT_ROTATE_LEFT, EXT_ROTATE_RIGHT, ULT, ULEQ, UGT, UGEQ, SLT, SLEQ, SGT, SGEQ, BUDIV, BUREM, BSDIV, BSREM, BSMOD, BUDIV_I, BUREM_I, BSDIV_I, BSREM_I, BSMOD_I };
    struct Value { Integer bits; unsigned width; };
    inline void append(std::vector<Value>& values, const std::vector<Integer>& inputs, Op op,
                       unsigned width, unsigned parameter, const char* literal, std::initializer_list<unsigned> args)
    {
        struct Step { Op op; unsigned width, parameter; std::vector<unsigned> args; Integer literal, mask; };
        const Step s{op, width, parameter, args, Integer(literal), width ? (Integer(1) << width) - 1 : Integer(1)};
            auto a = [&](unsigned i) -> const Integer& { return values.at(s.args.at(i)).bits; };
            auto sw = [&](unsigned i) { return values.at(s.args.at(i)).width; };
            auto sign = [&](unsigned i) { return signed_value(a(i), sw(i)); };
            Integer v = 0;
            switch (s.op)
            {
                case Op::BNUM: v = s.literal; break;
                case Op::UNINTERPRETED:
                    v = inputs.at(s.parameter); if (s.width) v = wrap(v, s.width);
                    else require(v == 0 || v == 1, "invalid Boolean input"); break;
                case Op::TRUE: v = 1; break;
                case Op::FALSE: v = 0; break;
                case Op::NOT: v = a(0) == 0; break;
                case Op::AND: v = 1; for (auto i : s.args) v = v != 0 && values[i].bits != 0; break;
                case Op::OR: for (auto i : s.args) v = v != 0 || values[i].bits != 0; break;
                case Op::XOR: for (auto i : s.args) v = (v != 0) != (values[i].bits != 0); break;
                case Op::IMPLIES: v = a(0) == 0 || a(1) != 0; break;
                case Op::EQ: case Op::BCOMP: v = a(0) == a(1); break;
                case Op::DISTINCT: { std::set<Integer> all; for (auto i : s.args) all.insert(values[i].bits); v = all.size() == s.args.size(); break; }
                case Op::ITE: v = a(0) != 0 ? a(1) : a(2); break;
                case Op::BNOT: v = a(0) ^ s.mask; break;
                case Op::BAND: case Op::BNAND:
                    v = s.mask; for (auto i : s.args) v &= values[i].bits; if (s.op == Op::BNAND) v ^= s.mask; break;
                case Op::BOR: case Op::BNOR:
                    for (auto i : s.args) v |= values[i].bits; if (s.op == Op::BNOR) v ^= s.mask; break;
                case Op::BXOR: case Op::BXNOR:
                    for (auto i : s.args) v ^= values[i].bits; if (s.op == Op::BXNOR) v ^= s.mask; break;
                case Op::BADD: for (auto i : s.args) v += values[i].bits; break;
                case Op::BSUB: v = a(0) - a(1); break;
                case Op::BNEG: v = -a(0); break;
                case Op::BMUL: v = 1; for (auto i : s.args) v *= values[i].bits; break;
                case Op::BSHL: if (a(1) < s.width) v = a(0) << a(1).convert_to<unsigned>(); break;
                case Op::BLSHR: if (a(1) < s.width) v = a(0) >> a(1).convert_to<unsigned>(); break;
                case Op::BASHR:
                    if (a(1) >= s.width) v = sign(0) < 0 ? s.mask : Integer(0);
                    else v = floor_div(sign(0), Integer(1) << a(1).convert_to<unsigned>()); break;
                case Op::CONCAT: for (auto i : s.args) { v <<= values.at(i).width; v |= values[i].bits; } break;
                case Op::EXTRACT: v = a(0) >> s.parameter; break;
                case Op::ZERO_EXT: v = a(0); break;
                case Op::SIGN_EXT: v = sign(0); break;
                case Op::REPEAT: for (unsigned i = 0; i < s.parameter; ++i) { v <<= sw(0); v |= a(0); } break;
                case Op::ROTATE_LEFT: case Op::ROTATE_RIGHT: case Op::EXT_ROTATE_LEFT: case Op::EXT_ROTATE_RIGHT:
                {
                    unsigned amount = s.args.size() == 2 ? (a(1) % s.width).convert_to<unsigned>() : s.parameter % s.width;
                    if (s.op == Op::ROTATE_RIGHT || s.op == Op::EXT_ROTATE_RIGHT) amount = (s.width - amount) % s.width;
                    v = (a(0) << amount) | (a(0) >> (s.width - amount)); break;
                }
                case Op::ULT: v = a(0) < a(1); break;
                case Op::ULEQ: v = a(0) <= a(1); break;
                case Op::UGT: v = a(0) > a(1); break;
                case Op::UGEQ: v = a(0) >= a(1); break;
                case Op::SLT: v = sign(0) < sign(1); break;
                case Op::SLEQ: v = sign(0) <= sign(1); break;
                case Op::SGT: v = sign(0) > sign(1); break;
                case Op::SGEQ: v = sign(0) >= sign(1); break;
                case Op::BUDIV: case Op::BUDIV_I: if (a(1) == 0) v = s.mask; else v = a(0) / a(1); break;
                case Op::BUREM: case Op::BUREM_I: if (a(1) == 0) v = a(0); else v = a(0) % a(1); break;
                case Op::BSDIV: case Op::BSDIV_I:
                { const auto x = sign(0), y = sign(1); if (y == 0) v = x < 0 ? Integer(1) : s.mask; else v = x / y; break; }
                case Op::BSREM: case Op::BSREM_I: case Op::BSMOD: case Op::BSMOD_I:
                {
                    const auto x = sign(0), y = sign(1); if (y == 0) v = x;
                    else { v = x % y; if ((s.op == Op::BSMOD || s.op == Op::BSMOD_I) && v != 0 && ((v < 0) != (y < 0))) v += y; }
                    break;
                }
                default: throw std::runtime_error("uncompiled evaluator operation");
            }
            // Avoid signed host overflow: all intermediates are mathematical
            // integers, and every BV instruction truncates to its own width.
            if (s.width) v = wrap(v, s.width);
            values.push_back({std::move(v), width});

    }
}
