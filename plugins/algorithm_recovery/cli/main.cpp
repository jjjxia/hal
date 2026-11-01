#include "algorithm_recovery/memory.h"
#include "algorithm_recovery/frontend.h"
#include "algorithm_recovery/grouping.h"
#include "hal_core/netlist/netlist.h"
#include "hal_core/netlist/persistent/netlist_serializer.h"
#include "hal_core/plugin_system/plugin_manager.h"
#include <nlohmann/json.hpp>
#include <fstream>
#include <iostream>

int main(int argc, char** argv)
{
    if (!((argc == 4 && (std::string(argv[1]) == "extract-ram" || std::string(argv[1]) == "groups")) || (argc == 5 && std::string(argv[1]) == "import")))
    {
        std::cerr << "Usage: hal-recover extract-ram input.hal output.json\n"
                     "       hal-recover import recovered.json library.hgl empty_output_directory\n"
                     "       hal-recover groups input.hal empty_output_directory\n"
                     "Native import, grouping and RAM extraction; full pipeline migration is in progress.\n";
        return 2;
    }
    try
    {
        if (std::string(argv[1]) == "import")
        {
            auto imported = hal::algorithm_recovery::import_recovered(argv[2], argv[3], argv[4]);
            if (imported.is_error()) throw std::runtime_error(imported.get_error().get());
            std::cout << imported.get().receipt.dump(2) << '\n';
            return 0;
        }
        if (!hal::plugin_manager::load("hgl_parser") || !hal::plugin_manager::load("algorithm_recovery"))
            throw std::runtime_error("cannot load hgl_parser or algorithm_recovery");
        auto nl = hal::netlist_serializer::deserialize_from_file(argv[2]);
        if (std::string(argv[1]) == "groups")
        {
            const auto out = std::filesystem::absolute(argv[3]);
            if (std::filesystem::exists(out) && !std::filesystem::is_empty(out)) throw std::runtime_error("group output directory must be empty");
            auto groups = hal::algorithm_recovery::recover_groups(nl.get());
            if (groups.is_error()) throw std::runtime_error(groups.get_error().get());
            std::filesystem::create_directories(out);
            for (const auto& [name, value] : std::map<std::string, nlohmann::json>{{"dana", groups.get().dana}, {"mi_simple", groups.get().candidates}, {"groups", groups.get().groups}})
            {
                std::ofstream stream(out / (name + ".json")); stream << value.dump(2) << '\n';
                if (!stream) throw std::runtime_error("cannot save " + name);
            }
            if (!hal::netlist_serializer::serialize_to_file(nl.get(), out / "ordered.hal")) throw std::runtime_error("cannot save ordered netlist");
            return 0;
        }
        z3::context ctx;
        auto result = hal::algorithm_recovery::extract_ice40_rams(nl.get(), ctx);
        if (result.is_error())
            throw std::runtime_error(result.get_error().get());
        nlohmann::json out = {{"schema", 1}, {"status", "RAM_PORTS_EXTRACTED"},
            {"semantics", "SB_RAM40_4K mode 0, read-before-write, MASK, INIT; read register initially unconstrained"},
            {"memories", nlohmann::json::object()}};
        for (const auto& ram : result.get())
        {
            auto& row = out["memories"][ram.name];
            row = {{"gate_id", ram.gate_id}, {"rclk", ram.read_clock}, {"wclk", ram.write_clock},
                   {"initial", ram.initial}, {"pins", ram.pins}};
            for (const auto& [port, term] : ram.ports)
                row["functions"][port] = {{"width", term.get_sort().bv_size()}, {"smt2", term.to_string()}};
        }
        std::ofstream stream(argv[3]);
        stream << out.dump(2) << '\n';
        if (!stream)
            throw std::runtime_error("cannot write output");
        std::cout << "Extracted " << result.get().size() << " RAMs using native HAL SSE\n";
    }
    catch (const std::exception& e)
    {
        std::cerr << e.what() << '\n';
        return 1;
    }
    return 0;
}
