// The legacy JSON is only regression evidence. This test runs the real native
// proposal generator and proof gate, and checks every formerly proved node.
#include "algorithm_recovery/lifting.h"
#include "../src/artifacts.h"
#include <fstream>
#include <iostream>

using namespace hal::algorithm_recovery;
using Json = nlohmann::json;
namespace fs = std::filesystem;
static void check(bool yes, const std::string& why) { if (!yes) throw std::runtime_error(why); }
static Json read(const fs::path& path) { Json data; std::ifstream(path) >> data; return data; }
int main(int argc, char** argv)
{
    if (argc != 3 && argc != 4) { std::cerr << "Usage: check-recovery-arithmetic-fixture old_case_directory empty_output_directory [fresh_native_analysis_directory]\n"; return 2; }
    try
    {
        const fs::path root(argv[1]), output(argv[2]);
        check(!fs::exists(output) || fs::is_empty(output), "output must be empty"); fs::create_directories(output);
        z3::context ctx; ArithmeticRecovery recovery;
        size_t preserved = 0, adopted = 0; Json results = Json::object(); std::set<std::string> checked_queries;
        for (const std::string profile : {"zero", "one", "pattern", "irregular"})
        {
            const auto prior_report = read(root / ("fold_" + profile) / "result.json");
            if (prior_report.at("status") == "INSUFFICIENT_PERIODIC_WINDOW")
            {
                if (argc == 4) check(read(fs::path(argv[3]) / ("lift_" + profile) / "arithmetic.json").at("status") == "NO_GRAPH", "unsupported graph was fabricated");
                continue;
            }
            const auto old = read(root / ("fold_" + profile) / "graph.json");
            check(old.at("fit_q_reorder").empty(), "routing coordinates require their separate migration regression");
            FoldGraph graph; graph.available = true; std::map<std::pair<std::string, unsigned>, std::string> expected;
            std::map<std::pair<std::string, unsigned>, unsigned> expected_widths;
            for (const auto& node : old.at("nodes"))
            {
                const std::string name = node.at("id")[0], status = node.at("fit_status"); const unsigned phase = node.at("id")[1];
                expected[{name, phase}] = status; expected_widths[{name, phase}] = node.at("width");
                if (argc == 4) continue;
                const auto widths = node.at("widths").get<std::map<std::string, unsigned>>(); std::string declarations;
                for (const auto& [name, width] : widths) declarations += "(declare-const |" + name + "| (_ BitVec " + std::to_string(width) + "))\n";
                const std::string text = node.at("smt2"); auto term = ctx.parse_string((declarations + "(assert (= " + text + " " + text + "))").c_str())[0].arg(0);
                graph.nodes.push_back({name, phase, term, {}, status != "RAW_UNORDERED"});
            }
            Json report;
            if (argc == 4) report = read(fs::path(argv[3]) / ("lift_" + profile) / "arithmetic.json");
            else
            {
                auto result = lift_arithmetic(graph, recovery, output / profile);
                if (result.is_error()) throw std::runtime_error(result.get_error().get()); report = std::move(result.get());
            }
            check(report.at("nodes").size() == expected.size(), "arithmetic node inventory changed");
            std::vector<Json> lost;
            std::set<std::pair<std::string, unsigned>> seen;
            for (const auto& row : report.at("nodes"))
            {
                const std::pair<std::string, unsigned> id{row.at("id")[0], row.at("id")[1]};
                check(seen.insert(id).second && row.at("width") == expected_widths.at(id), "duplicate node or changed width");
                const auto previous = expected.at(id);
                if (row.at("status") == "FIT_PROVED")
                {
                    ++adopted; const auto& proof = row.at("proof"); const std::string file = proof.at("file");
                    if (checked_queries.insert(file).second) check(artifacts::file_hash(file) == proof.at("sha256"), "changed arithmetic proof query");
                    check(row.contains("fit") && !row.contains("unadopted_candidate"), "proved model was not adopted");
                }
                else check(!row.contains("fit"), "unproved candidate was adopted");
                if (previous == "FIT_PROVED")
                {
                    if (row.at("status") != "FIT_PROVED") lost.push_back({row.at("id"), row.at("status")});
                    else ++preserved;
                }
                if (previous == "RAW_UNORDERED") check(row.at("status") == "RAW_UNORDERED", "unknown order was upgraded");
            }
            results[profile] = {{"counts", report.at("counts")}, {"fit_calls", report.at("fit_calls")}, {"lost", lost}};
            std::ofstream(output / "comparison.json") << results.dump(2) << '\n';
            std::cout << "CHECK " << profile << ' ' << results[profile].dump() << std::endl;
            check(lost.empty(), "formerly proved arithmetic was not recovered");
        }
        std::cout << "PASS preserved " << preserved << " previously proved nodes; " << adopted << " native adopted nodes; ";
        if (argc == 4) std::cout << checked_queries.size() << " proof-query identities checked on the fresh native run\n";
        else std::cout << recovery.calls() << " distinct fits\n";
    }
    catch (const std::exception& error) { std::cerr << error.what() << '\n'; return 1; }
}
