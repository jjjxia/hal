#include "algorithm_recovery/program.h"
#include "algorithm_recovery/arithmetic.h"
#include "algorithm_recovery/routing.h"
#include "../src/artifacts.h"
#include <fstream>
#include <iostream>

using namespace hal::algorithm_recovery;
using Json = nlohmann::json;
namespace fs = std::filesystem;
template<class T> static T unwrap(hal::Result<T>&& value)
{ if (value.is_error()) throw std::runtime_error(value.get_error().get()); return std::move(value.get()); }
static void check(bool condition, const std::string& why) { artifacts::require(condition, why); }

int main(int argc, char** argv)
{
    try
    {
        if (argc == 4 && std::string(argv[1]) == "verify")
        {
            std::ifstream expected(argv[2]), actual(argv[3]); check(bool(expected) && bool(actual), "missing compiled results");
            std::string a, b; size_t count = 0;
            while (std::getline(expected, a))
            { check(bool(std::getline(actual, b)) && Json::parse(a) == Json::parse(b), "compiled word arithmetic mismatch"); ++count; }
            check(!std::getline(actual, b), "extra compiled result"); std::cout << "PASS " << count << " compiled arithmetic cases\n"; return 0;
        }
        if (argc != 3 || std::string(argv[1]) != "generate") return 2;
        const fs::path out(argv[2]); fs::remove_all(out); fs::create_directories(out / "fits");
        z3::context ctx; FoldGraph graph; graph.available = true; graph.period = 1;
        Json lifting{{"status", "ARITHMETIC_CANDIDATES_CHECKED"}, {"nodes", Json::array()}};
        for (unsigned kind = 0; kind < 7; ++kind)
        {
            const unsigned width = kind == 4 ? 80 : 8;
            const std::string name = "case_" + std::to_string(kind), xn = name + "_x", yn = name + "_y";
            auto x = ctx.bv_const(xn.c_str(), width), y = ctx.bv_const(yn.c_str(), width);
            ArithmeticModel model; model.width = width; model.mode = "exact"; model.coefficients[xn] = 1; model.constant = 1;
            z3::expr raw = x + 1; Json extra = Json::object();
            if (kind == 1 || kind == 5)
            {
                model.mode = kind == 1 ? "floor" : "round"; model.coefficients[xn] = Rational(3, 4);
                model.coefficients[yn] = Rational(-1, 2); model.constant = Rational(-3, 4);
                const auto sum = 3 * z3::sext(x, 16) - 2 * z3::sext(y, 16) - (kind == 1 ? 3 : 1);
                raw = z3::ashr(sum, 2).extract(7, 0);
            }
            if (kind == 2)
            {
                model.mode = "floor"; model.unsigned_inputs.insert(xn); model.coefficients[xn] = Rational(1, 2); model.constant = 0;
                raw = z3::lshr(x, 1);
            }
            if (kind == 3)
            {
                const BitPermutation input{7, 0, 5, 1, 3, 6, 2, 4}, output{3, 2, 1, 0, 7, 6, 5, 4};
                raw = unpermute_bits(permute_bits(x, input) + 1, output);
                extra["input_permutations"][xn] = {{"word", "source"}, {"permutation", input}}; extra["output_permutation"] = output;
            }
            if (kind == 6) raw = x ^ y; // Exact fallback: not an affine model.
            const BitVectorProgram program(raw); std::map<std::string, Source> sources;
            for (const auto& [symbol, bits] : program.inputs()) sources.emplace(symbol, Source{SourceKind::input, symbol, 0, 0});
            graph.nodes.push_back({name, 0, raw, sources, true});
            Json row{{"id", {name, 0}}, {"width", width}, {"raw_sha256", artifacts::sha256(raw.to_string())},
                     {"numeric_order_known", true}, {"status", kind == 6 ? "RAW_OPAQUE" : "FIT_PROVED"}};
            if (kind != 6)
            {
                std::map<std::string, z3::expr> values{{xn, x}, {yn, y}};
                auto canonical = raw;
                if (kind == 3) canonical = permute_bits(x, extra["input_permutations"][xn]["permutation"].get<BitPermutation>()) + 1;
                auto proposal = model;
                // Routing case proof refers to canonical independent coordinates.
                const auto fitted_raw = kind == 3 ? x + 1 : canonical;
                const auto proof = verify_arithmetic(fitted_raw, proposal); check(proof.status == "FIT_PROVED", "invalid fixture arithmetic identity");
                const auto file = out / "fits" / (name + ".smt2"); artifacts::write(file, proof.proof_query);
                row["fit"] = model.to_json(); row["proof"] = {{"file", file.string()}, {"sha256", artifacts::file_hash(file)}};
                row.update(extra);
            }
            lifting["nodes"].push_back(row);
        }
        const auto report = unwrap(export_program(graph, out / "program", {}, lifting));
        check(report.at("arithmetic_nodes") == 6, "arithmetic not used by executable export");
        const auto model = unwrap(check_program_artifacts(out / "program"));
        artifacts::write(out / "arithmetic.json", lifting.dump(2) + '\n');
        const auto resynthesized = unwrap(resynthesize_program(out / "program", out / "arithmetic.json", out / "resynthesized", ctx));
        check(resynthesized.at("arithmetic_nodes") == 6, "resynthesis lost executable arithmetic");
        const auto new_model = unwrap(check_program_artifacts(out / "resynthesized"));
        for (size_t i = 0; i < graph.nodes.size(); ++i)
            check(new_model.at("nodes").at(i).at("kernel") == model.at("nodes").at(i).at("kernel"), "fixture kernel inventory unexpectedly changed");
        std::ofstream requests(out / "program" / "requests.jsonl"), expected(out / "program" / "expected.jsonl");
        for (unsigned node = 0; node < graph.nodes.size(); ++node)
        {
            const auto& raw = graph.nodes[node].equation; const BitVectorProgram program(raw);
            for (unsigned x = 0; x < 256; ++x) for (unsigned y : {0u, 1u, 127u, 128u, 255u})
            {
                Json inputs = Json::array(); z3::expr_vector from(ctx), to(ctx);
                for (const auto& [symbol, width] : program.inputs())
                {
                    Integer value = symbol.back() == 'x' ? x : y;
                    if (width == 80 && (x & 1)) value += (Integer(1) << 79);
                    inputs.push_back(value.str()); from.push_back(ctx.bv_const(symbol.c_str(), width)); to.push_back(ctx.bv_val(value.str().c_str(), width));
                }
                auto value = raw; value = value.substitute(from, to).simplify();
                requests << Json{{"command", "kernel"}, {"kernel", model.at("nodes").at(node).at("kernel")}, {"inputs", inputs}}.dump() << '\n';
                expected << Json{{"value", Z3_get_numeral_string(ctx, value)}}.dump() << '\n';
            }
        }
        auto x = ctx.bv_const("case_0_x", 8);
        auto body = arithmetic_cpp_function("mutant", lifting.at("nodes")[0], {{"case_0_x", 8}});
        const auto pos = body.find("Integer sum(\"1\")"); check(pos != std::string::npos, "fixture constant absent");
        body.replace(pos, std::string("Integer sum(\"1\")").size(), "Integer sum(\"2\")");
        z3::solver counterexample(ctx); counterexample.add(decode_cpp_function(body, {x}, ctx) != x + 1);
        check(counterexample.check() == z3::sat, "saved arithmetic mutation not detected");
        body.insert(body.find("    return"), "    sum = 0;\n"); bool rejected = false;
        try { decode_cpp_function(body, {x}, ctx); } catch (const std::exception&) { rejected = true; }
        check(rejected, "injected arithmetic statement accepted");
        auto stale = lifting; stale["nodes"][0]["raw_sha256"] = "wrong";
        check(export_program(graph, out / "stale", {}, stale).is_error(), "stale arithmetic identity accepted");
        auto wrong = lifting; wrong["nodes"][0]["fit"]["constant"] = {"2", "1"};
        check(export_program(graph, out / "wrong", {}, wrong).is_error(), "wrong arithmetic adopted through a copied proof receipt");
        const auto replay = unwrap(replay_resynthesis(out / "program", out / "resynthesized", out / "differential", 3));
        check(replay.at("frames") == 3 && replay.at("static_binding_conservation_checked") == true,
              "compiled resynthesis comparison did not cover complete frames");
        std::cout << "PASS native arithmetic synthesis and negative proof checks\n"; return 0;
    }
    catch (const std::exception& error) { std::cerr << error.what() << '\n'; return 1; }
}
