#pragma once
#include "algorithm_recovery/folding.h"
#include <filesystem>
#include <nlohmann/json.hpp>

namespace hal::algorithm_recovery
{
    struct ProgramOptions
    {
        unsigned proof_timeout_ms = 60000, proof_resource_limit = 0;
    };
    // Decode only the exact emitted C++ subset, rejecting every other statement.
    // Inputs are typed symbolic values in the function's positional order.
    z3::expr decode_cpp_function(const std::string& text, const std::vector<z3::expr>& inputs, z3::context& ctx);
    Result<nlohmann::json> export_program(const FoldGraph& graph, const std::filesystem::path& output,
                                          const ProgramOptions& options = {});
    // Verify saved source/runtime/proof identities before compiling/replaying.
    Result<nlohmann::json> check_program_artifacts(const std::filesystem::path& directory);
}
