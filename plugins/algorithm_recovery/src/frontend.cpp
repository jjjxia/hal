#include "algorithm_recovery/frontend.h"
#include "hal_core/netlist/endpoint.h"
#include "hal_core/netlist/gate.h"
#include "hal_core/netlist/net.h"
#include "hal_core/netlist/netlist_factory.h"
#include "hal_core/netlist/persistent/netlist_serializer.h"
#include "hal_core/plugin_system/plugin_manager.h"
#include <sodium.h>
#include <algorithm>
#include <fstream>
#include <regex>
#include <sstream>

namespace hal::algorithm_recovery
{
    namespace
    {
        using Json = nlohmann::json;
        using Ordered = nlohmann::ordered_json;
        void require(bool condition, const std::string& message) { if (!condition) throw std::runtime_error(message); }
        std::string read(const std::filesystem::path& path)
        {
            std::ifstream stream(path, std::ios::binary);
            require(bool(stream), "cannot read " + path.string());
            std::ostringstream text; text << stream.rdbuf();
            require(!stream.bad(), "failed reading " + path.string());
            return text.str();
        }
        void write(const std::filesystem::path& path, const std::string& text)
        {
            std::ofstream stream(path, std::ios::binary); stream << text;
            require(bool(stream), "cannot write " + path.string());
        }
        std::string digest(const std::string& text)
        {
            require(sodium_init() >= 0, "SHA256 initialization failed");
            unsigned char hash[crypto_hash_sha256_BYTES];
            crypto_hash_sha256(hash, reinterpret_cast<const unsigned char*>(text.data()), text.size());
            char hex[2 * crypto_hash_sha256_BYTES + 1];
            sodium_bin2hex(hex, sizeof(hex), hash, sizeof(hash));
            return hex;
        }
        std::string hex_parameter(const std::string& binary)
        {
            require(!binary.empty() && binary.find_first_not_of("01") == std::string::npos, "non-concrete binary parameter");
            std::string hex;
            const auto padded = std::string((4 - binary.size() % 4) % 4, '0') + binary;
            for (size_t i = 0; i < padded.size(); i += 4)
                hex += "0123456789abcdef"[std::stoul(padded.substr(i, 4), nullptr, 2)];
            const auto first = hex.find_first_not_of('0');
            return first == std::string::npos ? "0" : hex.substr(first);
        }
        std::string normalize_hex(std::string value)
        {
            require(!value.empty() && value.find_first_not_of("0123456789abcdefABCDEF") == std::string::npos, "non-concrete HAL parameter");
            std::transform(value.begin(), value.end(), value.begin(), [](unsigned char c) { return std::tolower(c); });
            const auto first = value.find_first_not_of('0');
            return first == std::string::npos ? "0" : value.substr(first);
        }
        void identifier(const std::string& value)
        {
            static const std::regex valid("[a-zA-Z_][a-zA-Z_0-9$]*");
            require(std::regex_match(value, valid), "unsupported Verilog identifier: " + value);
        }
    }

