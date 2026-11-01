#pragma once
#include "algorithm_recovery/folding.h"
#include <filesystem>
#include <nlohmann/json.hpp>

namespace hal::algorithm_recovery
{
    // raw bit j occupies canonical bit permutation[j]. These permutations are
    // separate from the executable graph's unchanged physical coordinates.
    using BitPermutation = std::vector<unsigned>;
    z3::expr permute_bits(const z3::expr& value, const BitPermutation& permutation);
    z3::expr unpermute_bits(const z3::expr& value, const BitPermutation& permutation);

    struct RoutingOptions
    {
        unsigned proof_timeout_ms = 30000;
        unsigned proof_resource_limit = 0;
    };
    struct RoutingOrders
    {
        std::map<std::string, BitPermutation> accepted;
        nlohmann::json report;
    };

    // Native InvFold one-hot proposals, followed by universal full-word copy
    // checks for every write. Only already established source orders propagate.
    // Word membership/order flags come directly from the preceding HAL window.
    Result<RoutingOrders> recover_routing(const FoldGraph& graph, const FoldInput& input,
                                         const std::filesystem::path& output,
                                         const RoutingOptions& options = {});

    // Fit a canonical-coordinate expression while preserving the raw graph.
    // Both output and source coordinates must be transformed, including uses
    // of an ordered source by a node whose own output order was already known.
    z3::expr canonical_equation(const FoldNode& node, const RoutingOrders& orders);
}
