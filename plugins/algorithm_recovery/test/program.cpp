#include "algorithm_recovery/program.h"
#include "algorithm_recovery/evaluation.h"
#include "../src/artifacts.h"
#include <fstream>
#include <iostream>
#include <random>

using namespace hal::algorithm_recovery;
using Json = nlohmann::json;
namespace fs = std::filesystem;
static void check(bool yes, const std::string& why) { if (!yes) throw std::runtime_error(why); }
template<class T> static T unwrap(hal::Result<T>&& result)
{ if (result.is_error()) throw std::runtime_error(result.get_error().get()); return std::move(result.get()); }
static std::string numeral(const z3::expr& value) { return Z3_get_numeral_string(value.ctx(), value); }
static Json memories(const std::map<std::string, RamState>& states)
{ Json out = Json::object(); for (const auto& [name, state] : states) out[name] = {{"data", state.memory}, {"read", state.read}}; return out; }
static Json request(const History& history, const FrameValues& inputs, const std::map<std::string, RamState>& states)
{
    Json h = Json::array(), in = Json::array();
    for (const auto& [src, value] : history) h.push_back({src.name, src.phase, src.delay, numeral(value)});
    for (const auto& [key, value] : inputs) in.push_back({key.first, key.second, numeral(value)});
    return {{"command", "run"}, {"history", h}, {"inputs", in}, {"memories", memories(states)}};
}
static Json expected(const FrameResult& frame)
{
    const auto base = request(frame.history, frame.values, frame.memories); Json reads = Json::array();
    for (const auto& phase : frame.reads)
    { Json row = Json::object(); for (const auto& [name, pair] : phase) row[name] = {pair.first, pair.second}; reads.push_back(row); }
    return {{"values", base.at("inputs")}, {"history", base.at("history")}, {"memories", base.at("memories")}, {"reads", reads}};
}
int main(int argc, char** argv)
{
    try
    {
        if (argc == 4 && std::string(argv[1]) == "verify")
        {
            std::ifstream expected(argv[2]), actual(argv[3]); check(bool(expected) && bool(actual), "missing executable output");
            std::string a, b; size_t count = 0;
            while (std::getline(expected, a))
            { check(bool(std::getline(actual, b)), "missing output row"); check(Json::parse(a) == Json::parse(b), "compiled program mismatch at row " + std::to_string(count)); ++count; }
            check(!std::getline(actual, b), "extra output row"); std::cout << "PASS " << count << " compiled program responses\n"; return 0;
        }
        if (argc != 3 || std::string(argv[1]) != "generate") return 2;
        const fs::path out(argv[2]); fs::remove_all(out); fs::create_directories(out); z3::context ctx;
        const std::vector<std::string> texts{
            "(bvadd x y)", "(bvsub x y)", "(bvmul x y)", "(bvneg x)", "(bvnot x)",
            "(bvand x y)", "(bvor x y)", "(bvxor x y)", "(bvnand x y)", "(bvnor x y)", "(bvxnor x y)",
            "(bvshl x y)", "(bvlshr x y)", "(bvashr x y)", "(concat x y)", "((_ extract 2 1) x)",
            "((_ zero_extend 3) x)", "((_ sign_extend 3) x)", "((_ repeat 3) x)",
            "((_ rotate_left 7) x)", "((_ rotate_right 7) x)", "(ext_rotate_left x y)", "(ext_rotate_right x y)",
            "(bvudiv x y)", "(bvurem x y)", "(bvsdiv x y)", "(bvsrem x y)", "(bvsmod x y)",
            "(bvult x y)", "(bvule x y)", "(bvugt x y)", "(bvuge x y)",
            "(bvslt x y)", "(bvsle x y)", "(bvsgt x y)", "(bvsge x y)",
            "(ite (= x #x0) y x)", "(bvcomp x y)", "(distinct x y #x0)", "(= x y)",
            "(and (= x #x0) (= y #x0))", "(or (= x #x0) (= y #x0))", "(xor (= x #x0) (= y #x0))",
            "(not (= x #x0))", "(=> (= x #x0) (= y #x0))", "true", "false", "#x9"};
        FoldGraph operations; operations.available = true; operations.period = 1;
        for (const auto& text : texts)
        {
            auto term = ctx.parse_string(("(declare-const x (_ BitVec 4))(declare-const y (_ BitVec 4))(assert (= " + text + " " + text + "))").c_str())[0].arg(0);
            if (term.is_bool()) term = z3::ite(term, ctx.bv_val(1, 1), ctx.bv_val(0, 1));
            std::map<std::string, Source> sources;
            const BitVectorProgram program(term);
            for (const auto& [name, width] : program.inputs()) sources.emplace(name, Source{SourceKind::input, name, 0, 0});
            operations.nodes.push_back({"operation_" + std::to_string(operations.nodes.size()), 0, term, sources, true});
        }
        const auto x80 = ctx.bv_const("x80", 80);
        operations.nodes.push_back({"wide", 0, z3::concat(x80, x80) + z3::sext(x80, 80), {{"x80", {SourceKind::input, "wide", 0, 0}}}, true});
        const auto operation_report = unwrap(export_program(operations, out / "operations"));
        const auto operation_model = unwrap(check_program_artifacts(out / "operations"));
        std::ofstream op_requests(out / "operations" / "requests.jsonl"), op_expected(out / "operations" / "expected.jsonl");
        for (unsigned i = 0; i < operations.nodes.size(); ++i)
        {
            const auto& term = operations.nodes[i].equation; const BitVectorProgram program(term);
            for (unsigned x = 0; x < 16; ++x) for (unsigned y = 0; y < 16; ++y)
            {
                Json values = Json::array(); z3::expr_vector from(ctx), to(ctx);
                for (const auto& [name, width] : program.inputs())
                {
                    const Integer value = width == 80 ? (Integer(1) << 79) + x * 16 + y : Integer(name == "x" ? x : y);
                    values.push_back(value.str()); from.push_back(ctx.bv_const(name.c_str(), width)); to.push_back(ctx.bv_val(value.str().c_str(), width));
                }
                auto reference = term; reference = reference.substitute(from, to).simplify();
                op_requests << Json{{"command", "kernel"}, {"kernel", operation_model.at("nodes").at(i).at("kernel")}, {"inputs", values}}.dump() << '\n';
                op_expected << Json{{"value", numeral(reference)}}.dump() << '\n';
            }
        }
        // The restricted parser must notice a changed result and reject an
        // injected statement, independently of the native emitter's Step data.
        auto x = ctx.bv_const("x", 8); const auto original = x + 1;
        auto body = BitVectorProgram(original).cpp_function("mutant");
        const auto pos = body.find("return values.at("); check(pos != std::string::npos, "test function has no return");
        const auto end = body.find(')', pos); body.replace(pos, end - pos, "return values.at(0");
        const auto bad = decode_cpp_function(body, {x}, ctx); z3::solver refute(ctx); refute.add(bad != original);
        check(refute.check() == z3::sat, "wrong emitted return was not refuted");
        body.insert(body.find("    return"), "    values.clear();\n"); bool rejected = false;
        try { decode_cpp_function(body, {x}, ctx); } catch (const std::exception&) { rejected = true; }
        check(rejected, "unsupported C++ statement accepted");
        const auto runtime_file = out / "operations" / "recovery_runtime.h"; const auto runtime = artifacts::read(runtime_file);
        artifacts::write(runtime_file, runtime + "// corrupt\n"); check(check_program_artifacts(out / "operations").is_error(), "changed runtime identity accepted");
        artifacts::write(runtime_file, runtime);

        FoldGraph graph; graph.available = true; graph.period = 4; graph.origin = 12;
        const auto old = ctx.bv_const("old", 80), older = ctx.bv_const("older", 80), delta = ctx.bv_const("delta", 16);
        graph.nodes.push_back({"acc", 0, old + 3 * older + z3::zext(delta, 64),
            {{"old", {SourceKind::signal, "acc", 2, 1}}, {"older", {SourceKind::signal, "acc", 2, 2}}, {"delta", {SourceKind::input, "delta", 0, 0}}}, true});
        graph.nodes.push_back({"acc", 2, z3::shl(old, 7) ^ z3::zext(delta, 64),
            {{"old", {SourceKind::signal, "acc", 0, 0}}, {"delta", {SourceKind::input, "delta", 2, 0}}}, true});
        graph.histories.insert({SourceKind::signal, "acc", 2, 1}); graph.histories.insert({SourceKind::signal, "acc", 2, 2});
        const auto ra = ctx.bv_const("ra", 16), rb = ctx.bv_const("rb", 16);
        graph.nodes.push_back({"read_sum", 1, ra + rb,
            {{"ra", {SourceKind::ram, "a", 1, 0}}, {"rb", {SourceKind::ram, "b", 1, 0}}}, true});
        std::mt19937 rng(20261101);
        const std::map<std::string, unsigned> widths{{"RADDR", 11}, {"WADDR", 11}, {"WDATA", 16}, {"MASK", 16}, {"RE", 1}, {"RCLKE", 1}, {"WE", 1}, {"WCLKE", 1}};
        for (const std::string name : {"a", "b"})
        {
            Ice40Ram memory; memory.name = name; memory.read_clock = name == "a" ? 11 : 22; memory.write_clock = name == "a" ? 22 : 11;
            for (auto& value : memory.initial) value = rng(); graph.memories.emplace(name, memory);
            for (unsigned phase = 0; phase < 4; ++phase) for (const auto& [port, bits] : widths)
            {
                auto value = ctx.bv_const(port.c_str(), bits);
                std::map<std::string, Source> sources{{port, {SourceKind::input, port, phase, 0}}};
                if (port == "WDATA")
                {
                    const auto read = ctx.bv_const("other", 16); value = name == "a" ? value ^ read : value + read;
                    sources.emplace("other", Source{SourceKind::ram, name == "a" ? "b" : "a", phase, 0});
                }
                graph.ram_nodes.push_back({name, port, phase, value, sources});
            }
        }
        for (unsigned phase = 0; phase < 4; ++phase)
            graph.nodes.push_back({"sample", phase, delta, {{"delta", {SourceKind::input, "delta", phase, 0}}}, true});
        const auto frame_report = unwrap(export_program(graph, out / "frames")); unwrap(check_program_artifacts(out / "frames"));
        check(frame_report.at("structured_schedule").at("ff_update_rules") == 4 &&
              frame_report.at("structured_schedule").at("ram_update_rules") == 16, "repeated updates were not consolidated");
        std::ofstream frame_requests(out / "frames" / "requests.jsonl"), frame_expected(out / "frames" / "expected.jsonl");
        auto states = initial_memories(graph); History history;
        for (const auto& src : graph.histories) history.emplace(src, ctx.bv_val("1512366075204170929041", 80));
        frame_requests << Json{{"command", "initial"}}.dump() << '\n'; frame_expected << Json{{"memories", memories(states)}}.dump() << '\n';
        for (unsigned frame = 0; frame < 256; ++frame)
        {
            FrameValues inputs;
            for (unsigned phase = 0; phase < 4; ++phase)
            {
                inputs.emplace(std::make_pair("delta", phase), ctx.bv_val(rng() & 65535, 16));
                for (const auto& [port, width] : widths)
                {
                    unsigned value = rng() & ((1u << width) - 1);
                    if (port == "RADDR" || port == "WADDR") value = (frame % 4 == 0 ? 17 : value) | 1024;
                    inputs.emplace(std::make_pair(port, phase), ctx.bv_val(value, width));
                }
                inputs.emplace(std::make_pair("event_rise_11", phase), ctx.bv_val(rng() & 1, 1));
                inputs.emplace(std::make_pair("event_rise_22", phase), ctx.bv_val(rng() & 1, 1));
            }
            frame_requests << request(history, inputs, states).dump() << '\n';
            const auto result = unwrap(run_frame(graph, history, inputs, states, ctx));
            frame_expected << expected(result).dump() << '\n'; history = result.history; states = result.memories;
        }
        FoldGraph order; order.available = true; order.period = 2;
        for (unsigned phase = 0; phase < 2; ++phase)
            for (const auto& name : phase ? std::vector<std::string>{"order_b", "order_a"} : std::vector<std::string>{"order_a", "order_b"})
                order.nodes.push_back({name, phase, delta, {{"delta", {SourceKind::input, "delta", phase, 0}}}, true});
        unwrap(export_program(order, out / "order"));
        check(Json::parse(artifacts::read(out / "order" / "schedule.json")).at("words").at("mode") == "EXACT_PHASE_DISPATCH",
              "conflicting phase orders were silently reordered");
        FrameValues order_inputs;
        order_inputs.emplace(std::make_pair(std::string("delta"), 0u), ctx.bv_val(0, 16));
        order_inputs.emplace(std::make_pair(std::string("delta"), 1u), ctx.bv_val(65535, 16));
        artifacts::write(out / "order" / "requests.jsonl", request({}, order_inputs, {}).dump() + '\n');
        artifacts::write(out / "order" / "expected.jsonl", expected(unwrap(run_frame(order, {}, order_inputs, {}, ctx))).dump() + '\n');
        FoldGraph absent; check(unwrap(export_program(absent, out / "absent")).at("status") == "NO_GRAPH" && !fs::exists(out / "absent" / "recovered.cpp"), "missing graph fabricated");
        auto malformed = graph; malformed.ram_nodes.pop_back(); check(export_program(malformed, out / "malformed").is_error(), "missing RAM port exported");
        check(export_program(graph, out / "frames").is_error(), "existing program overwritten");
        std::cout << "PASS generated and proved " << operations.nodes.size() * 256 << " operation cases and 256 coupled four-event frames\n";
    }
    catch (const std::exception& error) { std::cerr << error.what() << '\n'; return 1; }
}
