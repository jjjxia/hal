#pragma once
#include <filesystem>
#include <nlohmann/json.hpp>

namespace hal::algorithm_recovery
{
    // Recover repeated update rules, check their complete finite expansion,
    // and emit a real C++ frame executor (no JSON graph interpreter on this path).
    nlohmann::json export_structured_schedule(const nlohmann::json& model, const std::filesystem::path& output);
}
