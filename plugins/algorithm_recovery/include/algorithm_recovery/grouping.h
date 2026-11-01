#pragma once
#include "hal_core/utilities/result.h"
#include <nlohmann/json.hpp>

namespace hal
{
    class Netlist;
    namespace algorithm_recovery
    {
        struct GroupingResult
        {
            nlohmann::json dana, candidates, groups;
        };
        // Mutates module boundaries and destination pin orders in an imported
        // netlist. All register groups/MI seeds are recovered by native HAL.
        Result<GroupingResult> recover_groups(Netlist* nl, u32 dana_threads = 4);
    }
}
