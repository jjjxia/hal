// Migration differential check against the preserved Python adapter's certified
// RAM artifact. The artifact is test evidence only, never a recovery input.
#include "algorithm_recovery/memory.h"
#include "hal_core/netlist/gate.h"
#include "hal_core/netlist/net.h"
#include "hal_core/netlist/netlist.h"
#include "hal_core/netlist/persistent/netlist_serializer.h"
#include "hal_core/plugin_system/plugin_manager.h"
#include <nlohmann/json.hpp>
#include <fstream>
#include <iostream>
#include <stdexcept>

int main(int argc, char** argv)
{
    if (argc != 4)
    {
        std::cerr << "Usage: check-recovery-ram-extraction ordered.hal native_ram.json recovered.json\n";
        return 2;
    }
    try
    {
        if (!hal::plugin_manager::load("hgl_parser"))
            throw std::runtime_error("hgl_parser load failed");
        auto nl = hal::netlist_serializer::deserialize_from_file(argv[1]);
        if (!nl)
            throw std::runtime_error("netlist load failed");
        nlohmann::json baseline, original;
        std::ifstream(argv[2]) >> baseline;
        std::ifstream(argv[3]) >> original;
        const auto& modules = original.at("modules");
        if (modules.size() != 1)
            throw std::runtime_error("expected one recovered module");
        std::map<std::string, nlohmann::json> pads;
        for (auto it = modules.begin().value().at("ports").begin(); it != modules.begin().value().at("ports").end(); ++it)
            if (it.value().at("bits").size() == 1)
                pads.emplace(it.key(), it.value().at("bits")[0]);
        auto physical = [&](u32 id) -> nlohmann::json {
            const auto& name = nl->get_net_by_id(id)->get_name();
            if (pads.count(name))
                return pads.at(name);
            if (name == "'0'" || name == "'1'")
                return name.substr(1, 1);
            if (name.size() < 2 || name[0] != 'n' || name.find_first_not_of("0123456789", 1) != std::string::npos)
                throw std::runtime_error("unrecognized net name in migration fixture: " + name);
            return std::stoul(name.substr(1));
        };
        z3::context ctx;
        auto extracted = hal::algorithm_recovery::extract_ice40_rams(nl.get(), ctx);
        if (extracted.is_error())
            throw std::runtime_error(extracted.get_error().get());
        if (extracted.get().size() != baseline.at("memories").size())
            throw std::runtime_error("RAM count differs");
        size_t checks = 0;
        for (const auto& ram : extracted.get())
        {
            const auto& old = baseline.at("memories").at(ram.name);
            if (nlohmann::json(ram.initial) != old.at("initial") || physical(ram.read_clock) != old.at("rclk") || physical(ram.write_clock) != old.at("wclk"))
                throw std::runtime_error("INIT or clocks differ: " + ram.name);
            for (const auto& [port, ids] : ram.pins)
            {
                nlohmann::json pins = nlohmann::json::array();
                for (auto id : ids)
                    pins.push_back(physical(id));
                if (pins != old.at("pins").at(port))
                    throw std::runtime_error("pins differ: " + ram.name + "/" + port);
            }
            for (const auto& [port, value] : ram.ports)
            {
                z3::expr_vector from(ctx), to(ctx);
                // HAL symbols use native net IDs; the original baseline uses physical
                // bit IDs. This correspondence is used only by this migration test.
                for (const auto* net : nl->get_nets())
                {
                    const auto bit = physical(net->get_id());
                    from.push_back(ctx.bv_const(("net_" + std::to_string(net->get_id()) + "_0").c_str(), 1));
                    to.push_back(bit.is_string() ? ctx.bv_val(bit.get<std::string>().c_str(), 1)
                                                : ctx.bv_const(("b" + bit.dump()).c_str(), 1));
                }
                auto current = value;
                current = current.substitute(from, to).simplify();
                const auto& expected = old.at("functions").at(port);
                std::string input;
                for (const auto& symbol : expected.at("symbols"))
                    input += "(declare-const " + symbol.get<std::string>() + " (_ BitVec 1))\n";
                const auto term = expected.at("smt2").get<std::string>();
                input += "(assert (= " + term + " " + term + "))";
                auto previous = ctx.parse_string(input.c_str())[0].arg(0);
                z3::solver solver(ctx);
                solver.set("timeout", 60000u);
                solver.add(current != previous);
                if (solver.check() != z3::unsat)
                    throw std::runtime_error("port equivalence not proven: " + ram.name + "/" + port);
                ++checks;
            }
        }
        std::cout << "PASS " << extracted.get().size() << " RAMs, " << checks
                  << " universally equivalent port equations, exact pins/INIT/clocks\n";
        if (!extracted.get().empty())
        {
            auto* gate = nl->get_gate_by_id(extracted.get().front().gate_id);
            const auto old_mode = gate->get_data("generic", "READ_MODE");
            gate->set_data("generic", "READ_MODE", "bit_vector", "1");
            if (!hal::algorithm_recovery::extract_ice40_rams(nl.get(), ctx).is_error())
                throw std::runtime_error("unsupported mode was accepted");
            gate->set_data("generic", "READ_MODE", std::get<0>(old_mode), std::get<1>(old_mode));
            const auto old_init = gate->get_data("generic", "INIT_0");
            gate->set_data("generic", "INIT_0", "bit_vector", "x");
            if (!hal::algorithm_recovery::extract_ice40_rams(nl.get(), ctx).is_error())
                throw std::runtime_error("unknown INIT was accepted");
            gate->set_data("generic", "INIT_0", std::get<0>(old_init), std::get<1>(old_init));
            std::cout << "PASS unsupported mode and non-concrete INIT rejection\n";
        }
    }
    catch (const std::exception& error)
    {
        std::cerr << error.what() << '\n';
        return 1;
    }
}
