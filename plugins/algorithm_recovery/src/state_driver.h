#pragma once
#include "algorithm_recovery/state_machine.h"

namespace hal::algorithm_recovery
{
    nlohmann::json export_state_driver(const std::filesystem::path& output, const nlohmann::json& schema,
        const nlohmann::json& data, const nlohmann::json& controls, unsigned max_delta_events);
}
