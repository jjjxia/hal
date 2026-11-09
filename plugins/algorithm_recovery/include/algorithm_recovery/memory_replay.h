#pragma once
#include "algorithm_recovery/guided.h"
#include "hal_core/utilities/result.h"
#include <filesystem>

namespace hal::algorithm_recovery
{
    // Replays the extracted iCE40 RAM port functions over the original-gate trace and checks every observed
    // RDATA bit and the final RAM contents (HAL_PORT_RAM_STATE_ORIGINAL_GATE_REPLAY_PASS).
    struct MemoryReplay
    {
        // states[t] is before event t, including INIT at t=0 and the final state.
        std::vector<std::map<std::string, RamState>> states;
        nlohmann::json report;
    };
    Result<MemoryReplay> replay_memories(const GuidedAnalysis& analysis, const OriginalCircuit& original,
        const Observations& trace, const std::filesystem::path& final_storage_dump, const std::filesystem::path& output);
}
