#pragma once
#include "algorithm_recovery/observations.h"
#include "algorithm_recovery/taint.h"

namespace hal
{
    class Netlist;
    namespace algorithm_recovery
    {
        struct GuidedWord
        {
            std::vector<PhysicalBit> q, d;
            std::map<u32, z3::expr> equations;
            std::vector<std::vector<FFUpdate>> updates;
            std::vector<PhysicalBit> control_cone; // actual SSE specialization controls
            bool numeric_order_known = false;
            std::set<u32> complete_update; // events whose write decision is data-dependent: E/R kept symbolic
        };
        struct GuidedAnalysis
        {
            std::map<std::string, GuidedWord> words;
            std::map<std::string, FoldInput> windows;
            std::map<u32, PhysicalBit> physical_nets;
            nlohmann::json report;
        };
        // Native HAL SSE queries under observed primitive-control conditions;
        // each specialization must be nonvacuous and universally equivalent to
        // original LUT/carry semantics. RAM ports remain unconditionally symbolic.
        Result<GuidedAnalysis> analyze_observations(Netlist* nl, const nlohmann::json& groups,
            OriginalCircuit& original, const Observations& trace, z3::context& ctx,
            const std::filesystem::path& output, u32 window_length = 512, const std::vector<std::string>& profiles = {},
            const TaintTrace* taint = nullptr);
    }
}
