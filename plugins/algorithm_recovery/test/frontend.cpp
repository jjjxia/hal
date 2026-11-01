#include "algorithm_recovery/frontend.h"
#include "hal_core/netlist/gate.h"
#include "hal_core/netlist/net.h"
#include "hal_core/netlist/persistent/netlist_serializer.h"
#include <filesystem>
#include <fstream>
#include <iostream>
#include <set>

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
        check(result.get().receipt.at("restored_unused_input_ports").empty(), "connected inputs were treated as unused");
        auto unused = source;
        unused["modules"]["recovered"]["ports"]["unused_a"] = {{"direction", "input"}, {"bits", {7}}};
        unused["modules"]["recovered"]["ports"]["unused_b"] = {{"direction", "input"}, {"bits", {8}}};
        auto unused_result = run("unused_inputs", unused);
        if (unused_result.is_error()) throw std::runtime_error(unused_result.get_error().get());
        const auto& imported = unused_result.get();
        check(imported.netlist->get_global_input_nets().size() == 4 && imported.netlist->get_global_output_nets().size() == 1,
            "unused physical input pads disappeared during import");
        auto serialized = hal::netlist_serializer::deserialize_from_file(root / "unused_inputs" / "import.hal");
        check(bool(serialized) && serialized->get_global_input_nets().size() == 4 && serialized->get_global_output_nets().size() == 1,
            "unused physical input pads disappeared during serialization");
        std::set<Json> serialized_inputs;
        for (const auto* net : serialized->get_global_input_nets())
        {
            const auto physical = imported.physical_bits.at(net->get_id()); serialized_inputs.insert(physical);
            if (physical == 7 || physical == 8)
                check(net->get_sources().empty() && net->get_destinations().empty(), "unused input gained circuit behavior");
        }
        check(serialized_inputs == std::set<Json>{2,3,7,8}, "serialized physical input inventory changed");
        auto unused_inout = source;
        unused_inout["modules"]["recovered"]["ports"]["unused_bidirectional"] = {{"direction", "inout"}, {"bits", {9}}};
        check(run("unused_inout", unused_inout).is_error(), "unused-input repair fabricated bidirectional pad semantics");
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
        std::cout << "PASS native frontend IO FF, serialized unused inputs, unsupported widths/edges/types/parameters, exact IO preservation\n";
    }
    catch (const std::exception& error) { std::cerr << error.what() << '\n'; return 1; }
}
