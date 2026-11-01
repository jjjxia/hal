#include "algorithm_recovery/frontend.h"
#include "hal_core/netlist/gate.h"
#include <filesystem>
#include <fstream>
#include <iostream>

using Json = nlohmann::json;
namespace fs = std::filesystem;
static void check(bool ok, const char* why) { if (!ok) throw std::runtime_error(why); }
static void save(const fs::path& path, const Json& value) { std::ofstream out(path); out << value.dump(); check(bool(out), "fixture write failed"); }
int main(int argc, char** argv)
{
    if (argc != 3) return 2;
    try
    {
        // Build-tree-only scratch fixtures; clean only this test's own children.
        const fs::path root = fs::absolute(argv[2]), lib = fs::absolute(argv[1]);
        fs::create_directories(root);
        Json cell = {{"type", "$dff"}, {"parameters", {{"WIDTH", "1"}, {"CLK_POLARITY", "1"}}},
            {"connections", {{"CLK", {2}}, {"D", {3}}, {"Q", {4}}}}};
        Json source = {{"modules", {{"recovered", {{"ports", {{"clk", {{"direction", "input"}, {"bits", {2}}}},
                {"d", {{"direction", "input"}, {"bits", {3}}}}, {"q", {{"direction", "output"}, {"bits", {4}}}}}}, {"cells", {{"original_io_ff", cell}}}}}}}};
        auto run = [&](const std::string& name, const Json& fixture) {
            const auto file = root / (name + ".json"), out = root / name;
            fs::remove_all(out); save(file, fixture);
            return hal::algorithm_recovery::import_recovered(file, lib, out);
        };
        auto result = run("valid", source);
        if (result.is_error()) throw std::runtime_error(result.get_error().get());
        check(result.get().receipt.at("counts").at("cells") == 1, "valid import count");
        check(result.get().netlist->get_gates()[0]->get_type()->get_name() == "REC_IO_DFF", "IO DFF type was not preserved");
        auto modified = source;
        modified["modules"]["recovered"]["cells"]["original_io_ff"]["parameters"]["WIDTH"] = "10";
        check(run("wide", modified).is_error(), "wide IO FF was silently accepted");
        modified = source;
        modified["modules"]["recovered"]["cells"]["original_io_ff"]["parameters"]["CLK_POLARITY"] = "0";
        check(run("negative_edge", modified).is_error(), "unsupported edge accepted");
        modified = source;
        modified["modules"]["recovered"]["cells"]["original_io_ff"]["type"] = "unknown_primitive";
        check(run("unknown_type", modified).is_error(), "unknown primitive accepted");
        modified = source;
        modified["modules"]["recovered"]["cells"]["original_io_ff"]["parameters"]["WIDTH"] = "x";
        check(run("unknown_parameter", modified).is_error(), "unknown parameter silently concretized");
        check(hal::algorithm_recovery::import_recovered(root / "valid.json", lib, root / "valid").is_error(), "existing output was overwritten");
        std::cout << "PASS native frontend valid IO FF, unsupported widths/edges/types/parameters, output preservation\n";
    }
    catch (const std::exception& error) { std::cerr << error.what() << '\n'; return 1; }
}
