#pragma once
#include <boost/multiprecision/cpp_int.hpp>
#include <map>
#include <string>
#include <vector>
#include <z3++.h>

namespace hal::algorithm_recovery
{
    using Integer = boost::multiprecision::cpp_int;

    // Native, arbitrary-width evaluation of a Boolean/bit-vector z3 term (used for the RAM port
    // functions in memory_replay.cpp). Unsupported ASTs fail at construction.
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
