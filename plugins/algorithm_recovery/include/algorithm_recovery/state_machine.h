#pragma once
#include "algorithm_recovery/guided.h"
#include "algorithm_recovery/arithmetic.h"
#include "algorithm_recovery/program.h"

namespace hal::algorithm_recovery
{
    struct StateMachineOptions
    {
        ProgramOptions program;
        ArithmeticOptions arithmetic;
        unsigned max_delta_events = 128;
    };
    // All FF storage, HAL-extracted control/data cones, observed guarded
    // specializations and unconditional fallbacks. No observed-period premise.
    Result<nlohmann::json> export_state_machine(Netlist* netlist, const nlohmann::json& groups,
        OriginalCircuit& original, const GuidedAnalysis& guided, const std::filesystem::path& output,
        z3::context& ctx, const StateMachineOptions& options = {});
    Result<nlohmann::json> check_state_artifacts(const std::filesystem::path& program);
    // Continuous execution from primitive initialization and explicit X choices;
    // only native pad stimulus enters. Compare every known original FF/RDATA bit
    // and final physical RAM cell; never copy trace state into the program.
    Result<nlohmann::json> replay_state_machine(const std::filesystem::path& program,
        const Observations& observations, const std::filesystem::path& trace_directory,
        const std::filesystem::path& output, const std::string& compiler = "c++",
        unsigned process_timeout_ms = 600000);
}
