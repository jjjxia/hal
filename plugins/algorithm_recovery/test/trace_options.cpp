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
        for (const auto& [name, bit] : std::map<std::string, unsigned>{{"C",2}, {"D",3}, {"R",4}, {"Q",5}})
        { module["ports"][name] = {{"direction", name == "Q" ? "output" : "input"}, {"bits", {bit}}}; module["netnames"][name] = {{"bits", {bit}}}; }
        artifacts::write(out / "original.json", Json{{"modules", {{"recovered", module}}}}.dump());
        artifacts::write(out / "original.v", "module recovered(input C,D,R,output Q); SB_DFFR ff(.C(C),.D(D),.R(R),.Q(Q)); endmodule\n");
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
            }
        }
        require(generate_observations(out / "original.json", out / "original.v", argv[2], out / "duplicate", ctx, 93, 256, {"one", "one"}).is_error(), "duplicate profiles accepted");
        require(generate_observations(out / "original.json", out / "original.v", argv[2], out / "unknown", ctx, 93, 256, {"unknown"}).is_error(), "unknown profile accepted");
        std::cout << "PASS selected named profiles, configurable 256/1024-event original-gate traces, reset and known output bits\n";
    }
    catch (const std::exception& error) { std::cerr << error.what() << '\n'; return 1; }
}
