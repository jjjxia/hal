#pragma once

#include "hal_core/utilities/result.h"
#include <z3++.h>
#include <array>
#include <map>
#include <string>
#include <vector>

namespace hal
{
    class Netlist;

    namespace algorithm_recovery
    {
        // Primitive-specific semantics, not an inferred logical bank/address schedule.
        // The Z3 context must outlive the extracted expressions.
        struct Ice40Ram
        {
            u32 gate_id = 0;
            std::string name;
            u32 read_clock = 0;
            u32 write_clock = 0;
            std::array<u16, 256> initial{};
            std::map<std::string, std::vector<u32>> pins; // LSB first, HAL net IDs
            std::map<std::string, z3::expr> ports;       // symbolic HAL SSE expressions
        };

        struct RamPorts
        {
            u16 read_address = 0, write_address = 0, write_data = 0, mask = 0;
            bool read_enable = false, read_clock_enable = false;
            bool write_enable = false, write_clock_enable = false;
        };

        struct RamState
        {
            std::array<u16, 256> memory{};
            u16 read = 0; // an explicit two-state choice for the uninitialized read register
        };

        // Read-before-write on simultaneous edges; MASK=1 preserves that bit.
        void step_ram(RamState& state, const RamPorts& ports, bool read_edge, bool write_edge);

        // Fail closed on unsupported RAM modes, incomplete pins/INIT, non-primary clocks,
        // or unknown combinational primitives. No Python/observed-address inputs.
        Result<std::vector<Ice40Ram>> extract_ice40_rams(Netlist* netlist, z3::context& ctx);
    }
}
