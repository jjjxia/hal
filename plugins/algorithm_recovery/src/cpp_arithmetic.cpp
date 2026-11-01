#include "algorithm_recovery/program.h"
#include "algorithm_recovery/arithmetic.h"
#include "algorithm_recovery/routing.h"
#include "artifacts.h"
#include <regex>
#include <sstream>
#include <limits>

namespace hal::algorithm_recovery
{
    namespace
    {
        using artifacts::require;
        using Json = nlohmann::json;
        unsigned number(const std::string& text)
        {
            require(std::regex_match(text, std::regex("0|[1-9][0-9]*")), "invalid C++ decimal index");
            const auto n = std::stoull(text); require(n <= std::numeric_limits<unsigned>::max(), "C++ index overflow"); return n;
        }
        Integer integer(const std::string& text)
        {
            require(std::regex_match(text, std::regex("0|-?[1-9][0-9]*")), "invalid C++ decimal integer"); return Integer(text);
        }
        std::string literal(const Json& permutation)
        {
            const auto values = permutation.get<BitPermutation>(); std::ostringstream out; out << '{';
            for (unsigned i = 0; i < values.size(); ++i) { if (i) out << ", "; out << values[i]; }
            return out.str() + '}';
        }
        BitPermutation permutation(const std::string& text)
        {
            BitPermutation result; std::istringstream stream(text); std::string item;
            while (std::getline(stream, item, ','))
            {
                const auto first = item.find_first_not_of(' '); require(first != std::string::npos, "empty permutation entry");
                const auto last = item.find_last_not_of(' '); result.push_back(number(item.substr(first, last - first + 1)));
            }
            require(!result.empty() && text.back() != ',', "empty/trailing permutation"); return result;
        }
    }

    std::string arithmetic_cpp_function(const std::string& name, const Json& lifted,
                                        const std::vector<std::pair<std::string, unsigned>>& inputs)
    {
        require(std::regex_match(name, std::regex("[A-Za-z_][A-Za-z_0-9]*")), "invalid arithmetic function identifier");
        require(lifted.at("status") == "FIT_PROVED", "cannot execute an unproved arithmetic candidate");
        const auto model = ArithmeticModel::from_json(lifted.at("fit"));
        require(model.mode != "quant", "approximate arithmetic is not executable");
        const Rational offset = model.constant + (model.mode == "round" ? Rational(1, 2) : Rational(0));
        Integer denominator = offset.denominator();
        for (const auto& [key, value] : model.coefficients) denominator = std::max(denominator, value.denominator());
        require(denominator > 0 && (denominator & (denominator - 1)) == 0, "non-dyadic executable arithmetic");
        std::map<std::string, unsigned> indices;
        std::ostringstream out;
        out << "Integer " << name << "(const std::vector<Integer>& inputs)\n{\n";
        for (unsigned i = 0; i < inputs.size(); ++i)
        {
            require(inputs[i].second > 0 && indices.emplace(inputs[i].first, i).second, "invalid arithmetic source inventory");
            out << "    const Integer source_" << i << " = ";
            const bool permuted = lifted.contains("input_permutations") && lifted.at("input_permutations").contains(inputs[i].first);
            if (permuted) out << "permute_bits(";
            out << "inputs.at(" << i << ')';
            if (permuted) out << ", " << literal(lifted.at("input_permutations").at(inputs[i].first).at("permutation")) << ')';
            out << ";\n";
        }
        require(denominator % offset.denominator() == 0, "inexact arithmetic offset scale");
        const Integer constant = offset.numerator() * (denominator / offset.denominator());
        out << "    Integer sum(\"" << constant << "\");\n";
        for (const auto& [key, value] : model.coefficients)
        {
            const auto i = indices.at(key); require(denominator % value.denominator() == 0, "inexact arithmetic coefficient scale");
            const Integer coefficient = value.numerator() * (denominator / value.denominator());
            out << "    sum += Integer(\"" << coefficient << "\") * " << (model.unsigned_inputs.count(key) ? "wrap" : "signed_value")
                << "(source_" << i << ", " << inputs.at(i).second << ");\n";
        }
        out << "    const Integer result = wrap(floor_div(sum, Integer(\"" << denominator << "\")), " << model.width << ");\n";
        if (lifted.contains("output_permutation")) out << "    return unpermute_bits(result, " << literal(lifted.at("output_permutation")) << ");\n";
        else out << "    return result;\n";
        return out.str() + "}\n";
    }

