// Word-level arithmetic lifting with inferred bit orders.
#pragma once
#include "lift_tables.h"
#include "python_random.h"

namespace program_recovery
{
    using Support = std::set<BitRef>;
    using Chain = std::vector<std::pair<unsigned, Support>>;

    struct Bitwise
    {
        std::vector<BigInt> table;
        std::vector<std::vector<BitRef>> roles;
        std::optional<std::string> name;
    };

    struct Candidate
    {
        std::vector<unsigned> outputs;
        std::vector<std::vector<std::optional<BitRef>>> operands;
        BigInt cnst = 0;
        std::vector<BigInt> linear;
        std::vector<std::pair<std::pair<unsigned, unsigned>, BigInt>> bilinear; // Python dict order
        unsigned width = 0;
        std::vector<bool> is_signed;
        bool order_ambiguous = false;
        std::string method; // empty: "nested_support_chain"
        std::optional<Bitwise> bitwise;
    };

    std::pair<std::vector<Chain>, std::vector<unsigned>> chains(const z3::expr& expr);
    std::optional<Candidate> fit_chain(const ExprEval& f, const Chain& chain, PyRandom& rng);
    std::optional<Candidate> fit_chain_arx(const ExprEval& f, const Chain& chain, PyRandom& rng, size_t max_linear = 6);
    std::optional<Candidate> fit_known(const ExprEval& f, unsigned n_out, const std::vector<std::vector<std::optional<BitRef>>>& operands,
                                       PyRandom& rng, const std::vector<unsigned>& outputs, unsigned samples = 64);
    z3::expr candidate_expr(const Candidate& cand, const std::map<std::string, z3::expr>& variables);
}
