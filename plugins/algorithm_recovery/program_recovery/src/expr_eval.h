// Concrete evaluation of z3 bit-vector/Boolean DAGs with arbitrary-width integers. Same semantics
// as z3 (and as the Python text the printer emits); used in place of z3 substitute+simplify.
#pragma once
#include "python_format.h"
#include <z3++.h>
#include <map>
#include <string>
#include <vector>

namespace program_recovery
{
    class ExprEval
    {
    public:
        explicit ExprEval(const z3::expr& e);
        // Unassigned symbols are 0 (as with Python's defaultdict(int) and lift.Evaluator).
        BigInt operator()(const std::map<std::string, BigInt>& values) const;
        const std::vector<std::string>& symbols() const { return m_symbols; }
        unsigned width() const { return m_width; }

    private:
        struct Step
        {
            Z3_decl_kind op;
            unsigned width = 1;      // 1 for Boolean
            unsigned p0 = 0, p1 = 0; // extract hi/lo, sign/zero-ext amount
            int symbol = -1;
            std::vector<unsigned> args;
            BigInt literal = 0;
        };
        std::vector<Step> m_steps;
        std::vector<std::string> m_symbols;
        unsigned m_width = 1;
    };
}
