// Native classification vs the actual Yosys Verilog FF primitives, not a
// second copy of the same transition function. CMake runs Icarus and compares.
#include "algorithm_recovery/observations.h"
#include "../src/artifacts.h"
#include <bitset>
#include <iostream>
#include <random>

using namespace hal::algorithm_recovery;
using Json = nlohmann::json;
namespace fs = std::filesystem;
using artifacts::require;

template<class F> static void rejects(F f)
{
    bool failed = false; try { f(); } catch (const std::exception&) { failed = true; }
    require(failed, "invalid input was accepted");
}

int main(int argc, char** argv)
{
    if (argc != 2) return 2;
    try
    {
        const fs::path root = fs::absolute(argv[1]); fs::create_directories(root);
        Json module = {{"ports", Json::object()}, {"cells", Json::object()}};
        for (int i = 0; i < 4; ++i) module["ports"]["p" + std::to_string(i)] = {{"direction", "input"}, {"bits", {i + 2}}};
        std::ostringstream tb; tb << "module test; reg C=0,D=0,E=0,R=0; integer fd;\n";
        unsigned q = 10;
        for (const auto& falling : {"", "N"}) for (const auto& enable : {"", "E"}) for (const auto& reset : {"", "R", "S", "SR", "SS"})
        {
            const auto type = std::string("SB_DFF") + falling + enable + reset, name = "ff" + std::to_string(q);
            Json pins = {{"C", {2}}, {"D", {3}}, {"Q", {q}}};
            if (*enable) pins["E"] = {4};
            if (*reset) pins[std::string(reset).back() == 'R' ? "R" : "S"] = {5};
            module["cells"][name] = {{"type", type}, {"connections", pins}};
            tb << "wire q" << q << "; " << type << ' ' << name << "(.C(C),.D(D),.Q(q" << q << ')';
            if (*enable) tb << ",.E(E)";
            if (*reset) tb << (std::string(reset).back() == 'R' ? ",.R(R)" : ",.S(R)");
            tb << ");\n"; ++q;
        }
        const fs::path source = root / "original.json";
        auto save = [&] { artifacts::write(source, Json{{"modules", {{"test", module}}}}.dump()); };
        save(); z3::context ctx; OriginalCircuit original(source, ctx);
        require(original.flip_flops.size() == 20, "missing FF variants");
        Observations tr; tr.positions = {{2, 0}, {3, 1}, {4, 2}, {5, 3}};
        std::vector<int> states(20, 0); std::array<std::vector<int>, 3> mutants{states, states, states};
        std::ostringstream expected; std::array<std::ostringstream, 3> wrong;
        tb << "initial begin fd=$fopen(\"observed.txt\",\"w\"); #2;\n";
        std::mt19937 random(7319); unsigned clock = 0; std::set<unsigned> transitions;
        for (unsigned t = 0; t < 512; ++t)
        {
            const unsigned bits = random() & 15, next_clock = bits & 1;
            transitions.insert((clock << 4) | bits);
            tr.pre.push_back(std::bitset<4>((bits & 14) | clock).to_string()); tr.post.push_back(std::bitset<4>(bits).to_string());
            tb << "D=" << ((bits >> 1) & 1) << "; E=" << ((bits >> 2) & 1) << "; R=" << ((bits >> 3) & 1) << "; #2; C=" << next_clock << "; #2;\n";
            tb << "$fwrite(fd,\"";
            for (unsigned i = 0; i < states.size(); ++i) tb << "%b";
            tb << "\\n\"";
            unsigned index = 0;
            for (const auto& [bit, ff] : original.flip_flops)
            {
                const auto apply = [&](const OriginalFF& definition, int& state) {
                    const auto step = ff_update(definition, tr, t); require(step.write != Write::mixed_or_unknown, "unexpected unknown FF step");
                    if (step.write == Write::full) state = step.reset_value >= 0 ? step.reset_value : (bits >> 1) & 1;
                };
                apply(ff, states[index]); expected << states[index]; tb << ",q" << bit;
                for (unsigned mutant = 0; mutant < 3; ++mutant)
                {
                    auto incorrect = ff;
                    if (mutant == 0) incorrect.synchronous_reset = false;
                    if (mutant == 1) incorrect.falling = !incorrect.falling;
                    if (mutant == 2) incorrect.pins.erase("E");
                    apply(incorrect, mutants[mutant][index]); wrong[mutant] << mutants[mutant][index];
                }
                ++index;
            }
            expected << '\n'; for (auto& out : wrong) out << '\n'; tb << ");\n"; clock = next_clock;
        }
        require(transitions.size() == 32, "missing clock/control transition classes");
        tb << "$fclose(fd);$finish;end endmodule\n";
        artifacts::write(root / "test.v", tb.str()); artifacts::write(root / "expected.txt", expected.str());
        for (unsigned i = 0; i < wrong.size(); ++i) artifacts::write(root / ("mutant" + std::to_string(i) + ".txt"), wrong[i].str());

        // Four-state observations remain unknown; a provable hold stays a hold.
        OriginalFF plain; plain.pins = {{"C", 2}, {"D", 3}, {"E", 4}};
        tr.pre = {"00x0"}; tr.post = {"00x1"};
        require(ff_update(plain, tr, 0).write == Write::hold, "disabled FF should hold with unknown data");
        tr.pre = {"0x00"}; tr.post = {"0x01"};
        require(ff_update(plain, tr, 0).write == Write::mixed_or_unknown, "unknown enable must not become known");
        tr.pre = {"010x"}; tr.post = {"0101"};
        require(ff_update(plain, tr, 0).write == Write::mixed_or_unknown, "unknown clock must not become an edge");

        // Independent LUT interpretation: an asymmetric table exercises input
        // ordering; exhaustive assignments check the encoded truth table.
        module["cells"] = {{"lut", {{"type", "SB_LUT4"}, {"parameters", {{"LUT_INIT", "1001001011100011"}}},
            {"connections", {{"I0", {2}}, {"I1", {3}}, {"I2", {4}}, {"I3", {5}}, {"O", {20}}}}}}};
        save(); OriginalCircuit lut(source, ctx); auto equation = lut.function(20);
        for (unsigned assignment = 0; assignment < 16; ++assignment)
        {
            z3::expr_vector from(ctx), to(ctx);
            for (unsigned i = 0; i < 4; ++i) { from.push_back(ctx.bv_const(("b" + std::to_string(i + 2)).c_str(), 1)); to.push_back(ctx.bv_val((assignment >> i) & 1, 1)); }
            auto copy = equation; require(copy.substitute(from, to).simplify().is_true() == bool((0x92e3 >> assignment) & 1), "original LUT bit order");
        }
        for (const auto& invalid : {"1x", "1z", "10000000000000000", ""})
        {
            module["cells"]["lut"]["parameters"]["LUT_INIT"] = invalid; save();
            rejects([&] { OriginalCircuit invalid_lut(source, ctx); invalid_lut.function(20); });
        }
        module["cells"]["lut"]["parameters"]["LUT_INIT"] = "1010101010101010";
        module["cells"]["lut"]["connections"]["I0"] = {20}; save();
        rejects([&] { OriginalCircuit cycle(source, ctx); cycle.function(20); });
        module["cells"]["lut"]["connections"]["I0"] = {99}; save();
        rejects([&] { OriginalCircuit endpoint(source, ctx); endpoint.function(20); });

        // Loader rejects tampering even if the row remains syntactically valid.
        const auto data = root / "native_trace.txt"; artifacts::write(data, "01 xz\n");
        Json receipt = {{"files", {{data.string(), artifacts::file_hash(data)}}}, {"observed", {2, 3}}, {"frames", 1}};
        artifacts::write(root / "native_trace.json", receipt.dump());
        auto loaded = Observations::load(root); require(loaded.value(2, 0) == 1 && loaded.value(2, 0, true) == -1, "trace bit order/X/Z");
        artifacts::write(data, "00 xz\n"); rejects([&] { Observations::load(root); });
        receipt["files"][data.string()] = artifacts::file_hash(data); receipt["frames"] = 2;
        artifacts::write(root / "native_trace.json", receipt.dump()); rejects([&] { Observations::load(root); });
        std::cout << "PASS native original-cone/receipt checks; generated 20 FF variants x 512 events\n";
    }
    catch (const std::exception& error) { std::cerr << error.what() << '\n'; return 1; }
}
