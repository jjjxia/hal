#include "algorithm_recovery/memory.h"
#include "hal_core/netlist/endpoint.h"
#include "hal_core/netlist/gate.h"
#include "hal_core/netlist/gate_library/gate_type.h"
#include "hal_core/netlist/net.h"
#include "hal_core/netlist/netlist.h"
#include "hal_core/netlist/module.h"
#include "sequential_symbolic_execution/sequential_symbolic_execution.h"

#include <algorithm>
#include <cctype>
#include <set>

namespace hal::algorithm_recovery
{
    void step_ram(RamState& state, const RamPorts& p, bool read_edge, bool write_edge)
    {
        if (read_edge && p.read_enable && p.read_clock_enable)
            state.read = state.memory[p.read_address & 255];
        if (write_edge && p.write_enable && p.write_clock_enable)
        {
            auto& value = state.memory[p.write_address & 255];
            value = (value & p.mask) | (p.write_data & static_cast<u16>(~p.mask));
        }
    }

    namespace
    {
        Result<std::string> parameter(const Gate* gate, const std::string& key, size_t digits)
        {
            const auto& data = gate->get_data_map();
            const auto it = data.find({"generic", key});
            if (it == data.end())
                return ERR(gate->get_name() + ": missing parameter " + key);
            if (std::get<0>(it->second) != "bit_vector")
                return ERR(gate->get_name() + ": expected bit_vector parameter " + key);
            // HAL's Verilog importer stores bit-vector generic values in hexadecimal.
            auto value = std::get<1>(it->second);
            if (value.rfind("0x", 0) == 0 || value.rfind("0X", 0) == 0)
                value.erase(0, 2);
            if (value.empty() || !std::all_of(value.begin(), value.end(), [](unsigned char c) { return std::isxdigit(c); }))
                return ERR(gate->get_name() + ": non-concrete hexadecimal parameter " + key);
            const auto first = value.find_first_not_of('0');
            value = first == std::string::npos ? "0" : value.substr(first);
            if (value.size() > digits)
                return ERR(gate->get_name() + ": parameter too wide: " + key);
            return OK(std::string(digits - value.size(), '0') + value);
        }
    }

    Result<std::vector<Ice40Ram>> extract_ice40_rams(Netlist* nl, z3::context& ctx)
    {
        if (!nl)
            return ERR("null netlist");
        std::vector<bool> bitmap;
        for (const auto* gate : nl->get_gates())
            if (bitmap.size() <= gate->get_id())
                bitmap.resize(static_cast<size_t>(gate->get_id()) + 1, false);
        for (const auto* gate : nl->get_gates())
        {
            const auto& name = gate->get_type()->get_name();
            bitmap[gate->get_id()] = name == "SB_LUT4" || name == "SB_CARRY" || name == "VCC" || name == "GND";
        }
        const std::vector<std::pair<std::string, u32>> port_specs = {
            {"RADDR", 11}, {"WADDR", 11}, {"WDATA", 16}, {"MASK", 16},
            {"RE", 1}, {"RCLKE", 1}, {"WE", 1}, {"WCLKE", 1}};
        std::vector<Ice40Ram> result;
        for (auto* gate : nl->get_gates())
        {
            const auto& type = gate->get_type()->get_name();
            if (type != "SB_RAM40_4K")
            {
                if (type.rfind("SB_RAM", 0) == 0)
                    return ERR("unsupported RAM primitive: " + type);
                continue;
            }
            for (const auto& mode : {"READ_MODE", "WRITE_MODE"})
            {
                auto value = parameter(gate, mode, 1);
                if (value.is_error())
                    return ERR(value.get_error());
                if (value.get() != "0")
                    return ERR(gate->get_name() + ": unsupported " + mode + "=" + value.get());
            }
            Ice40Ram model;
            model.gate_id = gate->get_id();
            model.name = gate->get_name();
            for (size_t block = 0; block < 16; ++block)
            {
                auto value = parameter(gate, std::string("INIT_") + "0123456789ABCDEF"[block], 64);
                if (value.is_error())
                    return ERR(value.get_error());
                for (size_t word = 0; word < 16; ++word)
                    model.initial[16 * block + word] = static_cast<u16>(std::stoul(value.get().substr(60 - 4 * word, 4), nullptr, 16));
            }
            std::vector<std::vector<Net*>> words;
            auto specs = port_specs;
            specs.insert(specs.end(), {{"RDATA", 16}, {"RCLK", 1}, {"WCLK", 1}});
            for (const auto& [port, width] : specs)
            {
                std::vector<Net*> nets;
                for (u32 bit = 0; bit < width; ++bit)
                {
                    const auto pin = width == 1 ? port : port + "(" + std::to_string(bit) + ")";
                    auto* net = port == "RDATA" ? gate->get_fan_out_net(pin) : gate->get_fan_in_net(pin);
                    if (!net)
                        return ERR(model.name + ": unconnected pin " + pin);
                    model.pins[port].push_back(net->get_id());
                    nets.push_back(net);
                }
                if (words.size() < port_specs.size())
                    words.push_back(std::move(nets));
            }
            model.read_clock = model.pins.at("RCLK").front();
            model.write_clock = model.pins.at("WCLK").front();
            if (!nl->get_net_by_id(model.read_clock)->is_global_input_net() || !nl->get_net_by_id(model.write_clock)->is_global_input_net())
                return ERR(model.name + ": RAM recovery currently requires primary-input clocks");

            // Traverse the port cones before SSE: only FF/RAM outputs and primary inputs
            // may remain cutpoints. Unknown cells must not silently become free inputs.
            std::vector<Net*> pending;
            for (const auto& word : words)
                pending.insert(pending.end(), word.begin(), word.end());
            std::set<u32> seen;
            while (!pending.empty())
            {
                auto* net = pending.back();
                pending.pop_back();
                if (!seen.insert(net->get_id()).second)
                    continue;
                if (net->is_global_input_net())
                    continue;
                if (net->get_sources().size() != 1)
                    return ERR(model.name + ": ambiguous or undriven RAM port cone at net " + net->get_name());
                const auto* driver = net->get_sources().front()->get_gate();
                if (bitmap[driver->get_id()])
                {
                    const auto& inputs = driver->get_fan_in_nets();
                    pending.insert(pending.end(), inputs.begin(), inputs.end());
                }
                else if (!driver->get_type()->has_property(GateTypeProperty::ff) && driver->get_type()->get_name() != "SB_RAM40_4K")
                    return ERR(model.name + ": unsupported cell in RAM port cone: " + driver->get_type()->get_name());
            }
            auto values = sse::get_word_values_at_z3(words, std::vector<u32>(words.size(), 0), bitmap, {{}}, {}, false, ctx);
            if (values.is_error())
                return ERR_APPEND(values.get_error(), "RAM port extraction for " + model.name);
            for (size_t i = 0; i < port_specs.size(); ++i)
                model.ports.emplace(port_specs[i].first, values.get().at(i).simplify());
            result.push_back(std::move(model));
        }
        return OK(std::move(result));
    }
}
