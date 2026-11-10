// Routing and table lifting of bit-level node functions.
#pragma once
#include "expr_eval.h"
#include "python_format.h"
#include <z3++.h>
#include <functional>
#include <map>
#include <optional>
#include <set>
#include <string>
#include <tuple>
#include <vector>

namespace program_recovery
{
    using BitRef = std::pair<std::string, unsigned>; // (symbol, bit)
    using IsParam = std::function<bool(const std::string&)>;

    // Syntactic support {symbol: bits}; a symbol used whole contributes all of its bits.
    std::map<std::string, std::set<unsigned>> symbol_bits(const z3::expr& e);
    std::vector<BitRef> sorted_support(const z3::expr& e);

    struct LiftTable
    {
        std::vector<BitRef> inputs;
        std::vector<unsigned> outputs;
        std::vector<BigInt> table;
    };

    struct LiftPlan
    {
        unsigned width = 0;
        BigInt cnst = 0;
        std::vector<std::tuple<unsigned, BitRef, int>> route;
        std::vector<std::tuple<unsigned, std::vector<BitRef>, int>> xr;
        std::vector<std::tuple<unsigned, std::vector<BitRef>, std::vector<int>>> param_route;
        std::vector<LiftTable> tables;
        std::vector<unsigned> keep;
        // bits of more than MAX_TABLE inputs written as an XOR of small tables and single bits (disjoint-support XOR
        // decomposition, checked on the bit's full truth table): per bit its single-bit terms and constant; each table
        // XORs into the bits listed as its outputs
        std::vector<std::tuple<unsigned, std::vector<BitRef>, int>> dsd_linear;
        std::vector<LiftTable> dsd_tables;
    };

    LiftPlan lift_plan(const z3::expr& expr, const IsParam& is_param);

    struct Whitened
    {
        std::vector<BitRef> a, b;
        std::vector<BigInt> table;
    };
    std::optional<Whitened> whiten(const LiftTable& t, const IsParam& is_param);

    // T[x] == scatter(S[gather(x, in_order)], out_order) for all x: the first (in, out) pair in
    // itertools.permutations order, as lift.canonical([S, T]) finds it; nullopt if none.
    std::optional<std::pair<std::vector<unsigned>, std::vector<unsigned>>> match_tables(
        const std::vector<BigInt>& S, const std::vector<BigInt>& T, unsigned k, unsigned m);
}
