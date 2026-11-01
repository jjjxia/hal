#pragma once
#include "algorithm_recovery/taint.h"
#include <z3++.h>

namespace hal::algorithm_recovery
{
    // Protocol-relative schedule certificate (NetNotary Section 6 / Theorem 1).
    // R_t fixes every untainted FF to its recorded value. Initiation is checked
    // against primitive power-up values, B_t on the prefix and C_j over one
    // period; every step also discharges the recorded value of each untainted
    // FF control net, which is exactly the condition of the conditional SSE
    // word proofs. Failed consequents are tainted and the analysis repeats,
    // giving an inductive subset (Houdini-style refinement).
    struct ScheduleCertificate
    {
        bool certified = false;
        u64 origin = 0, period = 0;
        TaintTrace taint;
        ForcedTaint forced;
        nlohmann::json report;
    };

    Result<ScheduleCertificate> certify_schedule(OriginalCircuit& original, const Observations& trace, const PadProtocol& protocol,
                                                 z3::context& ctx, const std::filesystem::path& output, size_t horizon, unsigned max_rounds = 32);
}
