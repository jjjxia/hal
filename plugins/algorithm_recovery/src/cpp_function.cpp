#include "algorithm_recovery/evaluation.h"
#include "algorithm_recovery/program.h"
#include <limits>
#include <regex>
#include <sstream>

namespace hal::algorithm_recovery
{
    namespace
    {
        void require(bool yes, const std::string& why) { if (!yes) throw std::runtime_error(why); }
        std::string operation_name(Z3_decl_kind kind)
        {
            switch (kind)
            {
                case Z3_OP_BNUM: return "BNUM";
                case Z3_OP_UNINTERPRETED: return "UNINTERPRETED";
                case Z3_OP_EXTRACT: return "EXTRACT";
                case Z3_OP_REPEAT: return "REPEAT";
                case Z3_OP_ROTATE_LEFT: return "ROTATE_LEFT";
                case Z3_OP_ROTATE_RIGHT: return "ROTATE_RIGHT";
                case Z3_OP_TRUE: return "TRUE";
                case Z3_OP_FALSE: return "FALSE";
                case Z3_OP_NOT: return "NOT";
                case Z3_OP_AND: return "AND";
                case Z3_OP_OR: return "OR";
                case Z3_OP_XOR: return "XOR";
                case Z3_OP_IMPLIES: return "IMPLIES";
                case Z3_OP_EQ: return "EQ";
                case Z3_OP_DISTINCT: return "DISTINCT";
                case Z3_OP_ITE: return "ITE";
                case Z3_OP_BNOT: return "BNOT";
                case Z3_OP_BAND: return "BAND";
                case Z3_OP_BOR: return "BOR";
                case Z3_OP_BXOR: return "BXOR";
                case Z3_OP_BNAND: return "BNAND";
                case Z3_OP_BNOR: return "BNOR";
                case Z3_OP_BXNOR: return "BXNOR";
                case Z3_OP_BCOMP: return "BCOMP";
                case Z3_OP_BADD: return "BADD";
                case Z3_OP_BSUB: return "BSUB";
                case Z3_OP_BMUL: return "BMUL";
                case Z3_OP_BNEG: return "BNEG";
                case Z3_OP_BSHL: return "BSHL";
                case Z3_OP_BLSHR: return "BLSHR";
                case Z3_OP_BASHR: return "BASHR";
                case Z3_OP_CONCAT: return "CONCAT";
                case Z3_OP_ZERO_EXT: return "ZERO_EXT";
                case Z3_OP_SIGN_EXT: return "SIGN_EXT";
                case Z3_OP_EXT_ROTATE_LEFT: return "EXT_ROTATE_LEFT";
                case Z3_OP_EXT_ROTATE_RIGHT: return "EXT_ROTATE_RIGHT";
                case Z3_OP_ULT: return "ULT";
                case Z3_OP_ULEQ: return "ULEQ";
                case Z3_OP_UGT: return "UGT";
                case Z3_OP_UGEQ: return "UGEQ";
                case Z3_OP_SLT: return "SLT";
                case Z3_OP_SLEQ: return "SLEQ";
                case Z3_OP_SGT: return "SGT";
                case Z3_OP_SGEQ: return "SGEQ";
                case Z3_OP_BUDIV: return "BUDIV";
                case Z3_OP_BUREM: return "BUREM";
                case Z3_OP_BSDIV: return "BSDIV";
                case Z3_OP_BSREM: return "BSREM";
                case Z3_OP_BSMOD: return "BSMOD";
                case Z3_OP_BUDIV_I: return "BUDIV_I";
                case Z3_OP_BUREM_I: return "BUREM_I";
                case Z3_OP_BSDIV_I: return "BSDIV_I";
                case Z3_OP_BSREM_I: return "BSREM_I";
                case Z3_OP_BSMOD_I: return "BSMOD_I";
                default: throw std::runtime_error("unsupported C++ export operation");
            }
        }
        Z3_decl_kind operation(const std::string& name)
        {
            static const std::map<std::string, Z3_decl_kind> names{
                {"BNUM", Z3_OP_BNUM},
                {"UNINTERPRETED", Z3_OP_UNINTERPRETED},
                {"EXTRACT", Z3_OP_EXTRACT},
                {"REPEAT", Z3_OP_REPEAT},
                {"ROTATE_LEFT", Z3_OP_ROTATE_LEFT},
                {"ROTATE_RIGHT", Z3_OP_ROTATE_RIGHT},
                {"TRUE", Z3_OP_TRUE},
                {"FALSE", Z3_OP_FALSE},
                {"NOT", Z3_OP_NOT},
                {"AND", Z3_OP_AND},
                {"OR", Z3_OP_OR},
                {"XOR", Z3_OP_XOR},
                {"IMPLIES", Z3_OP_IMPLIES},
                {"EQ", Z3_OP_EQ},
                {"DISTINCT", Z3_OP_DISTINCT},
                {"ITE", Z3_OP_ITE},
                {"BNOT", Z3_OP_BNOT},
                {"BAND", Z3_OP_BAND},
                {"BOR", Z3_OP_BOR},
                {"BXOR", Z3_OP_BXOR},
                {"BNAND", Z3_OP_BNAND},
                {"BNOR", Z3_OP_BNOR},
                {"BXNOR", Z3_OP_BXNOR},
                {"BCOMP", Z3_OP_BCOMP},
                {"BADD", Z3_OP_BADD},
                {"BSUB", Z3_OP_BSUB},
                {"BMUL", Z3_OP_BMUL},
                {"BNEG", Z3_OP_BNEG},
                {"BSHL", Z3_OP_BSHL},
                {"BLSHR", Z3_OP_BLSHR},
                {"BASHR", Z3_OP_BASHR},
                {"CONCAT", Z3_OP_CONCAT},
                {"ZERO_EXT", Z3_OP_ZERO_EXT},
                {"SIGN_EXT", Z3_OP_SIGN_EXT},
                {"EXT_ROTATE_LEFT", Z3_OP_EXT_ROTATE_LEFT},
                {"EXT_ROTATE_RIGHT", Z3_OP_EXT_ROTATE_RIGHT},
                {"ULT", Z3_OP_ULT},
                {"ULEQ", Z3_OP_ULEQ},
                {"UGT", Z3_OP_UGT},
                {"UGEQ", Z3_OP_UGEQ},
                {"SLT", Z3_OP_SLT},
                {"SLEQ", Z3_OP_SLEQ},
                {"SGT", Z3_OP_SGT},
                {"SGEQ", Z3_OP_SGEQ},
                {"BUDIV", Z3_OP_BUDIV},
                {"BUREM", Z3_OP_BUREM},
                {"BSDIV", Z3_OP_BSDIV},
                {"BSREM", Z3_OP_BSREM},
                {"BSMOD", Z3_OP_BSMOD},
                {"BUDIV_I", Z3_OP_BUDIV_I},
                {"BUREM_I", Z3_OP_BUREM_I},
                {"BSDIV_I", Z3_OP_BSDIV_I},
                {"BSREM_I", Z3_OP_BSREM_I},
                {"BSMOD_I", Z3_OP_BSMOD_I}
            };
            return names.at(name);
        }
        unsigned number(const std::string& text)
        {
            require(!text.empty() && text.find_first_not_of("0123456789") == std::string::npos, "invalid C++ unsigned literal");
            require(text == "0" || text[0] != '0', "non-decimal C++ literal");
            const auto value = std::stoull(text); require(value <= std::numeric_limits<unsigned>::max(), "C++ literal overflow"); return value;
        }
    }
    std::string BitVectorProgram::cpp_function(const std::string& name) const
    {
        require(std::regex_match(name, std::regex("[A-Za-z_][A-Za-z_0-9]*")), "invalid generated function identifier");
        std::ostringstream out;
        out << "Integer " << name << "(const std::vector<Integer>& inputs)\n{\n"
            << "    std::vector<Value> values;\n    values.reserve(" << m_steps.size() << ");\n";
        for (const auto& step : m_steps)
        {
            out << "    append(values, inputs, Op::" << operation_name(step.op) << ", " << step.width << ", "
                << step.parameter << ", \"" << step.literal << "\", {";
            for (unsigned i = 0; i < step.args.size(); ++i) { if (i) out << ", "; out << step.args[i]; }
            out << "});\n";
        }
        out << "    return values.at(" << m_steps.size() - 1 << ").bits;\n}\n";
        return out.str();
    }

