#pragma once
#include "recovery_bits.h"
#include "json.hpp"
#include <array>
#include <cstdint>
#include <iostream>
#include <limits>
#include <map>
#include <tuple>

namespace hal_recovered
{
    using Json = nlohmann::json;
    using Kernel = Integer (*)(const std::vector<Integer>&);
    using Key = std::pair<std::string, unsigned>;
    using HistoryKey = std::tuple<std::string, unsigned, unsigned>;
    struct Memory { std::array<uint16_t, 256> data; uint16_t read; };
    struct Frame
    {
        std::map<Key, Integer> values;
        std::map<HistoryKey, Integer> history;
        std::map<std::string, Memory> memories;
        Json reads = Json::array();
    };
    inline Integer integer(const Json& value)
    {
        require(value.is_string(), "arbitrary-width values must be decimal strings");
        const auto text = value.get<std::string>();
        require(!text.empty() && text.find_first_not_of("0123456789") == std::string::npos, "invalid unsigned decimal value");
        // cpp_int interprets a leading zero as octal; accumulate decimal explicitly.
        Integer result = 0; for (const char c : text) { result *= 10; result += c - '0'; } return result;
    }
    inline void check_value(const Integer& value, unsigned width)
    { require(value >= 0 && value < (Integer(1) << width), "out-of-range frame value"); }
    inline uint16_t halfword(const Json& value)
    {
        require(value.is_number_integer() && value >= 0 && value <= 65535, "RAM value out of range");
        return value.get<uint16_t>();
    }
    inline Json memory_json(const std::map<std::string, Memory>& memories)
    {
        Json result = Json::object();
        for (const auto& [name, memory] : memories) result[name] = {{"data", memory.data}, {"read", memory.read}};
        return result;
    }
    inline std::map<std::string, Memory> parse_memories(const Json& data)
    {
        require(data.is_object(), "memory state must be an object");
        std::map<std::string, Memory> result;
        for (auto it = data.begin(); it != data.end(); ++it)
        {
            require(it.value().at("data").size() == 256, "wrong RAM depth");
            Memory memory{}; memory.read = halfword(it.value().at("read"));
            for (unsigned i = 0; i < 256; ++i)
                memory.data[i] = halfword(it.value().at("data").at(i));
            result.emplace(it.key(), memory);
        }
        return result;
    }
    inline Json frame_json(const Frame& frame)
    {
        Json values = Json::array(), history = Json::array();
        for (const auto& [key, value] : frame.values) values.push_back({key.first, key.second, value.str()});
        for (const auto& [key, value] : frame.history)
            history.push_back({std::get<0>(key), std::get<1>(key), std::get<2>(key), value.str()});
        return {{"values", values}, {"history", history}, {"memories", memory_json(frame.memories)}, {"reads", frame.reads}};
    }
    using InputValues = std::map<Key, Integer>;
    using HistoryValues = std::map<HistoryKey, Integer>;
    using FrameExecutor = void (*)(Frame&, const InputValues&, const HistoryValues&);
    inline Integer schedule_input(const InputValues& inputs, const std::string& name, unsigned phase, unsigned width)
    {
        const auto& value = inputs.at({name, phase}); check_value(value, width); return value;
    }
    inline Integer schedule_signal(const Frame& state, const HistoryValues& history, const std::string& name,
                                   unsigned phase, unsigned period, uint64_t lag, unsigned width)
    {
        require(period > 0 && phase < period, "invalid schedule time"); Integer value;
        if (lag <= phase) value = state.values.at({name, static_cast<unsigned>(phase - lag)});
        else
        {
            const uint64_t delta = lag - phase, delay = delta / period + (delta % period != 0);
            require(delay <= std::numeric_limits<unsigned>::max(), "schedule history delay overflow");
            const unsigned previous = delta % period ? period - delta % period : 0;
            value = history.at({name, previous, static_cast<unsigned>(delay)});
        }
        check_value(value, width); return value;
    }
    inline void schedule_write(Frame& state, const std::string& name, unsigned phase, const Integer& value)
    { require(state.values.emplace(Key{name, phase}, value).second, "duplicate structured word write"); }
    inline void schedule_memory(Frame& state, const InputValues& inputs, unsigned phase, const std::string& name,
                                unsigned read_clock, unsigned write_clock, const std::map<std::string, unsigned>& port, Json& reads)
    {
        auto& memory = state.memories.at(name); const auto before = memory.read;
        if (schedule_input(inputs, "event_rise_" + std::to_string(read_clock), phase, 1) != 0 && port.at("RE") && port.at("RCLKE"))
            memory.read = memory.data[port.at("RADDR") & 255];
        if (schedule_input(inputs, "event_rise_" + std::to_string(write_clock), phase, 1) != 0 && port.at("WE") && port.at("WCLKE"))
        {
            const unsigned mask = (~port.at("MASK")) & 65535, address = port.at("WADDR") & 255;
            memory.data[address] = (memory.data[address] & (~mask & 65535)) | (port.at("WDATA") & mask);
        }
        reads[name] = {before, memory.read};
    }
    class Program
    {
    public:
        Program(Json model, std::vector<Kernel> kernels, FrameExecutor executor = nullptr)
            : m_model(std::move(model)), m_kernels(std::move(kernels)), m_executor(executor)
        {
            const auto period = m_model.at("period").get<unsigned>(); require(period > 0, "no executable graph");
            m_nodes.resize(period); m_ram_nodes.resize(period);
            for (const auto& node : m_model.at("nodes")) m_nodes.at(node.at("phase").get<unsigned>()).push_back(node);
            for (const auto& node : m_model.at("ram_nodes")) m_ram_nodes.at(node.at("phase").get<unsigned>()).push_back(node);
        }
        Json request(const Json& request) const
        {
            const std::string command = request.at("command");
            if (command == "metadata") return m_model;
            if (command == "kernel")
            {
                const unsigned id = request.at("kernel"); std::vector<Integer> inputs;
                const auto widths = m_model.at("kernels").at(id).at("inputs").get<std::vector<unsigned>>();
                require(request.at("inputs").size() == widths.size(), "wrong kernel input count");
                for (unsigned i = 0; i < widths.size(); ++i)
                { auto value = integer(request.at("inputs").at(i)); check_value(value, widths[i] ? widths[i] : 1); inputs.push_back(value); }
                return {{"value", m_kernels.at(id)(inputs).str()}};
            }
            if (command == "initial")
            {
                const auto read = halfword(request.value("initial_read", Json(0)));
                std::map<std::string, Memory> memories;
                for (auto it = m_model.at("memories").begin(); it != m_model.at("memories").end(); ++it)
                    memories.emplace(it.key(), Memory{it.value().at("initial").get<std::array<uint16_t, 256>>(), static_cast<uint16_t>(read)});
                return {{"memories", memory_json(memories)}};
            }
            require(command == "run", "unsupported program command");
            std::map<Key, Integer> inputs; std::map<HistoryKey, Integer> history;
            for (const auto& row : request.at("inputs"))
                require(row.size() == 3 && inputs.emplace(Key{row.at(0), row.at(1)}, integer(row.at(2))).second, "duplicate or invalid frame input");
            for (const auto& row : request.at("history"))
                require(row.size() == 4 && history.emplace(HistoryKey{row.at(0), row.at(1), row.at(2)}, integer(row.at(3))).second, "duplicate or invalid history");
            Frame result; result.memories = parse_memories(request.at("memories"));
            require(result.memories.size() == m_model.at("memories").size(), "wrong memory inventory");
            for (auto it = m_model.at("memories").begin(); it != m_model.at("memories").end(); ++it)
                require(result.memories.count(it.key()), "missing memory instance");
            if (m_executor) m_executor(result, inputs, history);
            else
            {
            const auto evaluate = [&](const Json& node) {
                std::vector<Integer> operands;
                for (const auto& source : node.at("sources"))
                {
                    const std::string kind = source.at("kind"), name = source.at("name");
                    const unsigned phase = source.at("phase"), delay = source.at("delay"), width = source.at("width");
                    const Integer value = kind == "input" ? inputs.at({name, phase}) : kind == "ram" ? Integer(result.memories.at(name).read) :
                        delay ? history.at({name, phase, delay}) : result.values.at({name, phase});
                    check_value(value, width); operands.push_back(value);
                }
                return m_kernels.at(node.at("kernel").get<unsigned>())(operands);
            };
            const auto edge = [&](unsigned clock, unsigned phase) {
                const auto& value = inputs.at({"event_rise_" + std::to_string(clock), phase}); check_value(value, 1); return value != 0;
            };
            for (unsigned phase = 0; phase < m_nodes.size(); ++phase)
            {
                for (const auto& node : m_nodes[phase])
                    require(result.values.emplace(Key{node.at("name"), phase}, evaluate(node)).second, "duplicate write");
                std::map<std::string, std::map<std::string, unsigned>> ports;
                // Evaluate ALL port functions before updating ANY RAM read state.
                for (const auto& node : m_ram_nodes[phase])
                    require(ports[node.at("name").get<std::string>()].emplace(node.at("port"), evaluate(node).convert_to<unsigned>()).second, "duplicate RAM port");
                Json reads = Json::object();
                for (auto it = m_model.at("memories").begin(); it != m_model.at("memories").end(); ++it)
                {
                    auto& state = result.memories.at(it.key()); const auto& port = ports.at(it.key()); const auto before = state.read;
                    // Mode 0: synchronous read-before-write, held read register,
                    // independent clock edges/enables and inverted per-bit mask.
                    if (edge(it.value().at("read_clock"), phase) && port.at("RE") && port.at("RCLKE"))
                        state.read = state.data[port.at("RADDR") & 255];
                    if (edge(it.value().at("write_clock"), phase) && port.at("WE") && port.at("WCLKE"))
                    {
                        const unsigned mask = (~port.at("MASK")) & 65535, address = port.at("WADDR") & 255;
                        state.data[address] = (state.data[address] & (~mask & 65535)) | (port.at("WDATA") & mask);
                    }
                    reads[it.key()] = {before, state.read};
                }
                result.reads.push_back(std::move(reads));
            }
            }
            for (const auto& row : m_model.at("histories"))
            {
                const std::string name = row.at(0); const unsigned phase = row.at(1), delay = row.at(2);
                require(delay > 0, "zero-delay history");
                result.history.emplace(HistoryKey{name, phase, delay}, delay == 1 ? result.values.at({name, phase}) : history.at({name, phase, delay - 1}));
            }
            return frame_json(result);
        }
    private:
        Json m_model;
        std::vector<Kernel> m_kernels;
        FrameExecutor m_executor;
        std::vector<std::vector<Json>> m_nodes, m_ram_nodes;
    };
    inline int run_json_lines(const char* model, std::vector<Kernel> kernels, FrameExecutor executor = nullptr)
    {
        try
        {
            const Program program(Json::parse(model), std::move(kernels), executor);
            std::string line;
            while (std::getline(std::cin, line)) if (!line.empty()) std::cout << program.request(Json::parse(line)).dump() << std::endl;
            require(!std::cin.bad() && bool(std::cout), "program stream failure"); return 0;
        }
        catch (const std::exception& error) { std::cerr << error.what() << '\n'; return 1; }
    }
}
