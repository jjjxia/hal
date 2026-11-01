// Real saved C++ kernels and a full coupled frame, independently evaluated in
// Z3. This is an export regression, not original-gate observation replay.
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
int main(int argc, char** argv)
{
    if (argc != 3) { std::cerr << "Usage: check-recovery-program-fixture program_directory empty_check_directory\n"; return 2; }
    try
    {
        const fs::path root(argv[1]), output(argv[2]);
        check(!fs::exists(output) || fs::is_empty(output), "check directory must be empty"); fs::create_directories(output);
        const auto model = unwrap(check_program_artifacts(root));
        const auto report = Json::parse(artifacts::read(root / "program.json")); const auto cpp = artifacts::read(root / "recovered.cpp");
        z3::context ctx; std::mt19937_64 rng(20261101); std::vector<z3::expr> functions;
        std::ofstream requests(output / "requests.jsonl"), expected(output / "expected.jsonl"); size_t cases = 0;
        const auto random = [&](unsigned width) {
            Integer value = 0; for (unsigned bit = 0; bit < width; bit += 64) value |= Integer(rng()) << bit;
            return wrap(value, width);
        };
        for (unsigned i = 0; i < model.at("kernels").size(); ++i)
        {
            const auto& row = report.at("kernels").at(i); std::vector<z3::expr> parameters;
            const auto widths = model.at("kernels").at(i).at("inputs").get<std::vector<unsigned>>();
            for (unsigned j = 0; j < widths.size(); ++j)
            {
                const auto name = "argument_" + std::to_string(j);
                parameters.push_back(widths[j] ? ctx.bv_const(name.c_str(), widths[j]) : ctx.bool_const(name.c_str()));
            }
            functions.push_back(decode_cpp_function(cpp.substr(row.at("offset").get<size_t>(), row.at("length").get<size_t>()), parameters, ctx));
            for (unsigned sample = 0; sample < 8; ++sample)
            {
                z3::expr_vector from(ctx), to(ctx); Json inputs = Json::array();
                for (unsigned j = 0; j < widths.size(); ++j)
                {
                    const unsigned width = widths[j] ? widths[j] : 1; auto value = random(width);
                    if (sample == 0) value = 0;
                    if (sample == 1) value = (Integer(1) << width) - 1;
                    if (sample == 2) value = Integer(1) << (width - 1);
                    if (sample == 3) value = (j & 1) ? 1 : 0;
                    from.push_back(parameters[j]); to.push_back(widths[j] ? ctx.bv_val(value.str().c_str(), width) : ctx.bool_val(value != 0)); inputs.push_back(value.str());
                }
                auto expression = functions.back(); expression = expression.substitute(from, to).simplify();
                requests << Json{{"command", "kernel"}, {"kernel", i}, {"inputs", inputs}}.dump() << '\n';
                expected << Json{{"value", expression.is_bool() ? (expression.is_true() ? "1" : "0") : numeral(expression)}}.dump() << '\n'; ++cases;
            }
        }
        FoldGraph graph; graph.available = true; graph.period = model.at("period"); graph.origin = model.at("origin");
        std::map<std::pair<std::string, unsigned>, unsigned> word_widths;
        const auto sources = [](const Json& row) {
            std::map<std::string, Source> result;
            for (unsigned i = 0; i < row.size(); ++i)
            {
                const auto& s = row.at(i); const std::string kind = s.at("kind");
                check(kind == "signal" || kind == "input" || kind == "ram", "invalid saved binding");
                result.emplace("argument_" + std::to_string(i), Source{kind == "signal" ? SourceKind::signal : kind == "input" ? SourceKind::input : SourceKind::ram,
                    s.at("name"), s.at("phase"), s.at("delay")});
            }
            return result;
        };
        for (const auto& row : model.at("nodes"))
        {
            graph.nodes.push_back({row.at("name"), row.at("phase"), functions.at(row.at("kernel")), sources(row.at("sources")), row.at("numeric_order_known")});
            word_widths.emplace(std::make_pair(row.at("name").get<std::string>(), row.at("phase").get<unsigned>()), row.at("width"));
        }
        for (const auto& row : model.at("ram_nodes"))
            graph.ram_nodes.push_back({row.at("name"), row.at("port"), row.at("phase"), functions.at(row.at("kernel")), sources(row.at("sources"))});
        for (auto it = model.at("memories").begin(); it != model.at("memories").end(); ++it)
        {
            Ice40Ram memory; memory.name = it.key(); memory.initial = it.value().at("initial").get<decltype(memory.initial)>();
            memory.read_clock = it.value().at("read_clock"); memory.write_clock = it.value().at("write_clock"); graph.memories.emplace(it.key(), memory);
        }
        History history; FrameValues inputs; Json input_json = Json::array(), history_json = Json::array();
        for (const auto& row : model.at("histories"))
        {
            const Source source{SourceKind::signal, row.at(0), row.at(1), row.at(2)}; graph.histories.insert(source);
            const auto width = word_widths.at({source.name, source.phase}); const auto value = random(width);
            history.emplace(source, ctx.bv_val(value.str().c_str(), width)); history_json.push_back({source.name, source.phase, source.delay, value.str()});
        }
        for (const auto& row : model.at("inputs"))
        {
            const std::string name = row.at(0); const unsigned phase = row.at(1), width = row.at(2); const auto value = random(width);
            inputs.emplace(std::make_pair(name, phase), ctx.bv_val(value.str().c_str(), width)); input_json.push_back({name, phase, value.str()});
        }
        const auto initial = initial_memories(graph);
        requests << Json{{"command", "initial"}}.dump() << '\n'; expected << Json{{"memories", memories(initial)}}.dump() << '\n'; ++cases;
        requests << Json{{"command", "run"}, {"history", history_json}, {"inputs", input_json}, {"memories", memories(initial)}}.dump() << '\n';
        const auto frame = unwrap(run_frame(graph, history, inputs, initial, ctx));
        Json values = Json::array(), next = Json::array(), reads = Json::array();
        for (const auto& [key, value] : frame.values) values.push_back({key.first, key.second, numeral(value)});
        for (const auto& [s, value] : frame.history) next.push_back({s.name, s.phase, s.delay, numeral(value)});
        for (const auto& phase : frame.reads)
        { Json row = Json::object(); for (const auto& [name, pair] : phase) row[name] = {pair.first, pair.second}; reads.push_back(row); }
        expected << Json{{"values", values}, {"history", next}, {"memories", memories(frame.memories)}, {"reads", reads}}.dump() << '\n'; ++cases;
        check(bool(requests) && bool(expected), "test case write failure");
        artifacts::write(output / "prepared.json", Json{{"kernels", functions.size()}, {"cases", cases}, {"nodes", graph.nodes.size()},
            {"ram_nodes", graph.ram_nodes.size()}, {"period", graph.period}, {"program_sha256", artifacts::file_hash(root / "recovered.cpp")},
            {"scope", "Boundary/random kernel probes plus one full arbitrary-state frame against the Z3 graph executor; not original-gate trace replay."}}.dump(2) + '\n');
        std::cout << "PREPARED " << cases << " responses for " << functions.size() << " real-case kernels and a complete " << graph.period << "-event coupled frame\n";
    }
    catch (const std::exception& error) { std::cerr << error.what() << '\n'; return 1; }
}
