#pragma once
#include "hal_core/netlist/netlist.h"
#include "hal_core/utilities/result.h"
#include <nlohmann/json.hpp>
#include <filesystem>

namespace hal::algorithm_recovery
{
    struct ImportedNetlist
    {
        std::unique_ptr<Netlist> netlist;
        nlohmann::json receipt;
        std::map<u32, nlohmann::json> physical_bits;
    };
    // Native replacement for prepare/import/verify_import. Accepts one recovered
    // flat Yosys module, preserves cell insertion order, and checks HAL's actual
    // imported pins/parameters/truth tables. The output directory must be empty.
    Result<ImportedNetlist> import_recovered(const std::filesystem::path& source,
                                            const std::filesystem::path& library,
                                            const std::filesystem::path& output);
}
