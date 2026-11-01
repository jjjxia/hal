// Exercise the actual compiled-program process and replay contract. The small
// fixture intentionally changes control, enable and reset while Q stays zero:
// output equality alone must not count these frames as applicable evidence.
#include "algorithm_recovery/replay.h"
#include "../src/artifacts.h"
#include "../src/process.h"
#include <iostream>
#include <unistd.h>

using namespace hal::algorithm_recovery;
using Json = nlohmann::json;
namespace fs = std::filesystem;
using artifacts::require;
template<class T> static T unwrap(hal::Result<T>&& result)
{ if (result.is_error()) throw std::runtime_error(result.get_error().get()); return std::move(result.get()); }
template<class F> static void rejects(F f, const std::string& diagnostic)
{
    try { f(); } catch (const std::exception& error) {
        require(std::string(error.what()).find(diagnostic) != std::string::npos, "wrong rejection: " + std::string(error.what())); return;
    }
    throw std::runtime_error("invalid replay/process was accepted: " + diagnostic);
}

int main(int argc, char** argv)
{
    if (argc == 2)
    {
        const std::string mode = argv[1];
        if (mode == "exit") return 3;
        if (mode == "stall") { sleep(60); return 0; }
        std::string line;
        while (std::getline(std::cin, line))
        {
            if (mode == "badjson") std::cout << "not json\n" << std::flush;
            else std::cout << Json::parse(line).dump() << '\n' << std::flush;
        }
        return 0;
    }
    if (argc != 6) return 2;
    try
    {
        const auto root = fs::absolute(argv[1]);
        require(!fs::exists(root) || fs::is_empty(root), "test output must be empty"); fs::create_directories(root);
        const auto self = fs::absolute(argv[0]).string();
        {
            process::JsonProcess child({self, "echo"}, root / "echo.log", 10000);
            const Json large{{"text", std::string(2 * 1024 * 1024, 'a')}};
            require(child.request(large) == large && child.request({{"small", 3}}).at("small") == 3, "JSON process transfer mismatch"); child.finish();
        }
        rejects([&] { process::JsonProcess child({self, "exit"}, root / "exit.log", 1000); child.request({}); }, "generated program");
        rejects([&] { process::JsonProcess child({self, "stall"}, root / "stall.log", 100); child.request({}); }, "timed out");
        rejects([&] { process::JsonProcess child({self, "badjson"}, root / "badjson.log", 1000); child.request({}); }, "parse_error");
        rejects([&] { process::run({(root / "absent-executable").string()}, root / "missing.log", 1000); }, "failed");

        Json module = {{"ports", Json::object()}, {"cells", Json::object()}};
        for (auto bit : {2, 3, 4, 5, 8}) module["ports"]["p" + std::to_string(bit)] = {{"direction", "input"}, {"bits", {bit}}};
        module["ports"]["q"] = {{"direction", "output"}, {"bits", {7, 9}}};
        module["cells"]["main"] = {{"type", "SB_DFFER"}, {"connections", {{"C", {2}}, {"E", {3}}, {"R", {4}}, {"D", {6}}, {"Q", {7}}}}};
        module["cells"]["aux"] = {{"type", "SB_DFFE"}, {"connections", {{"C", {2}}, {"E", {5}}, {"D", {"0"}}, {"Q", {9}}}}};
        module["cells"]["and"] = {{"type", "SB_LUT4"}, {"parameters", {{"LUT_INIT", "1000100010001000"}}},
            {"connections", {{"I0", {5}}, {"I1", {8}}, {"I2", {"0"}}, {"I3", {"0"}}, {"O", {6}}}}};
        const auto source = root / "original.json", tb_path = root / "test.v", dump = root / "native_ram_final.txt";
        artifacts::write(source, Json{{"modules", {{"test", module}}}}.dump());
        std::ostringstream tb;
        tb << "module test; reg C=0,E=1,R=0,K=0,D=0; wire Q,A,X; integer fd;\n"
           << "SB_DFFER main(.C(C),.E(E),.R(R),.D(X),.Q(Q));\n"
           << "SB_DFFE aux(.C(C),.E(K),.D(1'b0),.Q(A));\n"
           << "SB_LUT4 #(.LUT_INIT(16'h8888)) comb(.I0(K),.I1(D),.I2(1'b0),.I3(1'b0),.O(X));\n"
           << "wire [7:0] observed={A,D,Q,X,K,R,E,C};\n"
           << "initial begin fd=$fopen(" << Json((root / "native_trace.txt").string()).dump() << ",\"w\");\n";
        for (unsigned t = 0; t < 96; ++t)
            tb << "E=" << (t != 24) << ";R=" << (t == 0 || t == 28) << ";K=" << (t == 20) << ";D=0;#2;"
               << "$fwrite(fd,\"%b \",observed);C=" << (t % 2 == 0) << ";#2;$fwrite(fd,\"%b\\n\",observed);\n";
        tb << "$fclose(fd);$finish;end endmodule\n";
        artifacts::write(tb_path, tb.str()); artifacts::write(dump, "");
        process::run({argv[2], "-g2012", "-s", "test", "-o", (root / "sim").string(), tb_path.string(), argv[4]}, root / "iverilog.log", 10000);
        process::run({argv[3], (root / "sim").string()}, root / "sim.log", 10000);
        Json metadata{{"status", "ORIGINAL_GATE_TRACE"}, {"frames", 96}, {"observed", {2,3,4,5,6,7,8,9}},
            {"files", Json::object()}, {"windows", {{"case", Json::array()}}}};
        for (unsigned t = 16; t < 96; ++t) metadata["windows"]["case"].push_back(t);
        for (const auto& path : {source, tb_path, dump, root / "native_trace.txt", fs::absolute(argv[4])}) metadata["files"][path.string()] = artifacts::file_hash(path);
        artifacts::write(root / "native_trace.json", metadata.dump());
        auto trace = Observations::load(root); z3::context ctx; OriginalCircuit original(source, ctx);
        require(original.controls.count(5), "fixture control is not a real primitive control");
        GuidedAnalysis analysis; GuidedWord word; word.q = {7}; word.d = {6}; word.control_cone = {5}; word.numeric_order_known = true;
        for (unsigned t = 0; t < 96; ++t) word.updates.push_back({ff_update(original.flip_flops.at(7), trace, t)});
        FoldInput input; input.start = 48; input.length = 48; WordWindow window; window.bits = {"b7"}; window.numeric_order_known = true;
        for (unsigned t = 48; t < 96; ++t) { window.writes.push_back(word.updates[t][0].write); window.equations.push_back(ctx.bv_val(0, 1)); }
        input.words.emplace("main", window);
        for (auto bit : {2,3,4,5,8}) input.inputs.emplace("pad" + std::to_string(bit), std::vector<std::string>{"b" + std::to_string(bit)});
        analysis.words.emplace("main", std::move(word)); analysis.windows.emplace("case", input);
        z3::solver condition(ctx); condition.add(!original.function(5)); require(condition.check() == z3::sat, "vacuous test condition");
        z3::solver proof(ctx); proof.add(!original.function(5) && original.function(6)); require(proof.check() == z3::unsat, "fixture D equation is not proved");
        const auto query = root / "guided.smt2"; artifacts::write(query, proof.to_smt2());
        analysis.report = {{"observation_files", metadata["files"]}, {"proofs", Json::array({{{"result", "UNSAT"}, {"query", query.string()}, {"sha256", artifacts::file_hash(query)}}})}};
        const auto graph = unwrap(fold_observed(input, ctx)); require(graph.available && graph.period == 2, "fixture failed to fold");
        const auto warm = unwrap(replay_memories(analysis, original, trace, dump, root / "warm"));
        unwrap(export_program(graph, root / "program")); ReplayOptions options; options.compiler = argv[5];
        const auto report = unwrap(replay_program("case", graph, analysis, trace, warm, root / "program", root / "baseline", options));
        require(report.at("counts").at("training_frames") == 24 && report.at("counts").at("withheld_frames") == 13 &&
            report.at("counts").at("withheld_outside_contract") == 3, "control/hold/reset mismatches were accepted or good frames dropped");
        require(report.at("candidate_source") == "PROFILE_WINDOW" && report.at("candidate_events") == Json({{"start", 16}, {"length", 80}}),
            "native replay escaped its profile boundary");

        // The same bound observations can describe an external protocol whose
        // declared window contains training only. Reuse the already extracted
        // graph: earlier rows must be validated, never added to its equations.
        auto external = trace;
        external.metadata["stimulus_kind"] = "PHYSICAL_PAD_STIMULUS_V1";
        external.metadata["windows"]["case"] = Json::array();
        for (unsigned t = input.start; t < input.start + input.length; ++t) external.metadata["windows"]["case"].push_back(t);
        const auto saved_program_hash = artifacts::file_hash(root / "program" / "program.json");
        const auto external_report = unwrap(replay_program("case", graph, analysis, external, warm, root / "program", root / "external", options));
        require(external_report.at("counts").at("training_frames") == 24 && external_report.at("counts").at("withheld_frames") == 20 &&
            external_report.at("counts").at("withheld_outside_contract") == 4, "external heldout rows were lost or contract checks bypassed");
        require(external_report.at("candidate_source") == "ALL_BOUND_EXTERNAL_EVENTS" &&
            external_report.at("candidate_events") == Json({{"start", 0}, {"length", 96}}) &&
            external_report.at("extraction_events") == Json({{"start", 48}, {"length", 48}}) &&
            artifacts::file_hash(root / "program" / "program.json") == saved_program_hash &&
            analysis.windows.at("case").words.at("main").equations.size() == 48,
            "heldout replay changed or expanded training extraction");
        auto several = analysis; auto other = input; other.start = 32; other.length = 8;
        other.words.at("main").equations.erase(other.words.at("main").equations.begin() + other.length, other.words.at("main").equations.end());
        other.words.at("main").writes.resize(other.length);
        several.windows.emplace("other", std::move(other));
        auto several_trace = external; several_trace.metadata["windows"]["other"] = {32,33,34,35,36,37,38,39};
        const auto several_report = unwrap(replay_program("case", graph, several, several_trace, warm, root / "program", root / "external_several", options));
        require(several_report.at("counts").at("training_frames") == 24 && several_report.at("counts").at("withheld_frames") == 16 &&
            several_report.at("counts").at("overlap_other_training_frames") == 4,
            "another profile's extraction events counted as withheld evidence");
        auto external_wrong = external;
        external_wrong.post[2][external_wrong.post[2].size() - 1 - external_wrong.positions.at(7)] = '1';
        rejects([&] { unwrap(replay_program("case", graph, analysis, external_wrong, warm, root / "program", root / "external_wrong", options)); },
            "known-bit replay mismatch");
        // In-memory fault injection deliberately leaves on-disk input receipts
        // unchanged; production loads and checks those files before this API.
        auto wrong = trace; wrong.post[34][wrong.post[34].size() - 1 - wrong.positions.at(7)] = '1';
        rejects([&] { unwrap(replay_program("case", graph, analysis, wrong, warm, root / "program", root / "wrong_output", options)); }, "known-bit replay mismatch");
        auto no_withheld = trace;
        for (unsigned t = 16; t < 48; ++t) no_withheld.pre[t][no_withheld.pre[t].size() - 1 - no_withheld.positions.at(5)] = '1';
        rejects([&] { unwrap(replay_program("case", graph, analysis, no_withheld, warm, root / "program", root / "no_withheld", options)); }, "insufficient applicable");
        const auto insufficient = Json::parse(artifacts::read(root / "no_withheld" / "replay.json"));
        require(insufficient.at("status") == "SAVED_PROGRAM_ORIGINAL_GATE_REPLAY_INSUFFICIENT_COVERAGE" &&
            insufficient.at("counts").at("training_frames") == 24 && insufficient.at("counts").at("withheld_frames") == 0 &&
            insufficient.at("counts").at("withheld_outside_contract") == 16,
            "insufficient coverage lost its actual replay counts or reported success");
        artifacts::write(query, proof.to_smt2() + "; changed\n");
        rejects([&] { unwrap(replay_program("case", graph, analysis, trace, warm, root / "program", root / "changed_proof", options)); }, "guided proof identity changed");
        std::cout << "PASS compiled replay preserves native profile boundaries, external training-only extraction has heldout replay, other training windows excluded, coverage failure counts retained; output/proof mutants and process failures rejected\n";
    }
    catch (const std::exception& error) { std::cerr << error.what() << '\n'; return 1; }
}
