#include "algorithm_recovery/observations.h"
#include "artifacts.h"
#include <algorithm>
#include <boost/multiprecision/cpp_int.hpp>
#include <cerrno>
#include <fcntl.h>
#include <random>
#include <regex>
#include <sys/wait.h>
#include <unistd.h>

namespace hal::algorithm_recovery
{
    using Json = nlohmann::json;
    using Ordered = nlohmann::ordered_json;
    using artifacts::require;
    namespace
    {
        uint64_t binary_parameter(const Ordered& value, unsigned width, const std::string& name)
        {
            require(value.is_string(), "non-binary original parameter: " + name);
            const auto bits = value.get<std::string>();
            require(!bits.empty() && bits.size() <= width && bits.find_first_not_of("01") == std::string::npos,
                    "unknown or oversized original parameter: " + name);
            return std::stoull(bits, nullptr, 2);
        }
    }
    PhysicalBit physical_bit(const Json& bit)
    {
        if (bit.is_number_integer()) { auto value = bit.get<int64_t>(); require(value >= 0, "negative physical net"); return value; }
        if (bit == "0") return -1;
        if (bit == "1") return -2;
        if (bit == "x") return -3;
        if (bit == "z") return -4;
        throw std::runtime_error("invalid physical bit");
    }
    Json bit_json(PhysicalBit bit)
    {
        if (bit >= 0) return bit;
        require(bit >= -4, "invalid constant"); return std::string(1, "01xz"[-bit - 1]);
    }
    OriginalCircuit::OriginalCircuit(const std::filesystem::path& source, z3::context& context) : m_ctx(context)
    {
        const auto text = artifacts::read(source);
        source_path = std::filesystem::absolute(source).string(); source_sha256 = artifacts::sha256(text);
        auto json = Ordered::parse(text);
        require(json.at("modules").size() == 1, "expected one original module");
        module_name = json.at("modules").begin().key(); m_module = json.at("modules").begin().value();
        std::set<PhysicalBit> output_pads;
        for (const auto& port : m_module.at("ports")) for (const auto& bit : port.at("bits"))
        {
            if (port.at("direction") != "output") inputs.insert(physical_bit(bit));
            if (port.at("direction") != "input") output_pads.insert(physical_bit(bit));
        }
        m_leaves = inputs;
        const std::regex ff_type("SB_DFF(N?)(E?)(SR|SS|R|S)?");
        for (auto cell = m_module.at("cells").begin(); cell != m_module.at("cells").end(); ++cell)
        {
            const std::string type = cell.value().at("type"); const auto& pins = cell.value().at("connections");
            if (type == "SB_LUT4" || type == "SB_CARRY")
            {
                auto q = physical_bit(pins.at(type == "SB_LUT4" ? "O" : "CO").at(0));
                require(m_comb.emplace(q, cell.value()).second, "multiple combinational drivers");
            }
            else if (type == "SB_RAM40_4K")
                for (const auto& bit : pins.at("RDATA")) m_leaves.insert(physical_bit(bit));
            else if (type == "$mux")
            {
                require(pins.at("A") == Ordered::array({"z"}) && pins.at("B").size() == 1 && pins.at("S").size() == 1 &&
                        pins.at("Y").size() == 1 && output_pads.count(physical_bit(pins.at("Y")[0])), "unsupported non-pad mux cutpoint");
            }
            else
            {
                std::smatch match;
                require(type == "$dff" || std::regex_match(type, match, ff_type), "unsupported original primitive: " + type);
                OriginalFF ff; ff.name = cell.key(); ff.type = type;
                if (type != "$dff") { ff.falling = !match[1].str().empty(); ff.synchronous_reset = match[3] == "SR" || match[3] == "SS"; }
                else require(binary_parameter(cell.value().at("parameters").at("WIDTH"), 32, "WIDTH") == 1 &&
                             binary_parameter(cell.value().at("parameters").at("CLK_POLARITY"), 32, "CLK_POLARITY") == 1, "unsupported IO FF");
                for (auto pin = pins.begin(); pin != pins.end(); ++pin)
                {
                    require(pin.value().size() == 1, "non-scalar FF pin");
                    ff.pins.emplace(pin.key() == "CLK" ? "C" : pin.key(), physical_bit(pin.value()[0]));
                }
                require(flip_flops.emplace(ff.pins.at("Q"), ff).second, "multiple FF Q drivers");
                m_leaves.insert(ff.pins.at("Q")); clocks.insert(ff.pins.at("C"));
                for (const auto& pin : {"E", "R", "S"}) if (ff.pins.count(pin)) controls.insert(ff.pins.at(pin));
            }
        }
    }
    bool OriginalCircuit::combinational(PhysicalBit bit) const { return m_comb.count(bit) != 0; }
    std::vector<PhysicalBit> OriginalCircuit::dependencies(PhysicalBit bit) const
    {
        if (!m_comb.count(bit)) return {};
        const auto& cell = m_comb.at(bit); const auto& pins = cell.at("connections"); std::vector<PhysicalBit> result;
        if (cell.at("type") == "SB_CARRY")
            for (const auto& name : {"I0", "I1", "CI"}) result.push_back(physical_bit(pins.at(name)[0]));
        else
        {
            const auto table = binary_parameter(cell.at("parameters").at("LUT_INIT"), 16, "LUT_INIT");
            for (unsigned i = 0; i < 4; ++i)
                for (unsigned k = 0; k < 16; ++k)
                    if (((table >> k) ^ (table >> (k ^ (1u << i)))) & 1)
                    { result.push_back(physical_bit(pins.at("I" + std::to_string(i))[0])); break; }
        }
        return result;
    }
    std::set<PhysicalBit> OriginalCircuit::support(PhysicalBit bit) const
    {
        std::vector<PhysicalBit> pending{bit}; std::set<PhysicalBit> seen, leaves;
        while (!pending.empty())
        {
            const auto current = pending.back(); pending.pop_back(); if (!seen.insert(current).second) continue;
            if (combinational(current)) { auto deps = dependencies(current); pending.insert(pending.end(), deps.begin(), deps.end()); }
            else if (current >= 0) { require(m_leaves.count(current), "unsupported original endpoint"); leaves.insert(current); }
            else require(current >= -2, "unknown constant in proof cone");
        }
        return leaves;
    }
    z3::expr OriginalCircuit::function(PhysicalBit bit)
    {
        if (bit < 0) { require(bit >= -2, "unknown constant in original function"); return m_ctx.bool_val(bit == -2); }
        if (m_functions.count(bit)) return m_functions.at(bit);
        if (!combinational(bit))
        {
            require(m_leaves.count(bit), "unrecognized original cutpoint " + std::to_string(bit));
            auto value = m_ctx.bv_const(("b" + std::to_string(bit)).c_str(), 1) == 1; m_functions.emplace(bit, value); return value;
        }
        require(m_visiting.insert(bit).second, "combinational feedback in original netlist");
        const auto& cell = m_comb.at(bit); const auto& pins = cell.at("connections");
        auto value = m_ctx.bool_val(false);
        if (cell.at("type") == "SB_CARRY")
        {
            const auto a = function(physical_bit(pins.at("I0")[0])), b = function(physical_bit(pins.at("I1")[0])), c = function(physical_bit(pins.at("CI")[0]));
            value = (a && b) || (a && c) || (b && c);
        }
        else
        {
            const auto table = binary_parameter(cell.at("parameters").at("LUT_INIT"), 16, "LUT_INIT");
            std::function<z3::expr(int, unsigned)> tree = [&](int i, unsigned position) -> z3::expr {
                if (i < 0) return m_ctx.bool_val((table >> position) & 1);
                auto lo = tree(i - 1, position), hi = tree(i - 1, position + (1u << i));
                return z3::eq(lo, hi) ? lo : z3::ite(function(physical_bit(pins.at("I" + std::to_string(i))[0])), hi, lo);
            };
            value = tree(3, 0);
        }
        value = value.simplify(); m_visiting.erase(bit); m_functions.emplace(bit, value); return value;
    }
    z3::expr OriginalCircuit::word(const std::vector<PhysicalBit>& bits)
    {
        require(!bits.empty(), "empty original word"); z3::expr_vector values(m_ctx);
        for (auto it = bits.rbegin(); it != bits.rend(); ++it) values.push_back(z3::ite(function(*it), m_ctx.bv_val(1, 1), m_ctx.bv_val(0, 1)));
        return values.size() == 1 ? values[0] : z3::concat(values);
    }
    int Observations::value(PhysicalBit bit, size_t event, bool after) const
    {
        if (bit < 0) return bit == -1 ? 0 : bit == -2 ? 1 : -1;
        const auto& values = (after ? post : pre).at(event); const auto pos = positions.at(bit);
        const auto v = values.at(values.size() - 1 - pos); return v == '0' ? 0 : v == '1' ? 1 : -1;
    }
    Observations Observations::load(const std::filesystem::path& directory)
    {
        Observations result; result.metadata = Json::parse(artifacts::read(directory / "native_trace.json"));
        for (auto it = result.metadata.at("files").begin(); it != result.metadata.at("files").end(); ++it)
            require(artifacts::file_hash(it.key()) == it.value(), "trace input/artifact hash mismatch: " + it.key());
        require(result.metadata.at("files").contains(std::filesystem::absolute(directory / "native_trace.txt").string()), "trace data is not bound by receipt");
        for (const auto& bit : result.metadata.at("observed")) require(result.positions.emplace(physical_bit(bit), result.positions.size()).second, "duplicate observation bit");
        std::ifstream in(directory / "native_trace.txt"); require(bool(in), "missing trace"); std::string line;
        while (std::getline(in, line))
        {
            std::istringstream row(line); std::string pre, post, extra;
            require(bool(row >> pre >> post) && !(row >> extra), "malformed trace row");
            for (const auto* value : {&pre, &post}) require(value->size() == result.positions.size() && value->find_first_not_of("01xz") == std::string::npos, "invalid trace width/value");
            result.pre.push_back(std::move(pre)); result.post.push_back(std::move(post));
        }
        require(result.pre.size() == result.metadata.at("frames"), "truncated trace"); return result;
    }
    FFUpdate ff_update(const OriginalFF& ff, const Observations& tr, size_t event)
    {
        const int before = tr.value(ff.pins.at("C"), event), after = tr.value(ff.pins.at("C"), event, true);
        const int edge = before < 0 || after < 0 ? -1 : ff.falling ? (before == 1 && after == 0) : (before == 0 && after == 1);
        const int enable = ff.pins.count("E") ? tr.value(ff.pins.at("E"), event) : 1;
        const bool reset_pin = ff.pins.count("R"), set_pin = ff.pins.count("S");
        require(!(reset_pin && set_pin), "unsupported simultaneous reset/set primitive");
        const int reset = reset_pin || set_pin ? tr.value(ff.pins.at(reset_pin ? "R" : "S"), event) : 0;
        if (!ff.synchronous_reset && (reset_pin || set_pin))
        {
            if (reset == 1) return {Write::full, set_pin ? 1 : 0};
            if (reset < 0) return {};
        }
        if (edge == 0 || enable == 0) return {Write::hold, -1};
        if (edge < 0 || enable < 0) return {};
        if (ff.synchronous_reset && reset < 0) return {};
        return {Write::full, ff.synchronous_reset && reset == 1 ? (set_pin ? 1 : 0) : -1};
    }