    Result<ImportedNetlist> import_recovered(const std::filesystem::path& source, const std::filesystem::path& library, const std::filesystem::path& output)
    {
        try
        {
            const auto text = read(source), library_text = read(library);
            auto input = Ordered::parse(text), lib = Ordered::parse(library_text);
            require(input.at("modules").size() == 1, "expected one flat recovered module");
            const auto& module = input.at("modules").begin().value();
            std::map<std::string, Ordered> types;
            for (const auto& cell : lib.at("cells")) types.emplace(cell.at("name"), cell);
            require(!types.count("REC_IO_DFF") && !types.count("REC_TBUF"), "library already contains recovery adapter types");
            auto ioff = types.at("SB_DFF"); ioff["name"] = "REC_IO_DFF";
            lib["cells"].push_back(ioff);
            auto tbuf = types.at("HAL_MUX"); tbuf["name"] = "REC_TBUF";
            tbuf["types"] = {"combinational", "tristate"};
            auto& groups = tbuf["pin_groups"];
            groups.erase(std::remove_if(groups.begin(), groups.end(), [](const auto& g) { return g.at("name") == "B"; }), groups.end());
            for (auto& group : groups)
                if (group.at("name") == "O")
                { group["pins"][0]["function"] = "A"; group["pins"][0]["z_function"] = "(! S)"; }
            lib["cells"].push_back(tbuf);
            // Keep the architecture name: MI dispatches using the library name.
            const auto& ports = module.at("ports");
            std::map<u32, std::string> inouts;
            std::set<u32> all_bits, source_pin_bits;
            std::map<u32, size_t> port_alias_counts;
            auto collect = [&](const auto& bits) {
                require(bits.is_array() && !bits.empty(), "empty/non-array connection");
                for (const auto& bit : bits)
                    if (bit.is_number_integer())
                    {
                        require(bit.template get<int64_t>() >= 0 && bit.template get<uint64_t>() <= std::numeric_limits<u32>::max(), "invalid physical bit ID");
                        all_bits.insert(bit.template get<u32>());
                    }
                    else require(bit == "0" || bit == "1" || bit == "x" || bit == "z", "invalid bit constant");
            };
            for (auto it = ports.begin(); it != ports.end(); ++it)
            {
                identifier(it.key());
                const auto& p = it.value();
                require(p.at("bits").size() == 1 && p.at("bits")[0].is_number_integer(), "only scalar physical pad ports supported");
                require(p.at("direction") == "input" || p.at("direction") == "output" || p.at("direction") == "inout", "invalid port direction");
                collect(p.at("bits"));
                ++port_alias_counts[p.at("bits")[0].get<u32>()];
                if (p.at("direction") == "inout")
                    require(inouts.emplace(p.at("bits")[0], it.key()).second, "duplicate bidirectional pad alias");
            }
            for (const auto& cell : module.at("cells")) for (const auto& connection : cell.at("connections"))
            {
                collect(connection);
                for (const auto& physical : connection) if (physical.is_number_integer()) source_pin_bits.insert(physical.get<u32>());
            }
            auto bit = [&](const Ordered& b) -> std::string {
                if (!b.is_number_integer()) return "1'b" + b.get<std::string>();
                const u32 id = b;
                return inouts.count(id) ? inouts.at(id) : "n" + std::to_string(id);
            };
            auto bus = [&](const Ordered& bits) {
                if (bits.size() == 1) return bit(bits[0]);
                std::string result = "{";
                for (auto it = bits.rbegin(); it != bits.rend(); ++it) { if (it != bits.rbegin()) result += ", "; result += bit(*it); }
                return result + "}";
            };
            std::ostringstream verilog;
            verilog << "module recovered(";
            for (auto it = ports.begin(); it != ports.end(); ++it) { if (it != ports.begin()) verilog << ", "; verilog << it.key(); }
            verilog << ");\n";
            for (auto it = ports.begin(); it != ports.end(); ++it) verilog << it.value().at("direction").get<std::string>() << ' ' << it.key() << ";\n";
            for (auto b : all_bits) verilog << "wire n" << b << ";\n";
            for (auto it = ports.begin(); it != ports.end(); ++it)
            {
                const auto& p = it.value();
                if (p.at("direction") == "output") verilog << "assign " << it.key() << " = " << bus(p.at("bits")) << ";\n";
                else verilog << "assign n" << p.at("bits")[0].get<u32>() << " = " << it.key() << ";\n";
            }
            Json mapping = Json::object(), expected_pins = Json::object(), expected_parameters = Json::object();
            Json input_counts = Json::object(), output_counts = Json::object();
            unsigned index = 0;
            for (auto it = module.at("cells").begin(); it != module.at("cells").end(); ++it, ++index)
            {
                const auto& cell = it.value();
                const std::string original_type = cell.at("type"), name = "g" + std::to_string(index);
                auto type = original_type; auto params = cell.at("parameters"), connections = cell.at("connections");
                if (type == "$dff")
                {
                    require(hex_parameter(params.at("WIDTH")) == "1" && hex_parameter(params.at("CLK_POLARITY")) == "1", "unsupported IO DFF width/edge");
                    type = "REC_IO_DFF"; params = Ordered::object();
                    connections = {{"C", connections.at("CLK")}, {"D", connections.at("D")}, {"Q", connections.at("Q")}};
                }
                else if (type == "$mux")
                {
                    require(hex_parameter(params.at("WIDTH")) == "1" && connections.at("A") == Ordered::array({"z"}) &&
                            connections.at("B").size() == 1 && connections.at("S").size() == 1 && connections.at("Y").size() == 1 &&
                            connections.at("B")[0] != "z", "unsupported tristate mux");
                    type = "REC_TBUF"; params = Ordered::object();
                    connections = {{"A", connections.at("B")}, {"S", connections.at("S")}, {"O", connections.at("Y")}};
                }
                else require(types.count(type), "unknown primitive " + type);
                identifier(type);
                verilog << type;
                if (!params.empty())
                {
                    verilog << " #(";
                    for (auto p = params.begin(); p != params.end(); ++p)
                    {
                        identifier(p.key()); const auto value = p.value().get<std::string>();
                        expected_parameters[name][p.key()] = hex_parameter(value);
                        if (p != params.begin()) verilog << ", ";
                        verilog << '.' << p.key() << '(' << value.size() << "'b" << value << ')';
                    }
                    verilog << ')';
                }
                verilog << ' ' << name << " (";
                for (auto c = connections.begin(); c != connections.end(); ++c)
                {
                    identifier(c.key());
                    if (c != connections.begin()) verilog << ", ";
                    verilog << '.' << c.key() << '(' << bus(c.value()) << ')';
                    for (size_t i = 0; i < c.value().size(); ++i)
                        expected_pins[name][c.value().size() == 1 ? c.key() : c.key() + "(" + std::to_string(i) + ")"] = c.value()[i];
                }
                verilog << ");\n";
                mapping[name] = {{"source_cell", it.key()}, {"source_type", original_type}, {"type", type}};
                input_counts[original_type] = input_counts.value(original_type, 0u) + 1;
                output_counts[type] = output_counts.value(type, 0u) + 1;
            }
            verilog << "endmodule\n";
            const auto out = std::filesystem::absolute(output);
            require(!std::filesystem::exists(out) || std::filesystem::is_empty(out), "import output directory must be empty");
            std::filesystem::create_directories(out);
            write(out / "library.hgl", lib.dump(2) + '\n');
            write(out / "hal_input.v", verilog.str());
            write(out / "mapping.json", mapping.dump(2) + '\n');
            write(out / "io_models.v", "module REC_IO_DFF(input C,D,output reg Q);\nalways @(posedge C) Q <= D;\nendmodule\nmodule REC_TBUF(input A,S,output O);\nassign O = S ? A : 1'bz;\nendmodule\n");
            require(plugin_manager::load("hgl_parser") && plugin_manager::load("verilog_parser"), "cannot load native parser plugins");
            auto nl = netlist_factory::load_netlist(out / "hal_input.v", out / "library.hgl");
            require(bool(nl), "HAL rejected prepared netlist");
            ImportedNetlist result;
            auto physical = [&](const Net* net) -> Json {
                require(net != nullptr, "null net in imported pin");
                if (net->is_gnd_net()) return "0";
                if (net->is_vcc_net()) return "1";
                const auto name = net->get_name();
                if (ports.contains(name)) return ports.at(name).at("bits")[0];
                require(name.size() > 1 && name[0] == 'n' && name.find_first_not_of("0123456789", 1) == std::string::npos, "unmapped physical net " + name);
                return std::stoul(name.substr(1));
            };
            std::set<Json> actual_in, actual_out, expected_in, expected_out;
            for (auto* net : nl->get_global_input_nets()) actual_in.insert(physical(net));
            for (auto* net : nl->get_global_output_nets()) actual_out.insert(physical(net));
            for (const auto& p : ports)
            {
                if (p.at("direction") != "output") expected_in.insert(Json(p.at("bits")[0]));
                if (p.at("direction") != "input") expected_out.insert(Json(p.at("bits")[0]));
            }
            // HAL's parser may discard a declared input with no endpoints.
            // Restore only that source-proven no-op port; never fabricate a
            // missing connected pin or change input/output alias semantics.
            Json restored_unused_inputs = Json::array();
            for (auto port = ports.begin(); port != ports.end(); ++port)
            {
                if (port.value().at("direction") != "input") continue;
                const auto bit = port.value().at("bits")[0].get<u32>();
                if (actual_in.count(Json(bit))) continue;
                require(!source_pin_bits.count(bit) && port_alias_counts.at(bit) == 1,
                    "cannot restore missing connected/aliased input port " + port.key() + " at physical bit " + std::to_string(bit));
                Net* retained = nullptr;
                for (auto* net : nl->get_nets()) if (physical(net) == Json(bit))
                {
                    require(retained == nullptr, "multiple retained nets for unused input " + port.key());
                    retained = net;
                }
                const bool recreated = retained == nullptr;
                if (recreated) retained = nl->create_net("n" + std::to_string(bit));
                require(retained && retained->get_sources().empty() && retained->get_destinations().empty() && !retained->is_global_output_net(),
                    "unused input has unexpected imported connectivity: " + port.key());
                require(nl->mark_global_input_net(retained), "cannot retain unused input port: " + port.key());
                restored_unused_inputs.push_back({{"port", port.key()}, {"physical_bit", bit}, {"net_id", retained->get_id()},
                    {"action", recreated ? "RECREATED_EMPTY_INPUT_NET" : "MARKED_RETAINED_EMPTY_NET"}});
            }
            actual_in.clear(); actual_out.clear();
            for (auto* net : nl->get_global_input_nets()) actual_in.insert(physical(net));
            for (auto* net : nl->get_global_output_nets()) actual_out.insert(physical(net));
            require(actual_in == expected_in && actual_out == expected_out,
                "HAL global I/O alias mismatch: actual inputs=" + Json(actual_in).dump() + " expected inputs=" + Json(expected_in).dump() +
                " actual outputs=" + Json(actual_out).dump() + " expected outputs=" + Json(expected_out).dump());
            for (auto* net : nl->get_nets()) result.physical_bits.emplace(net->get_id(), physical(net));
            std::map<std::string, unsigned> counts;
            Json graph = Json::object();
            std::set<std::string> seen;
            for (auto* gate : nl->get_gates())
            {
                const auto name = gate->get_name();
                if (!mapping.contains(name))
                { require(gate->get_type()->get_name() == "GND" || gate->get_type()->get_name() == "VCC", "unexpected imported gate"); continue; }
                require(seen.insert(name).second, "duplicate imported gate");
                require(gate->get_type()->get_name() == mapping.at(name).at("type"), "primitive type changed during import");
                Json actual = Json::object();
                auto endpoints = gate->get_fan_in_endpoints();
                const auto outputs = gate->get_fan_out_endpoints(); endpoints.insert(endpoints.end(), outputs.begin(), outputs.end());
                for (const auto* ep : endpoints) actual[ep->get_pin()->get_name()] = physical(ep->get_net());
                require(actual == expected_pins.at(name), "imported pin mismatch: " + name);
                counts["connected_pins"] += actual.size();
                if (expected_parameters.contains(name)) for (auto p = expected_parameters.at(name).begin(); p != expected_parameters.at(name).end(); ++p)
                {
                    const auto [dtype, value] = gate->get_data("generic", p.key());
                    require((dtype == "bit_vector" || (dtype == "bit_value" && (value == "0" || value == "1"))) && normalize_hex(value) == p.value(), "imported parameter mismatch: " + name + '/' + p.key());
                    // HAL tags short 0/1 literals as bit_value irrespective of
                    // their declared width. Keep the already-checked value and
                    // normalize its tag for LUT/RAM consumers.
                    if (dtype == "bit_value")
                    { gate->set_data("generic", p.key(), "bit_vector", value); ++counts["normalized_scalar_parameter_tags"]; }
                    ++counts["parameters"];
                }
                Json functions = Json::object();
                for (const auto& [port, fn] : gate->get_boolean_functions()) functions[port] = fn.to_string();
                graph[name] = {{"id", gate->get_id()}, {"type", gate->get_type()->get_name()}, {"pins", actual}, {"functions", functions}};
                const auto& cell = module.at("cells").at(mapping.at(name).at("source_cell").get<std::string>());
                const bool lut = cell.at("type") == "SB_LUT4", carry = cell.at("type") == "SB_CARRY";
                if (lut || carry)
                {
                    const auto init = lut ? std::stoul(hex_parameter(cell.at("parameters").at("LUT_INIT")), nullptr, 16) : 0;
                    for (unsigned value = 0; value < (lut ? 16u : 8u); ++value)
                    {
                        std::unordered_map<std::string, BooleanFunction::Value> env;
                        for (unsigned bit = 0; bit < (lut ? 4u : 3u); ++bit)
                            env[lut || bit < 2 ? "I" + std::to_string(bit) : "CI"] = value & (1u << bit) ? BooleanFunction::ONE : BooleanFunction::ZERO;
                        const bool expected = lut ? ((init >> value) & 1) : ((value & 1) + ((value >> 1) & 1) + ((value >> 2) & 1) >= 2);
                        const auto actual_value = gate->get_boolean_function(lut ? "O" : "CO").evaluate(env);
                        require(actual_value.is_ok() && actual_value.get() == (expected ? BooleanFunction::ONE : BooleanFunction::ZERO), "primitive truth table mismatch");
                        ++counts[lut ? "lut_assignments" : "carry_assignments"];
                    }
                }
                ++counts["cells"];
            }
            require(seen.size() == mapping.size(), "missing imported cells");
            result.receipt = {{"status", "IMPORT_CHECKED"}, {"input", std::filesystem::absolute(source).string()}, {"input_sha256", digest(text)},
                {"library", std::filesystem::absolute(library).string()}, {"library_sha256", digest(library_text)},
                {"input_cells", input_counts}, {"output_cells", output_counts}, {"counts", counts}, {"ports", Json(ports)},
                {"restored_unused_input_ports", restored_unused_inputs},
                {"manual_semantic_inputs", Json::array()}, {"scope", "All pins, parameters, global IO aliases and two-state LUT/carry truth tables; not sequential/RAM behavioral equivalence."}};
            write(out / "prepare.json", result.receipt.dump(2) + '\n');
            write(out / "verify_import.json", result.receipt.dump(2) + '\n');
            write(out / "hal_graph.json", graph.dump() + '\n');
            require(netlist_serializer::serialize_to_file(nl.get(), out / "import.hal"), "cannot save imported HAL netlist");
            result.netlist = std::move(nl);
            return OK(std::move(result));
        }
        catch (const std::exception& error) { return ERR(error.what()); }
    }
}