    z3::expr decode_cpp_function(const std::string& text, const std::vector<z3::expr>& inputs, z3::context& ctx)
    {
        if (text.find("    Integer sum(") != std::string::npos) return decode_arithmetic_cpp(text, inputs, ctx);
        // Parse the saved source, including its return index. No emitted line is
        // ignored, and the decoder never uses the compiler's private Step data.
        const std::regex header("Integer [A-Za-z_][A-Za-z_0-9]*\\(const std::vector<Integer>& inputs\\)");
        const std::regex reserve("    values\\.reserve\\(([0-9]+)\\);");
        const std::regex instruction("    append\\(values, inputs, Op::([A-Z_]+), ([0-9]+), ([0-9]+), \"([0-9]+)\", \\{([0-9, ]*)\\}\\);");
        const std::regex result("    return values\\.at\\(([0-9]+)\\)\\.bits;");
        std::istringstream stream(text); std::string line; std::smatch match;
        const auto next = [&]() { require(bool(std::getline(stream, line)), "truncated emitted C++ function"); };
        next(); require(std::regex_match(line, header), "invalid C++ function header");
        next(); require(line == "{", "invalid C++ function block");
        next(); require(line == "    std::vector<Value> values;", "invalid C++ local declaration");
        next(); require(std::regex_match(line, match, reserve), "invalid C++ reserve statement");
        const auto count = number(match[1]); std::vector<z3::expr> values;
        while (true)
        {
            next(); if (std::regex_match(line, match, result)) break;
            require(std::regex_match(line, match, instruction), "unsupported emitted C++ statement");
            const auto op = operation(match[1]); const unsigned width = number(match[2]), parameter = number(match[3]);
            const std::string literal = match[4], indices = match[5];
            require(literal == "0" || literal[0] != '0', "non-decimal runtime literal");
            std::vector<z3::expr> args;
            if (!indices.empty())
            {
                size_t first = 0;
                while (first < indices.size())
                {
                    const auto end = indices.find(", ", first);
                    args.push_back(values.at(number(indices.substr(first, end == std::string::npos ? end : end - first))));
                    if (end == std::string::npos) break;
                    first = end + 2; require(first < indices.size(), "trailing C++ argument comma");
                }
            }
            const auto a = [&](unsigned i) -> const z3::expr& { return args.at(i); };
            const auto arity = [&](unsigned n) { require(args.size() == n, "wrong C++ operation arity"); };
            z3::expr_vector vector(ctx); for (const auto& arg : args) vector.push_back(arg);
            const auto fold = [&](auto combine) { require(!args.empty(), "empty C++ reduction"); auto v = args[0]; for (unsigned i = 1; i < args.size(); ++i) v = combine(v, args[i]); return v; };
            const auto binary = [&](auto combine) { arity(2); return combine(a(0), a(1)); };
            const auto unary = [&](auto combine) { arity(1); return combine(a(0)); };
            z3::expr term(ctx);
            switch (op)
            {
                case Z3_OP_BNUM: arity(0); require(width > 0, "Boolean numeral"); term = ctx.bv_val(literal.c_str(), width); break;
                case Z3_OP_UNINTERPRETED: arity(0); term = inputs.at(parameter); break;
                case Z3_OP_TRUE: arity(0); term = ctx.bool_val(true); break;
                case Z3_OP_FALSE: arity(0); term = ctx.bool_val(false); break;
                case Z3_OP_NOT: term = unary([](auto a) { return !a; }); break;
                case Z3_OP_AND: term = z3::mk_and(vector); break;
                case Z3_OP_OR: term = z3::mk_or(vector); break;
                case Z3_OP_XOR: term = fold([](auto a, auto b) { return a != b; }); break;
                case Z3_OP_IMPLIES: term = binary([](auto a, auto b) { return z3::implies(a, b); }); break;
                case Z3_OP_EQ: term = binary([](auto a, auto b) { return a == b; }); break;
                case Z3_OP_BCOMP: arity(2); term = z3::ite(a(0) == a(1), ctx.bv_val(1, 1), ctx.bv_val(0, 1)); break;
                case Z3_OP_DISTINCT: term = z3::distinct(vector); break;
                case Z3_OP_ITE: arity(3); term = z3::ite(a(0), a(1), a(2)); break;
                case Z3_OP_BNOT: term = unary([](auto a) { return ~a; }); break;
                case Z3_OP_BAND: case Z3_OP_BNAND: term = fold([](auto a, auto b) { return a & b; }); if (op == Z3_OP_BNAND) term = ~term; break;
                case Z3_OP_BOR: case Z3_OP_BNOR: term = fold([](auto a, auto b) { return a | b; }); if (op == Z3_OP_BNOR) term = ~term; break;
                case Z3_OP_BXOR: case Z3_OP_BXNOR: term = fold([](auto a, auto b) { return a ^ b; }); if (op == Z3_OP_BXNOR) term = ~term; break;
                case Z3_OP_BADD: term = fold([](auto a, auto b) { return a + b; }); break;
                case Z3_OP_BSUB: term = binary([](auto a, auto b) { return a - b; }); break;
                case Z3_OP_BMUL: term = fold([](auto a, auto b) { return a * b; }); break;
                case Z3_OP_BNEG: term = unary([](auto a) { return -a; }); break;
                case Z3_OP_BSHL: term = binary([](auto a, auto b) { return z3::shl(a, b); }); break;
                case Z3_OP_BLSHR: term = binary([](auto a, auto b) { return z3::lshr(a, b); }); break;
                case Z3_OP_BASHR: term = binary([](auto a, auto b) { return z3::ashr(a, b); }); break;
                case Z3_OP_CONCAT: require(!args.empty(), "empty concatenation"); term = z3::concat(vector); break;
                case Z3_OP_EXTRACT: arity(1); require(width > 0 && parameter <= a(0).get_sort().bv_size() && width <= a(0).get_sort().bv_size() - parameter, "bad C++ extract"); term = a(0).extract(parameter + width - 1, parameter); break;
                case Z3_OP_ZERO_EXT: case Z3_OP_SIGN_EXT:
                    arity(1); require(width >= a(0).get_sort().bv_size(), "negative extension");
                    term = op == Z3_OP_ZERO_EXT ? z3::zext(a(0), width - a(0).get_sort().bv_size()) : z3::sext(a(0), width - a(0).get_sort().bv_size()); break;
                case Z3_OP_REPEAT: arity(1); require(parameter > 0, "zero repeat"); term = z3::expr(ctx, Z3_mk_repeat(ctx, parameter, a(0))); break;
                case Z3_OP_ROTATE_LEFT: arity(1); term = z3::expr(ctx, Z3_mk_rotate_left(ctx, parameter, a(0))); break;
                case Z3_OP_ROTATE_RIGHT: arity(1); term = z3::expr(ctx, Z3_mk_rotate_right(ctx, parameter, a(0))); break;
                case Z3_OP_EXT_ROTATE_LEFT: arity(2); term = z3::expr(ctx, Z3_mk_ext_rotate_left(ctx, a(0), a(1))); break;
                case Z3_OP_EXT_ROTATE_RIGHT: arity(2); term = z3::expr(ctx, Z3_mk_ext_rotate_right(ctx, a(0), a(1))); break;
                case Z3_OP_ULT: term = binary([](auto a, auto b) { return z3::ult(a, b); }); break;
                case Z3_OP_ULEQ: term = binary([](auto a, auto b) { return z3::ule(a, b); }); break;
                case Z3_OP_UGT: term = binary([](auto a, auto b) { return z3::ugt(a, b); }); break;
                case Z3_OP_UGEQ: term = binary([](auto a, auto b) { return z3::uge(a, b); }); break;
                case Z3_OP_SLT: term = binary([](auto a, auto b) { return a < b; }); break;
                case Z3_OP_SLEQ: term = binary([](auto a, auto b) { return a <= b; }); break;
                case Z3_OP_SGT: term = binary([](auto a, auto b) { return a > b; }); break;
                case Z3_OP_SGEQ: term = binary([](auto a, auto b) { return a >= b; }); break;
                case Z3_OP_BUDIV: case Z3_OP_BUDIV_I: term = binary([](auto a, auto b) { return z3::udiv(a, b); }); break;
                case Z3_OP_BUREM: case Z3_OP_BUREM_I: term = binary([](auto a, auto b) { return z3::urem(a, b); }); break;
                case Z3_OP_BSDIV: case Z3_OP_BSDIV_I: term = binary([](auto a, auto b) { return a / b; }); break;
                case Z3_OP_BSREM: case Z3_OP_BSREM_I: term = binary([](auto a, auto b) { return z3::srem(a, b); }); break;
                case Z3_OP_BSMOD: case Z3_OP_BSMOD_I: arity(2); term = z3::expr(ctx, Z3_mk_bvsmod(ctx, a(0), a(1))); break;
                default: throw std::runtime_error("unsupported C++ instruction");
            }
            require((term.is_bool() && width == 0) || (term.is_bv() && term.get_sort().bv_size() == width), "decoded C++ width mismatch");
            values.push_back(term);
        }
        require(values.size() == count, "emitted C++ instruction count mismatch");
        const auto value = values.at(number(match[1]));
        next(); require(line == "}" && !std::getline(stream, line), "unexpected C++ trailing statement");
        return value;
    }
}