    namespace
    {
        // Integer-seed MT19937 initialization compatible with the prior stimulus
        // stream. std::mt19937 supplies generation; no Python runtime is linked here.
        // Initialization algorithm: CPython v3.12.0 Modules/_randommodule.c,
        // init_by_array (MT19937, Nishimura/Matsumoto 2002).
        struct IntegerSeed
        {
            using result_type = uint32_t;
            uint32_t key;
            template<class Iterator> void generate(Iterator first, Iterator last)
            {
                require(std::distance(first, last) == 624, "unexpected MT19937 seed-state size");
                std::array<uint32_t, 624> state{}; state[0] = 19650218;
                for (size_t n = 1; n < state.size(); ++n) state[n] = 1812433253u * (state[n - 1] ^ (state[n - 1] >> 30)) + n;
                size_t index = 1;
                for (unsigned pass = 0; pass < 2; ++pass)
                    for (size_t count = 0; count < state.size() - pass; ++count)
                    {
                        const auto prior = state[index - 1];
                        state[index] ^= (prior ^ (prior >> 30)) * (pass ? 1566083941u : 1664525u);
                        state[index] += pass ? uint32_t(0u - index) : key;
                        if (++index == state.size()) { state[0] = state.back(); index = 1; }
                    }
                state[0] = 0x80000000u; std::copy(state.begin(), state.end(), first);
            }
        };
        std::string quote(const std::string& value)
        {
            std::string result = "\"";
            for (auto c : value) { require(c != '\n' && c != '\r', "newline in Verilog path"); if (c == '\\' || c == '"') result += '\\'; result += c; }
            return result + '"';
        }
        void identifier(const std::string& value)
        {
            static const std::regex valid("[A-Za-z_][A-Za-z_0-9]*"); require(std::regex_match(value, valid), "unsupported observation identifier " + value);
        }
        void execute(const std::vector<std::string>& command, const std::filesystem::path& output)
        {
            std::vector<char*> argv; for (const auto& arg : command) argv.push_back(const_cast<char*>(arg.c_str())); argv.push_back(nullptr);
            const int log = open(output.c_str(), O_CREAT | O_TRUNC | O_WRONLY, 0600); require(log >= 0, "cannot create simulator log");
            const auto pid = fork();
            if (pid == 0)
            {
                if (dup2(log, STDOUT_FILENO) < 0 || dup2(log, STDERR_FILENO) < 0) _exit(126);
                close(log); execvp(argv[0], argv.data()); _exit(127);
            }
            close(log); require(pid >= 0, "cannot start simulator"); int status = 0; pid_t waited;
            do { waited = waitpid(pid, &status, 0); } while (waited < 0 && errno == EINTR);
            require(waited == pid && WIFEXITED(status) && WEXITSTATUS(status) == 0, command.front() + " failed; see " + output.string());
        }
    }

