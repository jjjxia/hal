#include "algorithm_recovery/lifting.h"
#include "artifacts.h"
#include <iostream>

namespace hal::algorithm_recovery
{
    Result<nlohmann::json> lift_arithmetic(const FoldGraph& graph, ArithmeticRecovery& recovery, const std::filesystem::path& output)
    {
        try
        {
            using Json = nlohmann::json;
            const auto out = std::filesystem::absolute(output);
            artifacts::require(!std::filesystem::exists(out) || std::filesystem::is_empty(out), "arithmetic output directory must be empty");
            std::filesystem::create_directories(out / "proofs");
            const auto before = recovery.calls(), hits = recovery.cache_hits();
            Json rows = Json::array(); std::map<std::string, size_t> counts;
            for (const auto& node : graph.nodes)
            {
                const auto result = recovery.recover(node.equation, node.numeric_order_known);
                Json row = {{"id", {node.name, node.phase}}, {"width", node.equation.get_sort().bv_size()}, {"status", result.status},
                    {"raw_sha256", artifacts::sha256(node.equation.to_string())}, {"numeric_order_known", node.numeric_order_known},
                    {"cache_reused", result.reused}, {"evaluations", result.evaluations}};
                if (!result.reason.empty()) row["reason"] = result.reason;
                if (result.proposal) row[result.adopted() ? "fit" : "unadopted_candidate"] = result.proposal->to_json();
                if (!result.proof_query.empty())
                {
                    const auto hash = artifacts::sha256(result.proof_query), name = hash + ".smt2";
                    if (!std::filesystem::exists(out / "proofs" / name)) artifacts::write(out / "proofs" / name, result.proof_query);
                    row["proof"] = {{"file", (out / "proofs" / name).string()}, {"sha256", hash}, {"symbols", result.proof_symbols}};
                }
                ++counts[result.status]; rows.push_back(std::move(row));
                if (rows.size() % 500 == 0) std::cout << "LIFT " << rows.size() << '/' << graph.nodes.size() << ' ' << Json(counts).dump() << std::endl;
            }
            Json report = {{"status", graph.available ? "ARITHMETIC_CANDIDATES_CHECKED" : "NO_GRAPH"}, {"counts", counts}, {"nodes", rows},
                {"fit_calls", recovery.calls() - before}, {"cache_hits", recovery.cache_hits() - hits},
                {"scope", "Per-node universal equivalence within the existing observed-control graph; only FIT_PROVED is adopted. Raw equations retained; no new numeric orders or whole-device claim."}};
            artifacts::write(out / "arithmetic.json", report.dump(2) + '\n'); return OK(std::move(report));
        }
        catch (const std::exception& error) { return ERR(error.what()); }
    }
}
