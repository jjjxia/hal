#include "algorithm_recovery/frontend.h"
#include "algorithm_recovery/grouping.h"
#include "algorithm_recovery/state_machine.h"
#include "hal_core/netlist/gate.h"
#include "hal_core/netlist/gate_library/gate_type_component/ff_component.h"
#include "../src/artifacts.h"
#include "../src/process.h"
#include <bitset>
#include <iostream>
#include <random>
using namespace hal::algorithm_recovery;
using Json = nlohmann::json;
namespace fs = std::filesystem;
using artifacts::require;
template<class T> T unwrap(hal::Result<T>&& value)
{ if (value.is_error()) throw std::runtime_error(value.get_error().get()); return std::move(value.get()); }
int main(int argc, char** argv)
{
    if (argc != 7) return 2;
    try
    {
        const auto root = fs::absolute(argv[1]); require(!fs::exists(root) || fs::is_empty(root), "state test directory must be empty"); fs::create_directories(root);
        Json module{{"ports", Json::object()}, {"cells", Json::object()}};
        for (unsigned i = 2; i <= 9; ++i) module["ports"]["n" + std::to_string(i)] = {{"direction", i == 9 ? "inout" : "input"}, {"bits", {i}}};
        const auto ff = [&](const std::string& name, const std::string& type, unsigned q, Json pins) {
            pins["Q"] = {q}; module["cells"][name] = {{"type", type}, {"connections", pins}};
            module["ports"]["n" + std::to_string(q)] = {{"direction", "output"}, {"bits", {q}}};
        };
        unsigned q = 10;
        for (const auto& falling : {"", "N"}) for (const auto& enable : {"", "E"}) for (const auto& reset : {"", "R", "S", "SR", "SS"})
        {
            Json pins{{"C", {2}}, {"D", {3}}}; if (*enable) pins["E"] = {4}; if (*reset) pins[std::string(reset).back() == 'R' ? "R" : "S"] = {5};
            ff("ff" + std::to_string(q), std::string("SB_DFF") + falling + enable + reset, q, pins); ++q;
        }
        const auto lut = [&](const std::string& name, unsigned output, Json a, Json b, unsigned table) {
            module["cells"][name] = {{"type", "SB_LUT4"}, {"parameters", {{"LUT_INIT", std::bitset<16>(table).to_string()}}},
                {"connections", {{"I0", {a}}, {"I1", {b}}, {"I2", {"0"}}, {"I3", {"0"}}, {"O", {output}}}}};
        };
        for (unsigned i = 0; i < 8; ++i)
        {
            const Json carry = i ? Json(59 + i) : Json("1"); lut("sum" + std::to_string(i), 50 + i, 40 + i, carry, 0x6666);
            module["cells"]["carry" + std::to_string(i)] = {{"type", "SB_CARRY"}, {"connections", {{"I0", {40 + i}}, {"I1", {"0"}}, {"CI", {carry}}, {"CO", {60 + i}}}}};
            ff("count" + std::to_string(i), "SB_DFFER", 40 + i, {{"C", {2}}, {"E", {4}}, {"R", {5}}, {"D", {50 + i}}});
        }
        ff("derived", "SB_DFF", 70, {{"C", {40}}, {"D", {3}}});
        ff("async_chain", "SB_DFFR", 71, {{"C", {2}}, {"R", {40}}, {"D", {3}}});
        ff("io", "$dff", 72, {{"CLK", {2}}, {"D", {100}}}); module["cells"]["io"]["parameters"] = {{"WIDTH", "1"}, {"CLK_POLARITY", "1"}};
        ff("feedback", "SB_DFF", 73, {{"C", {2}}, {"D", {9}}});
        lut("guarded_data", 200, 3, 4, 0x8888); ff("guarded", "SB_DFF", 74, {{"C", {2}}, {"D", {200}}});
        module["cells"]["pad_driver"] = {{"type", "$mux"}, {"parameters", {{"WIDTH", "1"}}}, {"connections", {{"A", {"z"}}, {"B", {72}}, {"S", {4}}, {"Y", {9}}}}};
        Json rp{{"RCLK", {2}}, {"WCLK", {6}}, {"RCLKE", {"1"}}, {"WCLKE", {"1"}}, {"RE", {8}}, {"WE", {7}}, {"RADDR", Json::array()}, {"WADDR", Json::array()}, {"WDATA", Json::array()}, {"MASK", Json::array()}, {"RDATA", Json::array()}};
        for (unsigned i = 0; i < 11; ++i) { rp["RADDR"].push_back(i < 8 ? Json(40 + i) : Json("0")); rp["WADDR"].push_back(i < 8 ? Json(40 + i) : Json("0")); }
        for (unsigned i = 0; i < 16; ++i) { rp["RDATA"].push_back(100 + i); rp["WDATA"].push_back(i < 8 ? Json(40 + i) : Json(3)); rp["MASK"].push_back(i % 3 == 0 ? Json(5) : Json("0")); }
        Json params{{"READ_MODE", "00"}, {"WRITE_MODE", "00"}}; for (char i : std::string("0123456789ABCDEF")) params["INIT_" + std::string(1, i)] = std::string(256, '0');
        module["cells"]["ram"] = {{"type", "SB_RAM40_4K"}, {"parameters", params}, {"connections", rp}};
        for (auto& cell : module["cells"]) if (!cell.contains("parameters")) cell["parameters"] = Json::object();
        const auto source = root / "original.json"; artifacts::write(source, Json{{"modules", {{"test", module}}}}.dump());
        std::set<unsigned> bits; for (const auto& cell : module["cells"]) for (const auto& pin : cell["connections"]) for (const auto& bit : pin) if (bit.is_number_integer()) bits.insert(bit.get<unsigned>());
        std::ostringstream tb; tb << "module test; integer fd,i;\n";
        for (unsigned bit : bits) { tb << (bit >= 2 && bit <= 8 ? "reg " : bit == 72 ? "reg " : "wire ") << 'n' << bit; if (bit >= 2 && bit <= 8) tb << "=0"; tb << ";\n"; }
        tb << "reg ext9=0; assign n9=(n4===1'b0)?ext9:1'bz;\n";
        const auto expr = [](const Json& bs) { std::string s = "{"; for (auto i = bs.rbegin(); i != bs.rend(); ++i) { if (i != bs.rbegin()) s += ','; s += i->is_string() ? "1'b" + i->get<std::string>() : "n" + i->dump(); } return s + '}'; };
        for (auto cell = module["cells"].begin(); cell != module["cells"].end(); ++cell)
        {
            const std::string type = cell.value()["type"]; const auto& pins = cell.value()["connections"];
            if (type == "$dff") { tb << "always @(posedge n2) n72 <= n100;\n"; continue; }
            if (type == "$mux") { tb << "assign n9=n4?n72:1'bz;\n"; continue; }
            tb << type;
            if (!cell.value().at("parameters").empty()) { tb << " #("; bool first = true; for (auto p = cell.value()["parameters"].begin(); p != cell.value()["parameters"].end(); ++p) { if (!first) tb << ','; first = false; const auto v = p.value().get<std::string>(); tb << '.' << p.key() << '(' << v.size() << "'b" << v << ')'; } tb << ')'; }
            tb << ' ' << cell.key() << '('; bool first = true; for (auto p = pins.begin(); p != pins.end(); ++p) { if (!first) tb << ','; first = false; tb << '.' << p.key() << '(' << expr(p.value()) << ')'; } tb << ");\n";
        }
        tb << "wire [" << bits.size()-1 << ":0] observed={"; for (auto it = bits.rbegin(); it != bits.rend(); ++it) { if (it != bits.rbegin()) tb << ','; tb << 'n' << *it; } tb << "};\ninitial begin fd=$fopen(" << Json((root/"native_trace.txt").string()).dump() << ",\"w\");\n";
        Json metadata{{"status", "ORIGINAL_GATE_TRACE"}, {"frames", 384}, {"initial_discard", 128}, {"clocks", {2,6}}, {"observed", bits}, {"files", Json::object()}, {"windows", {{"training", Json::array()}}}};
        std::ostringstream stimulus; std::mt19937 random(12920);
        for (unsigned t = 0; t < 384; ++t)
        {
            unsigned v = random() & 63; v = (v & ~4u) | ((t < 8 || random() % 16 == 0) ? 4u : 0u); if (t >= 128 && t < 256) v |= 2;
            const unsigned c = (t + 1) % 2, w = ((t + 1) / 3) % 2; stimulus << std::hex << (v | (c << 6) | (w << 7)) << '\n';
            for (unsigned i = 0; i < 6; ++i) tb << (i == 5 ? "ext9" : "n" + std::to_string(std::vector<unsigned>{3,4,5,7,8,9}[i])) << '=' << ((v >> i)&1) << ';';
            tb << "#2;$fwrite(fd,\"%b \",observed);n2=" << c << ";n6=" << w << ";#2;$fwrite(fd,\"%b\\n\",observed);\n";
            if (t >= 128 && t < 256) metadata["windows"]["training"].push_back(t);
        }
        tb << "$fclose(fd);fd=$fopen(" << Json((root/"native_ram_final.txt").string()).dump() << ",\"w\");for(i=0;i<256;i=i+1)$fwrite(fd,\"ram %0d %b\\n\",i,ram.memory[i]);$fclose(fd);$finish;end endmodule\n";
        artifacts::write(root/"native_stimulus.hex", stimulus.str()); artifacts::write(root/"test.v", tb.str());
        process::run({argv[3], "-g2012", "-s", "test", "-o", (root/"sim").string(), (root/"test.v").string(), argv[5]}, root/"iverilog.log", 30000);
        process::run({argv[4], (root/"sim").string()}, root/"simulate.log", 30000);
        for (const auto& file : {source, root/"native_trace.txt", root/"native_ram_final.txt", root/"native_stimulus.hex", root/"test.v", fs::absolute(argv[5])}) metadata["files"][file.string()] = artifacts::file_hash(file);
        artifacts::write(root/"native_trace.json", metadata.dump());
        auto imported = unwrap(import_recovered(source, argv[2], root/"import")); GroupingOptions settings; settings.mi_threads = settings.dana_threads = 1; settings.types = {hal::module_identification::CandidateType::counter, hal::module_identification::CandidateType::addition};
        for (auto* gate : imported.netlist->get_gates())
        {
            const auto type = gate->get_type()->get_name();
            if (type != "SB_DFFESR" && type != "SB_DFFNESR" && type != "SB_DFFESS" && type != "SB_DFFNESS") continue;
            const auto* component = gate->get_type()->get_component_as<hal::FFComponent>([](const hal::GateTypeComponent* c) { return hal::FFComponent::is_class_of(c); });
            require(component && gate->get_type()->get_pin_by_name("E"), "missing enabled FF component/pin");
            for (unsigned assignment = 0; assignment < 16; ++assignment)
            {
                using V = hal::BooleanFunction::Value;
                const bool e = assignment & 1, reset = assignment & 2, d = assignment & 4, q = assignment & 8, set = type.back() == 'S';
                std::unordered_map<std::string,V> inputs{{"E", e ? V::ONE : V::ZERO}, {"R", reset ? V::ONE : V::ZERO}, {"S", reset ? V::ONE : V::ZERO}, {"D", d ? V::ONE : V::ZERO}, {"IQ", q ? V::ONE : V::ZERO}};
                const bool expected = e ? (reset ? set : d) : q;
                require(unwrap(component->get_next_state_function().evaluate(inputs)) == (expected ? V::ONE : V::ZERO), "HAL enabled synchronous reset priority differs from Yosys");
            }
        }
        const auto grouped = unwrap(recover_groups(imported.netlist.get(), settings));
        const auto trace = Observations::load(root); z3::context ctx; OriginalCircuit original(source, ctx);
        const auto guided = unwrap(analyze_observations(imported.netlist.get(), grouped.groups, original, trace, ctx, root/"guided", 64));
        const auto exported = unwrap(export_state_machine(imported.netlist.get(), grouped.groups, original, guided, root/"state", ctx));
        require(exported.at("FF_bits") == 33 && exported.at("RAM_instances") == 1, "state fixture inventory changed");
        const auto schema = unwrap(check_state_artifacts(root/"state")); bool specialized = false;
        for (const auto& word : schema.at("words"))
            if (std::find(word.at("q_bits").begin(), word.at("q_bits").end(), Json(74)) != word.at("q_bits").end())
                for (const auto& variant : word.at("variants")) for (const auto& condition : variant.at("when"))
                    specialized |= condition.at("control") == "c_b4" && condition.at("value") == 1;
        require(specialized, "missing observed guard specialization; fallback test would be vacuous");
        auto report = unwrap(replay_state_machine(root/"state", trace, root, root/"replay", argv[6]));
        require(report.at("max_delta_events_observed") >= 3 && report.at("counts").at("known_final_RAM_bits") == 4096, "derived-clock or full RAM validation missing");
        const auto body = artifacts::read(root/"state"/"state_machine.cpp"); artifacts::write(root/"state"/"state_machine.cpp", body + "// fault\n");
        require(check_state_artifacts(root/"state").is_error(), "state driver tampering accepted"); artifacts::write(root/"state"/"state_machine.cpp", body);
        auto mutant = body; const std::string needle = "clock_edge(before, now, \"c_b2\", false)";
        require(mutant.find(needle) != std::string::npos, "cannot locate generated rising-edge branch");
        for (size_t pos = 0; (pos = mutant.find(needle, pos)) != std::string::npos; ++pos) mutant.replace(pos, needle.size(), "clock_edge(before, now, \"c_b2\", true)");
        const auto manifest_text = artifacts::read(root/"state"/"state_program.json"); auto manifest = Json::parse(manifest_text);
        artifacts::write(root/"state"/"state_machine.cpp", mutant); manifest["files"]["state_machine.cpp"] = artifacts::sha256(mutant);
        artifacts::write(root/"state"/"state_program.json", manifest.dump());
        const auto incorrect = replay_state_machine(root/"state", trace, root, root/"wrong_edge", argv[6]);
        require(incorrect.is_error() && incorrect.get_error().get().find("state original-gate mismatch") != std::string::npos, "wrong edge escaped original-gate replay");
        artifacts::write(root/"state"/"state_machine.cpp", body); artifacts::write(root/"state"/"state_program.json", manifest_text);
        auto wrong = trace; wrong.post[300][wrong.post[300].size()-1-wrong.positions.at(70)] = wrong.value(70,300,true) ? '0' : '1';
        require(replay_state_machine(root/"state", wrong, root, root/"wrong", argv[6]).is_error(), "changed state observations accepted");
        std::cout << "PASS native HAL grouping/extraction, 33 FF bits/20 primitive types, counter, derived clock/reset, IO unknowns, guarded fallback, inout feedback and RAM; continuous compiled original-gate replay 384 events, full final RAM and artifact mutants\n";
    }
    catch (const std::exception& e) { std::cerr << e.what() << '\n'; return 1; }
}
