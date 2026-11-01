#pragma once
#include "hal_core/utilities/result.h"
#include "module_identification/types/candidate_types.h"
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
        struct GroupingOptions
        {
            u32 dana_threads = 4, mi_threads = 1, max_control_signals = 3;
            std::vector<module_identification::CandidateType> types = module_identification::all_checkable_candidate_types;
        };
        // Mutates module boundaries and destination pin orders in an imported
        // netlist. All register groups/MI seeds are recovered by native HAL.
        Result<GroupingResult> recover_groups(Netlist* nl, const GroupingOptions& options = {});
    }
}
