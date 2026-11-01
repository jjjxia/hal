#pragma once
// Runtime for generated event/state programs. All combinational functions and
// all physical FF/RAM wiring come from the generated source, not this runtime.
#include "recovery_bits.h"
#include "json.hpp"
#include <array>
#include <iostream>
#include <map>

namespace hal_recovered
{
    using Json = nlohmann::json;
    using Pads = std::map<std::string, Integer>;
    using Controls = std::map<std::string, bool>;
    struct StateMemory
    {
        std::array<uint16_t, 256> data{};
        uint16_t read = 0;
        bool operator==(const StateMemory& other) const { return data == other.data && read == other.read; }
    };
    struct MachineState
    {
        std::map<std::string, Integer> words;
        std::map<std::string, StateMemory> memories;
        Pads pads;
    };
    using EvaluateControls = Controls (*)(const MachineState&, const Pads&);
    using ApplyEvent = MachineState (*)(const MachineState&, const Pads&, const Controls&, const Controls&);
    inline bool clock_edge(const Controls& before, const Controls& now, const std::string& clock, bool falling = false)
    { return falling ? before.at(clock) && !now.at(clock) : !before.at(clock) && now.at(clock); }
    inline void update_word(Integer& word, const Integer& value, const Integer& mask, unsigned width)
    {
        const Integer all = (Integer(1) << width) - 1;
        require(mask >= 0 && mask <= all, "FF mask outside state word");
        word = (word & (all ^ mask)) | (value & mask);
    }
    inline Integer state_integer(const Json& value, unsigned width)
    {
        require(value.is_string(), "state words must be decimal strings"); const auto text = value.get<std::string>();
        require(!text.empty() && text.find_first_not_of("0123456789") == std::string::npos, "invalid state decimal value");
        Integer result = 0; for (const auto c : text) { result *= 10; result += c - '0'; }
        require(width > 0 && result < (Integer(1) << width), "state word out of range"); return result;
    }
    inline uint16_t state_halfword(const Json& value)
    {
        require(value.is_number_integer() && value >= 0 && value <= 65535, "state RAM halfword out of range"); return value.get<uint16_t>();
    }
    inline Pads read_pads(const Json& values, const Json& schema)
    {
        require(values.is_object() && values.size() == schema.at("pads").size(), "wrong external pad inventory"); Pads result;
        for (auto pad = schema.at("pads").begin(); pad != schema.at("pads").end(); ++pad)
            result.emplace(pad.key(), state_integer(values.at(pad.key()), 1));
        return result;
    }
    inline Json machine_json(const MachineState& state)
    {
        Json words = Json::object(), pads = Json::object(), memories = Json::object();
        for (const auto& [name, word] : state.words) words[name] = word.str();
        for (const auto& [name, value] : state.pads) pads[name] = value.str();
        for (const auto& [name, ram] : state.memories) memories[name] = {{"data", ram.data}, {"read", ram.read}};
        return {{"words", words}, {"pads", pads}, {"memories", memories}};
    }
    inline MachineState read_machine(const Json& value, const Json& schema)
    {
        require(value.at("words").is_object() && value.at("words").size() == schema.at("words").size(), "wrong FF state inventory");
        require(value.at("memories").is_object() && value.at("memories").size() == schema.at("memories").size(), "wrong RAM state inventory");
        MachineState state; state.pads = read_pads(value.at("pads"), schema);
        for (auto word = schema.at("words").begin(); word != schema.at("words").end(); ++word)
            state.words.emplace(word.key(), state_integer(value.at("words").at(word.key()), word.value().at("width")));
        for (auto ram = schema.at("memories").begin(); ram != schema.at("memories").end(); ++ram)
        {
            const auto& encoded = value.at("memories").at(ram.key()); require(encoded.at("data").size() == 256, "wrong state RAM depth");
            StateMemory memory; memory.read = state_halfword(encoded.at("read"));
            for (unsigned i = 0; i < 256; ++i) memory.data[i] = state_halfword(encoded.at("data")[i]);
            state.memories.emplace(ram.key(), std::move(memory));
        }
        return state;
    }
    inline Json driven_outputs(const Json& schema, const Controls& controls)
    {
        Json result = Json::object();
        for (auto output = schema.at("outputs").begin(); output != schema.at("outputs").end(); ++output)
        {
            const bool enabled = controls.at(output.value().at("enable").get<std::string>()), data = controls.at(output.value().at("data").get<std::string>());
            result[output.key()] = {{"enable", enabled}, {"data", data}, {"drive", enabled ? (data ? "1" : "0") : "z"}};
        }
        return result;
    }
    // Optional, explicit testbench environment: external drive is released
    // while the device enables its own pad driver, matching native_trace_tb.v.
    // The normal step API instead accepts externally resolved physical pads.
    inline Pads resolve_drives(const Json& schema, const MachineState& state, const Pads& drives, EvaluateControls controls)
    {
        Pads pads = drives;
        for (unsigned delta = 0; delta < schema.at("max_delta_events").get<unsigned>(); ++delta)
        {
            const auto values = controls(state, pads); auto next = drives;
            for (const auto& output : schema.at("outputs"))
            {
                const auto pad = "pad" + std::to_string(output.at("physical").get<int64_t>());
                if (next.count(pad) && values.at(output.at("enable").get<std::string>()))
                    next[pad] = values.at(output.at("data").get<std::string>()) ? 1 : 0;
            }
            if (next == pads) return pads;
            pads = std::move(next);
        }
        throw std::runtime_error("NONQUIESCENT: inout driver resolution did not settle");
    }
    inline Json machine_request(const Json& schema, EvaluateControls controls, ApplyEvent apply, const Json& request)
    {
        const std::string command = request.at("command");
        if (command == "metadata") return schema;
        if (command == "initial")
        {
            const unsigned choice = request.value("unknown_ff_choice", 0u); require(choice <= 1, "initial FF completion must be 0 or 1");
            const auto read_choice = state_halfword(request.value("unknown_ram_read_choice", Json(0)));
            MachineState state; Json unknown = Json::object();
            for (auto word = schema.at("words").begin(); word != schema.at("words").end(); ++word)
            {
                const unsigned width = word.value().at("width"); const Integer all = (Integer(1) << width) - 1;
                const auto known = state_integer(word.value().at("initial_known_mask"), width);
                const Integer missing = all ^ known; unknown[word.key()] = missing.str();
                state.words[word.key()] = state_integer(word.value().at("initial"), width) | (choice ? missing : Integer(0));
            }
            for (auto ram = schema.at("memories").begin(); ram != schema.at("memories").end(); ++ram)
                state.memories.emplace(ram.key(), StateMemory{ram.value().at("initial").get<std::array<uint16_t, 256>>(), read_choice});
            for (auto pad = schema.at("pads").begin(); pad != schema.at("pads").end(); ++pad) state.pads[pad.key()] = 0;
            return {{"state", machine_json(state)}, {"initial_unknown_ff_masks", unknown}, {"ram_read_initially_known", false},
                {"two_state_completion", true}, {"unknown_ff_choice", choice}, {"unknown_ram_read_choice", read_choice}};
        }
        require(command == "step" || command == "drive", "unsupported state-machine request");
        auto state = read_machine(request.at("state"), schema); const auto input = read_pads(request.at("pads"), schema);
        auto previous = controls(state, state.pads);
        const unsigned limit = schema.at("max_delta_events"); require(limit > 0, "empty delta-event budget");
        for (unsigned delta = 0; delta < limit; ++delta)
        {
            const auto pads = command == "drive" ? resolve_drives(schema, state, input, controls) : input;
            const auto current = controls(state, pads); auto next = apply(state, pads, previous, current);
            next.pads = pads;
            if (next.words == state.words && next.memories == state.memories)
                return {{"state", machine_json(next)}, {"outputs", driven_outputs(schema, current)}, {"delta_events", delta + 1}, {"two_state_completion", true}};
            previous = current; state = std::move(next);
        }
        throw std::runtime_error("NONQUIESCENT: state machine did not settle within its explicit delta-event limit");
    }
    inline int run_state_machine(const char* schema_text, EvaluateControls controls, ApplyEvent apply)
    {
        try
        {
            const auto schema = Json::parse(schema_text); std::string line;
            while (std::getline(std::cin, line)) if (!line.empty()) std::cout << machine_request(schema, controls, apply, Json::parse(line)).dump() << std::endl;
            require(!std::cin.bad() && bool(std::cout), "state-machine stream failure"); return 0;
        }
        catch (const std::exception& error) { std::cerr << error.what() << '\n'; return 1; }
    }
}