    Result<Observations> generate_observations(const std::filesystem::path& source, const std::filesystem::path& verilog,
        const std::filesystem::path& primitive_model, const std::filesystem::path& output, z3::context& ctx, u32 seed, u32 length,
        const std::vector<std::string>& profiles, const std::filesystem::path& stimulus_json)
    {
        try
        {
            const bool external = !stimulus_json.empty();
            if (!external)
            {
                require(length > 128 && length <= (1u << 20), "invalid stimulus length");
                require(!profiles.empty(), "no stimulus profiles selected"); std::set<std::string> seen_profiles;
                for (const auto& profile : profiles)
                    require((profile == "zero" || profile == "one" || profile == "pattern" || profile == "irregular") && seen_profiles.insert(profile).second,
                        "unknown or duplicate stimulus profile: " + profile);
            }
            OriginalCircuit circuit(source, ctx); const auto& original = circuit.module();
            Json external_input;
            std::string external_text;
            std::map<PhysicalBit, size_t> external_positions;
            if (external)
            {
                external_text = artifacts::read(stimulus_json);
                external_input = Json::parse(external_text);
                require(external_input.is_object() && external_input.at("schema") == "PHYSICAL_PAD_STIMULUS_V1", "unsupported external stimulus schema");
                require(external_input.at("source_sha256") == circuit.source_sha256, "external stimulus source identity mismatch");
                require(external_input.at("description").is_string(), "external stimulus description must be a string");
                const auto& bits = external_input.at("input_bits");
                require(bits.is_array() && bits.size() == circuit.inputs.size(), "external stimulus must enumerate every physical input exactly once");
                for (size_t i = 0; i < bits.size(); ++i)
                {
                    require(bits[i].is_number_integer(), "external stimulus input is not a physical bit ID");
                    const auto bit = physical_bit(bits[i]);
                    require(circuit.inputs.count(bit) && external_positions.emplace(bit, i).second,
                        "external stimulus contains an internal, unknown, or duplicate input bit");
                }
                std::set<PhysicalBit> port_inputs;
                for (const auto& port : original.at("ports")) if (port.at("direction") != "output")
                    for (const auto& bit : port.at("bits"))
                        require(port_inputs.insert(physical_bit(bit)).second, "external stimulus does not accept aliased input ports");
                const auto& events = external_input.at("events");
                require(events.is_array() && !events.empty() && events.size() <= (1u << 20), "invalid external stimulus event count");
                for (const auto& row : events)
                {
                    require(row.is_array() && row.size() == bits.size(), "external stimulus row width mismatch");
                    for (const auto& value : row)
                        require(value.is_number_integer() && (value == 0 || value == 1), "external stimulus values must be integer 0 or 1");
                }
                const auto& windows = external_input.at("windows");
                require(windows.is_object() && !windows.empty(), "external stimulus requires nonempty training windows");
                std::set<size_t> selected_events;
                for (auto window = windows.begin(); window != windows.end(); ++window)
                {
                    identifier(window.key());
                    require(window.value().is_array() && !window.value().empty(), "external stimulus window is empty or not an array");
                    size_t prior = 0;
                    for (size_t i = 0; i < window.value().size(); ++i)
                    {
                        const auto& event = window.value()[i];
                        require(event.is_number_integer() && event >= 0 && event < events.size(), "external stimulus window index out of bounds");
                        const auto index = event.get<size_t>();
                        require((i == 0 || index == prior + 1) && selected_events.insert(index).second,
                            "external stimulus windows must be contiguous and cannot duplicate events");
                        prior = index;
                    }
                }
            }
            std::map<PhysicalBit, unsigned> clock_counts;
            std::set<PhysicalBit> resets;
            for (const auto& [q, ff] : circuit.flip_flops)
            {
                ++clock_counts[ff.pins.at("C")];
                for (const auto& pin : {"R", "S"}) if (ff.pins.count(pin)) resets.insert(ff.pins.at(pin));
            }
            std::vector<PhysicalBit> clocks(circuit.clocks.begin(), circuit.clocks.end());
            require(!clocks.empty() && clocks.size() < 28, "unsupported clock count");
            for (auto clock : clocks) require(circuit.inputs.count(clock), "non-primary clock requires a different stimulus adapter");
            std::sort(clocks.begin(), clocks.end(), [&](auto a, auto b) { return clock_counts[a] != clock_counts[b] ? clock_counts[a] > clock_counts[b] : a < b; });
            std::vector<PhysicalBit> pads;
            std::set_difference(circuit.inputs.begin(), circuit.inputs.end(), circuit.clocks.begin(), circuit.clocks.end(), std::back_inserter(pads));
            const std::set<PhysicalBit> pad_set(pads.begin(), pads.end());
            std::vector<PhysicalBit> direct, reset_order(resets.begin(), resets.end());
            std::sort(reset_order.begin(), reset_order.end(), [](auto a, auto b) { return std::to_string(a) < std::to_string(b); });
            std::set<PhysicalBit> reset_pads;
            for (auto reset : reset_order) if (!external)
            {
                const auto support = circuit.support(reset);
                if (!support.empty() && std::includes(pad_set.begin(), pad_set.end(), support.begin(), support.end()))
                { direct.push_back(reset); reset_pads.insert(support.begin(), support.end()); }
            }
            std::map<PhysicalBit, unsigned> release, asserted;
            if (!external)
            {
                z3::solver inactive(ctx), active(ctx); inactive.set("timeout", 60000u); active.set("timeout", 60000u);
                for (auto reset : direct) { inactive.add(!circuit.function(reset)); active.add(circuit.function(reset)); }
                require(inactive.check() == z3::sat, "cannot establish inactive pad-controlled resets");
                for (auto pad : reset_pads) release[pad] = inactive.get_model().eval(circuit.function(pad), true).is_true();
                if (!direct.empty())
                {
                    const auto status = active.check(); require(status != z3::unknown, "reset assertion query UNKNOWN");
                    if (status == z3::sat) for (auto pad : reset_pads) asserted[pad] = active.get_model().eval(circuit.function(pad), true).is_true();
                }
            }
            std::map<PhysicalBit, std::string> lookup;
            for (auto it = original.at("netnames").begin(); it != original.at("netnames").end(); ++it)
            {
                if (!std::regex_match(it.key(), std::regex("[A-Za-z_][A-Za-z_0-9]*"))) continue;
                const auto& bits = it.value().at("bits");
                for (size_t i = 0; i < bits.size(); ++i) if (bits[i].is_number_integer())
                    lookup.emplace(physical_bit(bits[i]), it.key() + (bits.size() == 1 ? "" : "[" + std::to_string(i) + "]"));
            }
            std::set<PhysicalBit> required = circuit.inputs;
            for (const auto& port : original.at("ports"))
                for (const auto& bit : port.at("bits")) required.insert(physical_bit(bit));
            required.insert(circuit.clocks.begin(), circuit.clocks.end()); required.insert(circuit.controls.begin(), circuit.controls.end());
            for (const auto& [q, ff] : circuit.flip_flops) { required.insert(q); required.insert(ff.pins.at("D")); }
            std::map<PhysicalBit, PhysicalBit> tristates;
            for (const auto& cell : original.at("cells"))
            {
                if (cell.at("type") == "$mux")
                {
                    require(tristates.emplace(physical_bit(cell.at("connections").at("Y")[0]), physical_bit(cell.at("connections").at("S")[0])).second, "multiple pad tristates");
                    for (const auto* pin : {"B", "S", "Y"}) required.insert(physical_bit(cell.at("connections").at(pin)[0]));
                }
                if (cell.at("type") == "SB_RAM40_4K") for (const auto& connection : cell.at("connections"))
                    for (const auto& bit : connection) required.insert(physical_bit(bit));
            }
            for (auto it = required.begin(); it != required.end();) { if (*it < 0) it = required.erase(it); else { require(lookup.count(*it), "unobservable physical net"); ++it; } }
            std::vector<PhysicalBit> observed(required.begin(), required.end());
            IntegerSeed state{seed}; std::mt19937 random(state);
            std::ostringstream stimulus; Json windows = Json::object();
            std::vector<std::string> actual_profiles = profiles;
            const size_t event_count = external ? external_input.at("events").size() : profiles.size() * length;
            if (external)
            {
                windows = external_input.at("windows"); actual_profiles.clear();
                for (auto window = windows.begin(); window != windows.end(); ++window) actual_profiles.push_back(window.key());
                for (const auto& row : external_input.at("events"))
                {
                    boost::multiprecision::cpp_int packed = 0;
                    for (size_t i = 0; i < pads.size(); ++i)
                        if (row[external_positions.at(pads[i])] == 1) packed |= boost::multiprecision::cpp_int(1) << i;
                    for (size_t i = 0; i < clocks.size(); ++i)
                        if (row[external_positions.at(clocks[i])] == 1) packed |= boost::multiprecision::cpp_int(1) << (pads.size() + i);
                    stimulus << std::hex << packed << '\n';
                }
            }
            else for (unsigned profile = 0; profile < profiles.size(); ++profile)
            {
                windows[profiles[profile]] = Json::array();
                for (unsigned t = 0; t < length; ++t)
                {
                    boost::multiprecision::cpp_int packed = 0;
                    for (unsigned i = 0; i < pads.size(); ++i)
                    {
                        unsigned value = profiles[profile] == "zero" ? 0 : profiles[profile] == "one" ? 1 : profiles[profile] == "pattern" ? (t / (3 + 2 * (i % 11))) & 1 : random() >> 31;
                        const auto& reset = t < 8 ? asserted : release; if (reset.count(pads[i])) value = reset.at(pads[i]);
                        if (value) packed |= boost::multiprecision::cpp_int(1) << i;
                    }
                    for (unsigned i = 0; i < clocks.size(); ++i)
                        if (((profile * length + t + 1) / (i == 0 ? 1u : (1u << (3 + i)))) & 1) packed |= boost::multiprecision::cpp_int(1) << (pads.size() + i);
                    stimulus << std::hex << packed << '\n';
                    if (t >= 128) windows[profiles[profile]].push_back(profile * length + t);
                }
            }
            const auto out = std::filesystem::absolute(output);
            require(!std::filesystem::exists(out) || std::filesystem::is_empty(out), "trace output directory must be empty");
            std::filesystem::create_directories(out); identifier(circuit.module_name);
            const auto stim = out / "native_stimulus.hex", tb_path = out / "native_trace_tb.v", trace = out / "native_trace.txt", final_ram = out / "native_ram_final.txt";
            artifacts::write(stim, stimulus.str()); std::ostringstream tb;
            tb << "`timescale 1ns/1ps\nmodule test;\nreg [" << pads.size() + clocks.size() - 1 << ":0] stimulus[0:" << event_count - 1 << "];\ninteger i,fd;\n";
            for (auto bit : circuit.inputs) tb << "reg drive_" << bit << "=0;\n";
            std::string connections;
            for (auto port = original.at("ports").begin(); port != original.at("ports").end(); ++port)
            {
                identifier(port.key()); require(port.value().at("bits").size() == 1, "non-scalar pad");
                const auto q = physical_bit(port.value().at("bits")[0]); std::string net = "drive_" + std::to_string(q);
                if (port.value().at("direction") != "input")
                {
                    net = "pad_" + std::to_string(q); tb << "wire " << net << ";\n";
                    if (port.value().at("direction") == "inout")
                    {
                        const auto enable = tristates.at(q); const auto expr = enable >= 0 ? "dut." + lookup.at(enable) : "1'b" + bit_json(enable).get<std::string>();
                        tb << "assign " << net << "=(" << expr << " === 1'b0) ? drive_" << q << " : 1'bz;\n";
                    }
                }
                if (!connections.empty()) connections += ','; connections += '.' + port.key() + '(' + net + ')';
            }
            tb << circuit.module_name << " dut(" << connections << ");\nwire [" << observed.size() - 1 << ":0] observed={";
            for (auto it = observed.rbegin(); it != observed.rend(); ++it) { if (it != observed.rbegin()) tb << ','; tb << "dut." << lookup.at(*it); }
            tb << "};\ninitial begin\n$readmemh(" << quote(stim.string()) << ",stimulus);\nfd=$fopen(" << quote(trace.string()) << ",\"w\");\nfor(i=0;i<" << event_count << ";i=i+1) begin\n";
            for (unsigned i = 0; i < pads.size(); ++i) tb << "drive_" << pads[i] << "=stimulus[i][" << i << "];\n";
            tb << "#2;\n$fwrite(fd,\"%b \",observed);\n";
            for (unsigned i = 0; i < clocks.size(); ++i) tb << "drive_" << clocks[i] << "=stimulus[i][" << pads.size() + i << "];\n";
            tb << "#2;\n$fwrite(fd,\"%b\\n\",observed);\nend\n$fclose(fd);\nfd=$fopen(" << quote(final_ram.string()) << ",\"w\");\n";
            for (auto cell = original.at("cells").begin(); cell != original.at("cells").end(); ++cell)
                if (cell.value().at("type") == "SB_RAM40_4K")
                { identifier(cell.key()); tb << "for(i=0;i<256;i=i+1) $fwrite(fd,\"" << cell.key() << " %0d %b\\n\",i,dut." << cell.key() << ".memory[i]);\n"; }
            tb << "$fclose(fd);$finish;end endmodule\n"; artifacts::write(tb_path, tb.str());
            execute({"iverilog", "-g2012", "-s", "test", "-o", (out / "native_trace.vvp").string(), tb_path.string(), std::filesystem::absolute(verilog).string(), std::filesystem::absolute(primitive_model).string()}, out / "compile.log");
            execute({"vvp", (out / "native_trace.vvp").string()}, out / "simulate.log");
            Json controls = Json::array(), release_json = Json::object(), asserted_json = Json::object(), files = Json::object();
            for (auto bit : circuit.controls) controls.push_back(bit_json(bit));
            for (auto [bit, value] : release) release_json[std::to_string(bit)] = value;
            for (auto [bit, value] : asserted) asserted_json[std::to_string(bit)] = value;
            for (const auto& file : {std::filesystem::absolute(source), std::filesystem::absolute(verilog), std::filesystem::absolute(primitive_model), tb_path, stim, trace, final_ram}) files[file.string()] = artifacts::file_hash(file);
            if (external)
            {
                require(artifacts::file_hash(stimulus_json) == artifacts::sha256(external_text), "external stimulus changed during simulation");
                files[std::filesystem::absolute(stimulus_json).string()] = artifacts::sha256(external_text);
            }
            Json metadata{{"status", "ORIGINAL_GATE_TRACE"}, {"frames", event_count}, {"observed", observed}, {"clocks", clocks}, {"controls", controls}, {"windows", windows},
                {"profile_length", external ? Json(nullptr) : Json(length)}, {"profiles", actual_profiles}, {"initial_discard", external ? 0 : 128},
                {"release_pad_assignment", release_json}, {"initial_reset_pad_assignment", asserted_json}, {"seed", seed}, {"files", files}, {"manual_semantic_inputs", Json::array()},
                {"scope", "Original-gate simulation; generic physical pad stimuli, discovered primary clocks/resets, X/Z retained; no internal force or protocol model."}};
            if (external)
            {
                metadata["seed"] = nullptr;
                metadata["stimulus_kind"] = "PHYSICAL_PAD_STIMULUS_V1";
                metadata["stimulus_json"] = std::filesystem::absolute(stimulus_json).string();
                metadata["stimulus_description"] = external_input.at("description");
                metadata["manual_semantic_inputs"] = {"externally specified physical-pad protocol and training windows; no internal state or word hints"};
                metadata["scope"] = "Original-gate simulation under a finite externally specified physical-pad protocol; clocks and resets driven exactly as supplied, with data-pad then clock-pad event ordering. X/Z retained, no internal force. Protocol legality is not proved; windows select training only and do not exclude events from replay.";
            }
            artifacts::write(out / "native_trace.json", metadata.dump(2) + '\n');
            return OK(Observations::load(out));
        }
        catch (const std::exception& error) { return ERR(error.what()); }
    }
}
