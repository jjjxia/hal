#include "algorithm_recovery/lifting.h"
#include <fstream>
#include <iostream>

using namespace hal::algorithm_recovery;
namespace fs = std::filesystem;
using Json = nlohmann::json;
static void check(bool yes, const std::string& why) { if (!yes) throw std::runtime_error(why); }
template<class F> static void rejects(F action)
{ bool failed = false; try { action(); } catch (const std::exception&) { failed = true; } check(failed, "invalid permutation accepted"); }
static void word(FoldInput& input, const std::string& name, unsigned width, bool known = false)
{
    auto& w = input.words[name]; w.numeric_order_known = known;
    for (unsigned i = 0; i < width; ++i) w.bits.push_back(name + ":" + std::to_string(i));
}
static void node(FoldGraph& graph, const std::string& name, unsigned phase, const z3::expr& expression,
                 const std::string& source, bool known = false)
{
    graph.nodes.push_back({name, phase, expression, {{"x", {SourceKind::signal, source, 0, 1}}}, known});
}
static RoutingOrders run(const FoldGraph& graph, const FoldInput& input, const fs::path& output, const RoutingOptions& options = {})
{
    auto result = recover_routing(graph, input, output, options);
    if (result.is_error()) throw std::runtime_error(result.get_error().get());
    return std::move(result.get());
}
int main(int argc, char** argv)
{
    if (argc != 2) return 2;
    try
    {
        const fs::path out(argv[1]); fs::remove_all(out); fs::create_directories(out);
        z3::context ctx; auto x = ctx.bv_const("x", 5);
        const BitPermutation p{2, 4, 0, 1, 3}, q{4, 1, 3, 0, 2};
        // Construct physical coordinates independently of permute_bits.
        const auto raw = z3::concat(x.extract(3, 3), z3::concat(x.extract(1, 1),
            z3::concat(x.extract(0, 0), z3::concat(x.extract(4, 4), x.extract(2, 2)))));
        z3::solver inverse(ctx); inverse.add(permute_bits(raw, p) != x);
        check(inverse.check() == z3::unsat, "raw-to-canonical orientation reversed");
        inverse.reset(); inverse.add(unpermute_bits(x, p) != raw);
        check(inverse.check() == z3::unsat, "canonical-to-raw orientation reversed");
        for (const BitPermutation invalid : {BitPermutation{}, BitPermutation{0, 1, 2, 3}, BitPermutation{0, 1, 2, 3, 3}, BitPermutation{0, 1, 2, 3, 5}})
            rejects([&] { permute_bits(x, invalid); });

        FoldInput input; word(input, "anchor", 5, true); word(input, "A_destination", 5); word(input, "Z_middle", 5);
        word(input, "sum", 5, true); word(input, "unused", 5);
        FoldGraph graph; graph.available = true;
        node(graph, "Z_middle", 0, raw, "anchor"); node(graph, "Z_middle", 1, raw, "anchor");
        node(graph, "A_destination", 0, unpermute_bits(permute_bits(x, p), q), "Z_middle");
        node(graph, "sum", 0, permute_bits(x, q) + 3, "A_destination", true);
        std::vector<std::string> originals; for (const auto& n : graph.nodes) originals.push_back(n.equation.to_string());
        const auto orders = run(graph, input, out / "chain");
        check(orders.accepted == std::map<std::string, BitPermutation>{{"Z_middle", p}, {"A_destination", q}}, "multi-round routing propagation failed");
        check(orders.report.at("accepted_bits") == 10 && orders.report.at("native_calls") == 3, "routing fixed-point accounting");
        check(orders.report.at("relative_candidates").empty(), "stale relative candidate retained after acceptance");
        check(orders.report.at("rejected").at("unused") == "NO_WRITES_IN_GRAPH", "missing graph writes hidden");
        size_t obligations = 0;
        for (const auto& rows : orders.report.at("proofs")) for (const auto& row : rows)
        {
            check(row.at("result") == "unsat", "unproved order accepted");
            const std::string file = row.at("query"); z3::solver proof(ctx); proof.add(ctx.parse_file(file.c_str()));
            check(proof.check() == z3::unsat, "saved routing proof failed replay"); ++obligations;
        }
        check(obligations == 3, "not every write was checked");
        for (unsigned i = 0; i < graph.nodes.size(); ++i)
        {
            const auto& n = graph.nodes[i];
            check(n.equation.to_string() == originals[i], "routing mutated executable raw graph");
            auto expected = n.name == "sum" ? x + 3 : x;
            z3::solver proof(ctx); proof.add(canonical_equation(n, orders) != expected);
            check(proof.check() == z3::unsat, "fitting coordinate transform is wrong");
        }
        ArithmeticRecovery arithmetic;
        auto lifted = lift_arithmetic(graph, arithmetic, out / "lift", orders);
        if (lifted.is_error()) throw std::runtime_error(lifted.get_error().get());
        check(lifted.get().at("counts").at("FIT_PROVED") == 4, "routing orders did not reach real arithmetic recovery");
        for (const auto& row : lifted.get().at("nodes"))
            if (row.at("id")[0] == "sum") check(row.contains("input_permutations") && !row.contains("output_permutation"), "known output omitted canonical input transform");

        FoldInput cycle; word(cycle, "a", 5); word(cycle, "b", 5);
        FoldGraph relative; relative.available = true;
        node(relative, "a", 0, raw, "b"); node(relative, "b", 0, raw, "a");
        const auto cyclic = run(relative, cycle, out / "cycle");
        check(cyclic.accepted.empty() && cyclic.report.at("relative_candidates").size() == 2, "unanchored cycle became numeric order");
        auto relative_lift = lift_arithmetic(relative, arithmetic, out / "cycle_lift", cyclic);
        check(relative_lift.is_ok() && relative_lift.get().at("counts").at("RAW_UNORDERED") == 2, "relative orders upgraded downstream");

        FoldInput bad_input; word(bad_input, "anchor", 5, true);
        for (const auto* name : {"dropped", "inconsistent", "nonzero", "duplicate", "primary", "multiple"}) word(bad_input, name, 5);
        word(bad_input, "partial", 4);
        FoldGraph bad; bad.available = true;
        node(bad, "dropped", 0, x & 15, "anchor");
        node(bad, "inconsistent", 0, raw, "anchor"); node(bad, "inconsistent", 1, x, "anchor");
        node(bad, "nonzero", 0, ~x, "anchor");
        node(bad, "duplicate", 0, z3::concat(x.extract(3, 0), x.extract(0, 0)), "anchor");
        node(bad, "partial", 0, x.extract(3, 0), "anchor");
        bad.nodes.push_back({"primary", 0, x, {{"x", {SourceKind::input, "pad", 0, 0}}}, false});
        bad.nodes.push_back({"multiple", 0, x ^ ctx.bv_const("y", 5),
            {{"x", {SourceKind::signal, "anchor", 0, 1}}, {"y", {SourceKind::signal, "anchor", 1, 1}}}, false});
        const auto rejected = run(bad, bad_input, out / "bad");
        check(rejected.accepted.empty(), "invalid routing proposal adopted");
        check(rejected.report.at("proofs").at("dropped")[0].at("result") == "sat", "missing bits were not refuted");
        check(rejected.report.at("rejected").at("partial") == "RELATIVE_ONLY_OR_NOT_FULL_WIDTH", "partial source falsely established full order");
        for (const auto* name : {"inconsistent", "nonzero", "duplicate", "primary", "multiple"})
            check(rejected.report.at("rejected").at(name) == "NATIVE_INFERENCE_NO_ORDER", std::string("bad one-hot inference: ") + name);

        // Every one-hot input passes this 80-bit nonlinear function. The
        // universal check still finds the deliberately rare multi-bit error.
        FoldInput wide_input; word(wide_input, "anchor", 80, true); word(wide_input, "target", 80);
        const auto wide = ctx.bv_const("x", 80); const auto rare = ctx.bv_val("1512366075204170929041", 80);
        FoldGraph deceptive; deceptive.available = true;
        node(deceptive, "target", 0, z3::ite(wide == rare, wide ^ 1, wide), "anchor");
        const auto refuted = run(deceptive, wide_input, out / "rare");
        check(refuted.accepted.empty() && refuted.report.at("proofs").at("target")[0].at("result") == "sat", "one-hot probe alone accepted nonlinear impostor");
        // Same one-hot pattern in both phases; second write alone is invalid.
        deceptive.nodes.insert(deceptive.nodes.begin(), {"target", 1, wide, {{"x", {SourceKind::signal, "anchor", 0, 1}}}, false});
        const auto late = run(deceptive, wide_input, out / "later_write");
        check(late.accepted.empty() && late.report.at("proofs").at("target").size() == 2, "only first write proved");
        FoldGraph difficult; difficult.available = true;
        node(difficult, "target", 0, wide * 3 - wide * 2, "anchor");
        RoutingOptions limited; limited.proof_resource_limit = 1;
        const auto unknown = run(difficult, wide_input, out / "unknown", limited);
        check(unknown.accepted.empty() && unknown.report.at("proofs").at("target")[0].at("result") == "unknown", "UNKNOWN upgraded to proof");
        const auto proved = run(difficult, wide_input, out / "wide");
        check(proved.accepted.at("target").size() == 80, "wide order lost");
        FoldGraph absent; const auto unavailable = run(absent, wide_input, out / "no_graph");
        check(unavailable.report.at("status") == "NO_GRAPH" && unavailable.accepted.empty(), "absent graph invented");
        auto malformed = input; malformed.words.at("anchor").bits.pop_back();
        check(recover_routing(graph, malformed, out / "malformed").is_error(), "inconsistent source width accepted");
        check(recover_routing(graph, input, out / "chain").is_error(), "nonempty evidence directory overwritten");
        std::cout << "PASS native routing: anchored multi-round chain, all-write proofs, canonical arithmetic, relative cycles, wide proposals, SAT/UNKNOWN and coordinate validation\n";
    }
    catch (const std::exception& error) { std::cerr << error.what() << '\n'; return 1; }
}
