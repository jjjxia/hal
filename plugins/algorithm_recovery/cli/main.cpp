#include "algorithm_recovery/memory.h"
#include "algorithm_recovery/frontend.h"
#include "algorithm_recovery/grouping.h"
#include "algorithm_recovery/observations.h"
#include "algorithm_recovery/guided.h"
#include "algorithm_recovery/lifting.h"
#include "hal_core/netlist/netlist.h"
#include "hal_core/netlist/persistent/netlist_serializer.h"
#include "hal_core/plugin_system/plugin_manager.h"
#include <nlohmann/json.hpp>
#include <fstream>
#include <iostream>

int main(int argc, char** argv)
{
    if (!((argc == 4 && (std::string(argv[1]) == "extract-ram" || std::string(argv[1]) == "groups")) || (argc == 5 && std::string(argv[1]) == "import") || (argc == 6 && std::string(argv[1]) == "trace") || (argc == 7 && std::string(argv[1]) == "analyze")))
    {
        std::cerr << "Usage: hal-recover extract-ram input.hal output.json\n"
                     "       hal-recover import recovered.json library.hgl empty_output_directory\n"
                     "       hal-recover groups input.hal empty_output_directory\n"
                     "       hal-recover trace recovered.json recovered.v cells_sim.v empty_output_directory\n"
                     "       hal-recover analyze ordered.hal groups.json recovered.json trace_directory empty_output_directory\n"
                     "Native import, grouping, trace, SSE/folding and RAM extraction; full pipeline migration is in progress.\n";
        return 2;
    }
    try
    {
        if (std::string(argv[1]) == "trace")
        {
            z3::context ctx;
            auto trace = hal::algorithm_recovery::generate_observations(argv[2], argv[3], argv[4], argv[5], ctx);
            if (trace.is_error()) throw std::runtime_error(trace.get_error().get());
            std::cout << "TRACED " << trace.get().pre.size() << " events, " << trace.get().positions.size() << " observed nets\n";
            return 0;
        }
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
        if (std::string(argv[1]) == "analyze")
        {
            nlohmann::json groups; std::ifstream(argv[3]) >> groups;
            z3::context ctx; hal::algorithm_recovery::OriginalCircuit original(argv[4], ctx);
            auto trace = hal::algorithm_recovery::Observations::load(argv[5]);
            auto analyzed = hal::algorithm_recovery::analyze_observations(nl.get(), groups, original, trace, ctx, argv[6]);
            if (analyzed.is_error()) throw std::runtime_error(analyzed.get_error().get());
            nlohmann::json profiles = nlohmann::json::object();
            hal::algorithm_recovery::ArithmeticRecovery arithmetic;
            for (const auto& [name, input] : analyzed.get().windows)
            {
                auto folded = hal::algorithm_recovery::fold_observed(input, ctx);
                if (folded.is_error()) throw std::runtime_error(folded.get_error().get());
                const auto& graph = folded.get();
                profiles[name] = {{"available", graph.available}, {"period", graph.period}, {"origin", graph.origin},
                    {"nodes", graph.nodes.size()}, {"ram_nodes", graph.ram_nodes.size()}, {"histories", graph.histories.size()}, {"excluded", graph.excluded}};
                std::cout << "FOLDED " << name << ' ' << profiles[name].dump() << std::endl;
                auto routing = hal::algorithm_recovery::recover_routing(graph, input, std::filesystem::path(argv[6]) / ("routing_" + name));
                if (routing.is_error()) throw std::runtime_error(routing.get_error().get());
                profiles[name]["routing_groups"] = routing.get().accepted.size();
                std::cout << "ROUTING " << name << ' ' << routing.get().accepted.size() << " accepted" << std::endl;
                auto lifted = hal::algorithm_recovery::lift_arithmetic(graph, arithmetic, std::filesystem::path(argv[6]) / ("lift_" + name), routing.get());
                if (lifted.is_error()) throw std::runtime_error(lifted.get_error().get());
                profiles[name]["arithmetic_counts"] = lifted.get().at("counts");
                profiles[name]["fit_calls"] = lifted.get().at("fit_calls");
                std::cout << "LIFTED " << name << ' ' << lifted.get().at("counts").dump() << std::endl;
            }
            std::ofstream stream(std::filesystem::path(argv[6]) / "fold_report.json"); stream << profiles.dump(2) << '\n';
            if (!stream) throw std::runtime_error("cannot save fold report");
            return 0;
        }
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
