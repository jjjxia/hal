#include "algorithm_recovery/observations.h"
#include "../src/artifacts.h"
#include <iostream>

using namespace hal::algorithm_recovery;
using Json = nlohmann::json;
namespace fs = std::filesystem;
using artifacts::require;
template<class T> static T unwrap(hal::Result<T>&& value)
{ if (value.is_error()) throw std::runtime_error(value.get_error().get()); return std::move(value.get()); }
int main(int argc, char** argv)
{
    if (argc != 3) return 2;
    try
    {
        const auto out = fs::absolute(argv[1]); fs::create_directories(out);
        Json module{{"ports", Json::object()}, {"netnames", Json::object()}, {"cells", Json::object()}};
        module["cells"]["ff"] = {{"type", "SB_DFFR"}, {"connections", {{"C", {2}}, {"D", {3}}, {"R", {4}}, {"Q", {5}}}}};
        module["cells"]["invert"] = {{"type", "SB_LUT4"}, {"parameters", {{"LUT_INIT", "0101010101010101"}}}, {"connections", {{"I0", {5}}, {"I1", {"0"}}, {"I2", {"0"}}, {"I3", {"0"}}, {"O", {6}}}}};
        for (const auto& [name, bit] : std::map<std::string, unsigned>{{"C",2}, {"D",3}, {"R",4}, {"Q",5}, {"O",6}})
        { module["ports"][name] = {{"direction", bit >= 5 ? "output" : "input"}, {"bits", {bit}}}; module["netnames"][name] = {{"bits", {bit}}}; }
        artifacts::write(out / "original.json", Json{{"modules", {{"recovered", module}}}}.dump());
        artifacts::write(out / "original.v", "module recovered(input C,D,R,output Q,O); SB_DFFR ff(.C(C),.D(D),.R(R),.Q(Q)); SB_LUT4 #(.LUT_INIT(16'h5555)) invert(.I0(Q),.I1(1'b0),.I2(1'b0),.I3(1'b0),.O(O)); endmodule\n");
        z3::context ctx;
        for (const auto& [profile, length] : std::map<std::string, unsigned>{{"one", 256}, {"pattern", 1024}})
        {
            const auto trace = unwrap(generate_observations(out / "original.json", out / "original.v", argv[2], out / profile, ctx, 93, length, {profile}));
            require(trace.pre.size() == length && trace.metadata.at("profile_length") == length && trace.metadata.at("profiles") == Json::array({profile}), "trace length/profile setting lost");
            require(trace.metadata.at("windows").size() == 1 && trace.metadata.at("windows").at(profile).size() == length - 128, "selected trace window differs");
            int q = 0;
            for (unsigned t = 0; t < length; ++t)
            {
                const int data = profile == "one" ? 1 : (t / 3) & 1;
                require(trace.value(3, t) == data && trace.value(4, t) == (t < 8), "selected profile used wrong stimulus or reset");
                if (t < 8) q = 0; else if (t % 2 == 0) q = data;
                require(trace.value(5, t, true) == q, "selected-profile Yosys output differs");
                require(trace.value(6, t, true) == 1-q, "combinational output observation missing or incorrect");
            }
        }
        require(generate_observations(out / "original.json", out / "original.v", argv[2], out / "duplicate", ctx, 93, 256, {"one", "one"}).is_error(), "duplicate profiles accepted");
        require(generate_observations(out / "original.json", out / "original.v", argv[2], out / "unknown", ctx, 93, 256, {"unknown"}).is_error(), "unknown profile accepted");

        // Exercise a protocol that the generic profiles cannot express: hold
        // clock high, disable a rising edge, and assert reset after startup.
        module["cells"]["ff"]["type"] = "SB_DFFER";
        module["cells"]["ff"]["connections"]["E"] = {7};
        module["ports"]["E"] = {{"direction", "input"}, {"bits", {7}}};
        module["netnames"]["E"] = {{"bits", {7}}};
        const auto external_source = out / "external_original.json", external_verilog = out / "external_original.v";
        artifacts::write(external_source, Json{{"modules", {{"recovered", module}}}}.dump());
        artifacts::write(external_verilog, "module recovered(input C,D,R,E,output Q,O); SB_DFFER ff(.C(C),.D(D),.E(E),.R(R),.Q(Q)); SB_LUT4 #(.LUT_INIT(16'h5555)) invert(.I0(Q),.I1(1'b0),.I2(1'b0),.I3(1'b0),.O(O)); endmodule\n");
        const auto supplied = out / "external_stimulus.json";
        Json external{{"schema", "PHYSICAL_PAD_STIMULUS_V1"}, {"source_sha256", artifacts::file_hash(external_source)},
            {"input_bits", {7,4,3,2}}, {"description", "External enable, repeated-clock and later-reset regression"},
            {"events", {{1,0,1,0}, {1,0,1,1}, {0,0,0,0}, {0,0,0,1}, {0,1,0,1},
                        {1,0,1,1}, {1,0,1,0}, {1,0,1,1}, {1,1,1,0}, {1,0,0,1}}},
            {"windows", {{"training", {2,3,4,5,6}}}}};
        artifacts::write(supplied, external.dump());
        const auto external_trace = unwrap(generate_observations(external_source, external_verilog, argv[2], out / "external", ctx, 93, 256, {"one"}, supplied));
        require(external_trace.pre.size() == 10 && external_trace.metadata.at("windows") == external.at("windows") &&
            external_trace.metadata.at("profiles") == Json::array({"training"}) && external_trace.metadata.at("profile_length").is_null() &&
            external_trace.metadata.at("initial_discard") == 0 && external_trace.metadata.at("seed").is_null(), "external trace contract or event count changed");
        require(external_trace.metadata.at("files").at(supplied.string()) == artifacts::file_hash(supplied) &&
            !external_trace.metadata.at("manual_semantic_inputs").empty(), "external stimulus identity/protocol input not recorded");
        const std::vector<int> expected{0,1,1,1,0,0,0,1,0,0};
        for (size_t t = 0; t < expected.size(); ++t)
        {
            require(external_trace.value(5, t, true) == expected[t] && external_trace.value(6, t, true) == 1-expected[t],
                "external enable/clock/reset behavior differs from supplied protocol");
            for (size_t i = 0; i < external.at("input_bits").size(); ++i)
                require(external_trace.value(external.at("input_bits")[i].get<PhysicalBit>(), t, true) == external.at("events")[t][i].get<int>(),
                    "external physical input row was overridden");
        }
        unsigned invalid_index = 0;
        const auto reject = [&](const Json& invalid, const std::string& reason) {
            const auto file = out / ("invalid_external_" + std::to_string(invalid_index++) + ".json");
            artifacts::write(file, invalid.dump());
            require(generate_observations(external_source, external_verilog, argv[2], out / (file.stem().string() + "_trace"),
                ctx, 93, 256, {"one"}, file).is_error(), reason);
        };
        auto invalid = external; invalid["source_sha256"] = "wrong"; reject(invalid, "external stale source identity accepted");
        invalid = external; invalid["input_bits"][0] = 5; reject(invalid, "internal-state stimulus accepted");
        invalid = external; invalid["input_bits"][0] = 2; reject(invalid, "duplicate external bit accepted");
        invalid = external; invalid["input_bits"].erase(0); reject(invalid, "missing external bit accepted");
        invalid = external; invalid["events"][0][0] = 2; reject(invalid, "non-bit external value accepted");
        invalid = external; invalid["events"][0].erase(0); reject(invalid, "external row width mismatch accepted");
        invalid = external; invalid["windows"]["training"] = {2,3,3}; reject(invalid, "duplicate training event accepted");
        invalid = external; invalid["windows"]["training"] = {2,4}; reject(invalid, "noncontiguous training window accepted");
        invalid = external; invalid["windows"]["training"] = {10}; reject(invalid, "out-of-bounds training event accepted");
        invalid = external; invalid["windows"]["training"] = {-1}; reject(invalid, "negative training event accepted");
        invalid = external; invalid["windows"]["training"] = Json::array(); reject(invalid, "empty training window accepted");
        invalid = external; invalid["windows"]["other"] = {6,7}; reject(invalid, "overlapping training windows accepted");
        std::cout << "PASS generic profiles and external physical-pad protocol, explicit training windows, clocks/enables/resets, and malformed-input rejection\n";
    }
    catch (const std::exception& error) { std::cerr << error.what() << '\n'; return 1; }
}
