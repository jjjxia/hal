#pragma once
#include "algorithm_recovery/guided.h"
#include "algorithm_recovery/program.h"

namespace hal::algorithm_recovery
{
    struct MemoryReplay
    {
        // states[t] is before event t, including INIT at t=0 and the final state.
        std::vector<std::map<std::string, RamState>> states;
        nlohmann::json report;
    };
    struct ReplayOptions
    {
        std::string compiler = "c++";
        unsigned process_timeout_ms = 300000;
        unsigned minimum_training_frames = 3, minimum_withheld_frames = 1;
    };
    Result<MemoryReplay> replay_memories(const GuidedAnalysis& analysis, const OriginalCircuit& original,
        const Observations& trace, const std::filesystem::path& final_storage_dump, const std::filesystem::path& output);
    // Compile and execute the saved artifact. Segment continuations use its
    // returned history/RAM state; observation values only initialize explicit
    // FF boundaries and histories. RAM reads never become replay inputs.
    Result<nlohmann::json> replay_program(const std::string& profile, const FoldGraph& graph, const GuidedAnalysis& analysis,
        const Observations& trace, const MemoryReplay& warm, const std::filesystem::path& program_directory,
        const std::filesystem::path& output, const ReplayOptions& options = {});
}
