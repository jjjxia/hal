#pragma once
#include <boost/multiprecision/cpp_int.hpp>
#include <map>
#include <string>
#include <vector>
#include <z3++.h>

namespace hal::algorithm_recovery
{
    using Integer = boost::multiprecision::cpp_int;
    Integer wrap(const Integer& value, unsigned width);
    Integer signed_value(const Integer& value, unsigned width);
    Integer floor_div(const Integer& numerator, const Integer& positive_denominator);

    // Native, arbitrary-width evaluation of the same Boolean/BV operations
    // supported by InvFold's SMT2 compiler. Unsupported ASTs fail at construction.
    // Sources and operations are taken from the actual Z3 DAG, never Python text.
    class BitVectorProgram
    {
    public:
        explicit BitVectorProgram(const z3::expr& expression);
        const std::vector<std::pair<std::string, unsigned>>& inputs() const { return m_inputs; }
        unsigned width() const { return m_steps.back().width; } // 0 means Boolean
        Integer evaluate(const std::vector<Integer>& inputs) const;
        Integer evaluate(const std::map<std::string, Integer>& inputs) const;
    private:
        struct Step
        {
            Z3_decl_kind op;
            unsigned width, parameter = 0;
            std::vector<unsigned> args;
            Integer literal = 0, mask = 0;
        };
        std::vector<Step> m_steps;
        std::vector<std::pair<std::string, unsigned>> m_inputs;
    };
}
