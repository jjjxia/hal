#pragma once
#include "algorithm_recovery/folding.h"
#include <filesystem>
#include <nlohmann/json.hpp>

namespace hal::algorithm_recovery
{
    using PhysicalBit = int64_t; // >=0 net ID; -1=0, -2=1, -3=x, -4=z
    PhysicalBit physical_bit(const nlohmann::json& bit);
    nlohmann::json bit_json(PhysicalBit bit);

    struct OriginalFF
    {
        std::string name, type;
        std::map<std::string, PhysicalBit> pins;
        bool falling = false, synchronous_reset = false;
    };
    // Independent original-JSON LUT truth-table/carry interpretation. No HAL
    // BooleanFunction or recovered word equations are used by this evaluator.
    class OriginalCircuit
    {
    public:
        OriginalCircuit(const std::filesystem::path& source, z3::context& context);
        z3::expr function(PhysicalBit bit);
        // Same interpretation with the given nets fixed to constants (memoised per call);
        // used by the guided fallback to build control-specialised equations from the original netlist.
        z3::expr function_with(PhysicalBit bit, const std::map<PhysicalBit, bool>& fixed, std::map<PhysicalBit, z3::expr>& memo);
        z3::expr word(const std::vector<PhysicalBit>& bits);
        std::vector<PhysicalBit> dependencies(PhysicalBit bit) const;
        std::set<PhysicalBit> support(PhysicalBit bit) const;
        bool combinational(PhysicalBit bit) const;
        const nlohmann::ordered_json& module() const { return m_module; }
        const std::map<PhysicalBit, nlohmann::ordered_json>& combinational_cells() const { return m_comb; }
        const std::set<PhysicalBit>& leaves() const { return m_leaves; }
        std::string module_name;
        std::string source_path, source_sha256;
        std::vector<std::string> constant_output_ports;  // output pads driven only by constants (dropped)
        std::map<PhysicalBit, OriginalFF> flip_flops;
        std::set<PhysicalBit> inputs, controls, clocks;
    private:
        z3::context& m_ctx;
        nlohmann::ordered_json m_module;
        std::map<PhysicalBit, nlohmann::ordered_json> m_comb;
        std::set<PhysicalBit> m_leaves, m_visiting;
        std::map<PhysicalBit, z3::expr> m_functions;
    };
    struct Observations
    {
        nlohmann::json metadata;
        std::map<PhysicalBit, size_t> positions;
        std::vector<std::string> pre, post; // MSB-first, x/z retained verbatim
        int value(PhysicalBit bit, size_t event, bool after = false) const;
        static Observations load(const std::filesystem::path& directory);
    };
    struct FFUpdate
    {
        Write write = Write::mixed_or_unknown;
        int reset_value = -1; // -1=data; 0/1=reset/set; unknown writes never folded
    };
    FFUpdate ff_update(const OriginalFF& ff, const Observations& trace, size_t event);

    // Uses original gate Verilog + Yosys cells_sim.v with Icarus. Only physical
    // pad stimulus; no internal force, protocol roles, or observed RAM oracle.
    Result<Observations> generate_observations(const std::filesystem::path& source,
        const std::filesystem::path& verilog, const std::filesystem::path& primitive_model,
        const std::filesystem::path& output, z3::context& ctx, u32 seed = 20261031, u32 profile_length = 768,
        const std::vector<std::string>& profiles = {"zero", "one", "pattern", "irregular"},
        const std::filesystem::path& stimulus_json = {});
}