    z3::expr decode_arithmetic_cpp(const std::string& text, const std::vector<z3::expr>& inputs, z3::context& ctx)
    {
        // Decode saved C++ statements, not emitter metadata. Unknown statements,
        // misplaced casts/indices, and malformed permutations are rejected.
        const std::regex header(R"(Integer [A-Za-z_][A-Za-z_0-9]*\(const std::vector<Integer>& inputs\))");
        const std::regex source(R"(    const Integer source_([0-9]+) = inputs.at\(([0-9]+)\);)");
        const std::regex permuted(R"(    const Integer source_([0-9]+) = permute_bits\(inputs.at\(([0-9]+)\), \{([0-9, ]*)\}\);)");
        const std::regex sum("    Integer sum\\(\"(-?[0-9]+)\"\\);");
        const std::regex term("    sum \\+= Integer\\(\"(-?[0-9]+)\"\\) \\* (wrap|signed_value)\\(source_([0-9]+), ([0-9]+)\\);");
        const std::regex result("    const Integer result = wrap\\(floor_div\\(sum, Integer\\(\"([0-9]+)\"\\)\\), ([0-9]+)\\);");
        const std::regex returned(R"(    return unpermute_bits\(result, \{([0-9, ]*)\}\);)");
        std::istringstream stream(text); std::string line; std::smatch match;
        const auto next = [&]() { require(bool(std::getline(stream, line)), "truncated arithmetic C++ function"); };
        next(); require(std::regex_match(line, header), "invalid arithmetic C++ header");
        next(); require(line == "{", "invalid arithmetic C++ block");
        std::map<std::string, z3::expr> values;
        for (unsigned i = 0; i < inputs.size(); ++i)
        {
            next(); z3::expr value = inputs.at(i); require(value.is_bv(), "non-word arithmetic input");
            if (std::regex_match(line, match, source))
                require(number(match[1]) == i && number(match[2]) == i, "arithmetic source position mismatch");
            else
            {
                require(std::regex_match(line, match, permuted), "invalid arithmetic source declaration");
                require(number(match[1]) == i && number(match[2]) == i, "permuted source position mismatch");
                value = permute_bits(value, permutation(match[3]));
            }
            values.emplace("source_" + std::to_string(i), value);
        }
        next(); require(std::regex_match(line, match, sum), "invalid arithmetic sum declaration");
        const auto constant = integer(match[1]);
        ArithmeticModel model; model.mode = "floor";
        std::map<std::string, Integer> coefficients;
        while (true)
        {
            next(); if (std::regex_match(line, match, result)) break;
            require(std::regex_match(line, match, term), "unsupported arithmetic C++ statement");
            const auto coefficient = integer(match[1]); const std::string cast = match[2];
            const auto index = number(match[3]), width = number(match[4]); const auto key = "source_" + std::to_string(index);
            require(inputs.at(index).get_sort().bv_size() == width, "arithmetic cast width mismatch");
            require(coefficients.emplace(key, coefficient).second, "duplicate arithmetic term");
            if (cast == "wrap") model.unsigned_inputs.insert(key);
        }
        const auto denominator = integer(match[1]); model.width = number(match[2]);
        require(denominator > 0, "nonpositive arithmetic denominator");
        model.constant = Rational(constant, denominator);
        for (const auto& [key, coefficient] : coefficients) model.coefficients.emplace(key, Rational(coefficient, denominator));
        auto expression = model.expression(values, ctx);
        next();
        if (line != "    return result;")
        {
            require(std::regex_match(line, match, returned), "invalid arithmetic return statement");
            expression = unpermute_bits(expression, permutation(match[1]));
        }
        next(); require(line == "}" && !std::getline(stream, line), "unexpected arithmetic C++ trailing statement");
        return expression;
    }
}
