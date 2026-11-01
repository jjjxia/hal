#pragma once
#include "algorithm_recovery/observations.h"
#include <nlohmann/json.hpp>
#include <filesystem>
#include <set>
#include <vector>

namespace hal::algorithm_recovery
{
    // Supplied input contract: pads listed as non-data keep their recorded
    // values in every admitted run; every other pad is free data.
    struct PadProtocol
    {
        std::set<PhysicalBit> non_data;
        std::string path, sha256;
        nlohmann::json description;
        static PadProtocol load(const std::filesystem::path& file, const OriginalCircuit& original);
    };

    // Additional taint imposed by certification refinement. A periodic rule
    // applies to every event t >= origin with (t - origin) mod period == phase.
    struct ForcedTaint
    {
        std::map<PhysicalBit, std::set<size_t>> events;
        std::set<std::tuple<PhysicalBit, size_t, size_t, size_t>> periodic; // bit, origin, period, phase
        bool applies(PhysicalBit bit, size_t t) const;
        size_t size() const;
    };

    // Cell-exact gate-level information flow over one recorded run. Free data
    // pads, unknown values and unknown initial state are tainted; RAM contents
    // use a sticky value-independent rule. Untainted nets have the same value
    // in every run under the protocol from the same configuration state.
    struct TaintTrace
    {
        std::map<PhysicalBit, u32> column;
        std::vector<std::vector<bool>> tainted, value; // [event][column], pre-edge frame
        nlohmann::json report;
        bool is_tainted(PhysicalBit bit, size_t t) const;
        int known(PhysicalBit bit, size_t t) const; // -1 if tainted, otherwise 0/1
    };

    Result<TaintTrace> compute_taint(OriginalCircuit& original, const Observations& trace, const PadProtocol& protocol,
                                     const ForcedTaint& forced = {});
}
