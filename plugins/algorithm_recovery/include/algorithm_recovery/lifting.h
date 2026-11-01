#pragma once
#include "algorithm_recovery/arithmetic.h"
#include "algorithm_recovery/folding.h"
#include "algorithm_recovery/routing.h"
#include <filesystem>

namespace hal::algorithm_recovery
{
    // Annotate every raw folded node, saving canonical proof queries and their
    // bijective symbol bindings. The original graph is preserved on all paths.
    Result<nlohmann::json> lift_arithmetic(const FoldGraph& graph, ArithmeticRecovery& recovery,
                                          const std::filesystem::path& output,
                                          const RoutingOrders& orders = {});
}
