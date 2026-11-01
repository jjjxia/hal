// Reference artifacts are read only here, never by the recovery plugin.
#include "algorithm_recovery/routing.h"
#include <fstream>
#include <iostream>

using namespace hal::algorithm_recovery;
using Json = nlohmann::json;
namespace fs = std::filesystem;
static void check(bool yes, const std::string& why) { if (!yes) throw std::runtime_error(why); }
static Json read(const fs::path& path)
{ std::ifstream in(path); check(bool(in), "cannot read " + path.string()); Json data; in >> data; return data; }
int main(int argc, char** argv)
{
    if (argc != 3 && argc != 4)
    { std::cerr << "Usage: check-recovery-routing-fixture old_case empty_output_directory [fresh_native_analysis_directory]\n"; return 2; }
    try
    {
        const fs::path root(argv[1]), output(argv[2]);
        check(!fs::exists(output) || fs::is_empty(output), "output must be empty"); fs::create_directories(output);
        const auto sse = read(root / "native_sse.json"); z3::context ctx; Json results = Json::object(); size_t groups = 0;
        for (const std::string profile : {"zero", "one", "pattern", "irregular"})
        {
            const auto folder = root / ("fold_" + profile);
            if (read(folder / "result.json").at("status") == "INSUFFICIENT_PERIODIC_WINDOW")
            {
                if (argc == 4) check(read(fs::path(argv[3]) / ("routing_" + profile) / "routing.json").at("status") == "NO_GRAPH", "absent graph invented");
                continue;
            }
            const auto expected = read(folder / "routing_orders" / "result.json");
            Json actual;
            if (argc == 4) actual = read(fs::path(argv[3]) / ("routing_" + profile) / "routing.json");
            else
            {
                const auto old = read(folder / "graph.json"); FoldInput input; FoldGraph graph; graph.available = true;
                for (auto it = old.at("words").begin(); it != old.at("words").end(); ++it)
                {
                    auto& word = input.words[it.key()];
                    for (const auto& bit : it.value()) word.bits.push_back(bit.dump());
                    word.numeric_order_known = sse.at("words").at(it.key()).at("order_origin") != "UNRESOLVED";
                }
                for (const auto& row : old.at("nodes"))
                {
                    const std::string name = row.at("id")[0], text = row.at("smt2"); const unsigned phase = row.at("id")[1];
                    std::string decl;
                    for (auto it = row.at("widths").begin(); it != row.at("widths").end(); ++it)
                        decl += "(declare-const |" + it.key() + "| (_ BitVec " + it.value().dump() + "))\n";
                    auto term = ctx.parse_string((decl + "(assert (= " + text + " " + text + "))").c_str())[0].arg(0);
                    std::map<std::string, Source> sources;
                    for (auto it = row.at("sources").begin(); it != row.at("sources").end(); ++it)
                    {
                        const auto& src = it.value(); const std::string kind = src[0];
                        check(kind == "sig" || kind == "ram" || kind == "in", "unsupported reference source");
                        sources.emplace(it.key(), Source{kind == "sig" ? SourceKind::signal : kind == "ram" ? SourceKind::ram : SourceKind::input, src[1], src[2], src[3]});
                    }
                    graph.nodes.push_back({name, phase, term, sources, input.words.at(name).numeric_order_known});
                }
                auto result = recover_routing(graph, input, output / profile);
                if (result.is_error()) throw std::runtime_error(result.get_error().get());
                actual = std::move(result.get().report);
            }
            check(actual.at("status") == expected.at("status"), "routing status changed");
            // These reference cases have no accepted routing orders. Positive
            // recovery is covered independently by the synthetic CTest.
            check(expected.at("accepted").empty() && actual.at("accepted").empty(), "unexpected numeric-order upgrade");
            check(actual.at("native_calls") == expected.at("native_calls"), "active routing candidate inventory changed");
            check(actual.at("proofs") == expected.at("proofs"), "unexpected routing proof inventory");
            for (auto it = expected.at("rejected").begin(); it != expected.at("rejected").end(); ++it)
            { check(actual.at("rejected").at(it.key()) == it.value(), "routing classification changed: " + it.key()); ++groups; }
            for (auto it = actual.at("rejected").begin(); it != actual.at("rejected").end(); ++it)
                if (!expected.at("rejected").contains(it.key())) check(it.value() == "NO_WRITES_IN_GRAPH", "new rejection in active graph");
            std::set<std::string> relative_actual, relative_expected;
            for (auto it = actual.at("relative_candidates").begin(); it != actual.at("relative_candidates").end(); ++it) relative_actual.insert(it.key());
            for (auto it = expected.at("relative_candidates").begin(); it != expected.at("relative_candidates").end(); ++it) relative_expected.insert(it.key());
            check(relative_actual == relative_expected, "relative proposal inventory changed");
            // A fresh HAL import can enumerate unresolved storage differently.
            // Only same-coordinate fixtures compare the relative permutations.
            if (argc == 3) check(actual.at("relative_candidates") == expected.at("relative_candidates"), "one-hot permutation differs from InvFold");
            check(actual.at("accepted_groups") == 0 && actual.at("accepted_bits") == 0, "inconsistent routing acceptance counts");
            const auto pending = actual.at("unresolved").get<std::set<std::string>>();
            for (const auto& name : expected.at("unresolved")) check(pending.count(name.get<std::string>()), "unknown order silently removed");
            results[profile] = {{"native_calls", actual.at("native_calls")}, {"relative_groups", relative_actual.size()}, {"accepted_groups", 0}};
            std::cout << "PASS " << profile << ' ' << results[profile].dump() << std::endl;
        }
        std::ofstream(output / "comparison.json") << results.dump(2) << '\n';
        std::cout << "PASS " << groups << " active unknown-order decisions match reference" << std::endl;
    }
    catch (const std::exception& error) { std::cerr << error.what() << '\n'; return 1; }
}
