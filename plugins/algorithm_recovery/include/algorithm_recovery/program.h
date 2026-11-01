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
    // Actual executable word arithmetic, with raw/canonical coordinate changes.
    std::string arithmetic_cpp_function(const std::string& name, const nlohmann::json& lifted,
                                        const std::vector<std::pair<std::string, unsigned>>& inputs);
    z3::expr decode_arithmetic_cpp(const std::string& text, const std::vector<z3::expr>& inputs, z3::context& ctx);
    Result<nlohmann::json> export_program(const FoldGraph& graph, const std::filesystem::path& output,
                                          const ProgramOptions& options = {});
    Result<nlohmann::json> export_program(const FoldGraph& graph, const std::filesystem::path& output,
                                          const ProgramOptions& options, const nlohmann::json& arithmetic);
    // Verify saved source/runtime/proof identities before compiling/replaying.
    Result<nlohmann::json> check_program_artifacts(const std::filesystem::path& directory);
    // Re-synthesize an existing proved program without re-running grouping/SSE.
    // The saved source is decoded, not treated as an external equation oracle.
    Result<nlohmann::json> resynthesize_program(const std::filesystem::path& program,
        const std::filesystem::path& arithmetic, const std::filesystem::path& output,
        z3::context& ctx, const ProgramOptions& options = {});
    Result<nlohmann::json> replay_resynthesis(const std::filesystem::path& original,
        const std::filesystem::path& synthesized, const std::filesystem::path& output,
        unsigned frames = 8, const std::string& compiler = "c++", unsigned process_timeout_ms = 300000);
}
