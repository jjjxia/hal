#include "algorithm_recovery/state_machine.h"
#include "artifacts.h"
#include "process.h"
#include <algorithm>
#include <iostream>

namespace hal::algorithm_recovery
{
    namespace
    {
        using Json = nlohmann::json;
        using artifacts::require;
        template<class T> T unwrap(hal::Result<T>&& value)
        { if (value.is_error()) throw std::runtime_error(value.get_error().get()); return std::move(value.get()); }
        Integer decimal(const Json& json)
        {
            require(json.is_string(), "state value is not a decimal string"); const auto text = json.get<std::string>();
            require(!text.empty() && text.find_first_not_of("0123456789") == std::string::npos, "invalid decimal state value");
            Integer value = 0; for (char c : text) { value *= 10; value += c - '0'; } return value;
        }
    }
    Result<Json> check_state_artifacts(const std::filesystem::path& directory)
    {
        try
        {
            const auto manifest = Json::parse(artifacts::read(directory / "state_program.json"));
            require(manifest.at("status") == "GUARDED_STATE_PROGRAM_EXPORTED" && manifest.at("execution_kind") == "GUARDED_EVENT_TRANSITIONS", "unexpected state program stage");
            for (auto file = manifest.at("files").begin(); file != manifest.at("files").end(); ++file)
            {
                require(std::filesystem::path(file.key()).filename() == file.key(), "nonlocal state artifact");
                require(artifacts::file_hash(directory / file.key()) == file.value(), "state artifact identity changed: " + file.key());
            }
            for (const auto* file : {"state_machine.cpp", "state_schema.h", "state_schema.json", "arithmetic_kernels.h", "exact_data_kernels.h", "control_kernels.h", "recovery_state.h", "recovery_bits.h", "json.hpp"})
                require(manifest.at("files").contains(file), "unbound state source");
            for (const auto& directory_name : {std::string("data"), std::string("controls")})
            {
                require(artifacts::file_hash(directory / directory_name / "program.json") == manifest.at(directory_name == "data" ? "data_manifest_sha256" : "control_manifest_sha256"), "state expression manifest changed");
                const auto model = unwrap(check_program_artifacts(directory / directory_name));
                require(model.at("execution_kind") == "COMBINATIONAL_FUNCTIONS", "event function is not combinational");
            }
            for (const auto& copy : manifest.at("copied_kernels"))
            {
                const std::string file = copy.at("file"), origin = copy.at("origin");
                require(manifest.at("files").contains(file) && (origin == "data" || origin == "controls"), "unknown copied kernel origin");
                const auto original = Json::parse(artifacts::read(directory / origin / "program.json"));
                const auto& record = original.at("kernels").at(copy.at("kernel").get<size_t>());
                const auto saved = artifacts::read(directory / file);
                require(copy.at("sha256") == record.at("sha256") && artifacts::sha256(saved.substr(copy.at("offset").get<size_t>(), copy.at("length").get<size_t>())) == record.at("sha256"), "copied state kernel differs from proved bytes");
            }
            const auto schema = Json::parse(artifacts::read(directory / "state_schema.json"));
            require(artifacts::file_hash(schema.at("source_json").get<std::string>()) == schema.at("source_sha256"), "original state source changed");
            for (const auto& proof : schema.at("proofs"))
                require(proof.at("result") == "UNSAT" && artifacts::file_hash(proof.at("query").get<std::string>()) == proof.at("sha256"), "state cone proof identity changed");
            return OK(schema);
        }
        catch (const std::exception& error) { return ERR(error.what()); }
    }

    Result<Json> replay_state_machine(const std::filesystem::path& program, const Observations& trace,
        const std::filesystem::path& trace_directory, const std::filesystem::path& output,
        const std::string& compiler, unsigned timeout)
    {
        try
        {
            const auto schema = unwrap(check_state_artifacts(program));
            require(trace.metadata.at("status") == "ORIGINAL_GATE_TRACE" && trace.metadata.at("files").at(schema.at("source_json").get<std::string>()) == schema.at("source_sha256"), "state/trace source mismatch");
            const auto reloaded = Observations::load(trace_directory);
            require(reloaded.pre == trace.pre && reloaded.post == trace.post && reloaded.metadata == trace.metadata, "state replay observations differ from saved trace");
            const auto out = std::filesystem::absolute(output);
            require(!std::filesystem::exists(out) || std::filesystem::is_empty(out), "state replay directory must be empty"); std::filesystem::create_directories(out);
            const auto stimulus_path = std::filesystem::absolute(trace_directory / "native_stimulus.hex");
            require(trace.metadata.at("files").at(stimulus_path.string()) == artifacts::file_hash(stimulus_path), "unbound native stimulus");
            std::vector<PhysicalBit> clocks = trace.metadata.at("clocks").get<std::vector<PhysicalBit>>(), pads;
            const std::set<PhysicalBit> clock_set(clocks.begin(), clocks.end());
            std::set<PhysicalBit> input_set;
            for (const auto& pad : schema.at("pads")) input_set.insert(pad.at("physical").get<PhysicalBit>());
            for (auto bit : input_set) if (!clock_set.count(bit)) pads.push_back(bit);
            for (auto bit : clocks) require(input_set.count(bit), "native stimulus has non-primary clock");
            auto stimulus_order = pads; stimulus_order.insert(stimulus_order.end(), clocks.begin(), clocks.end());
            std::ifstream stream(stimulus_path); std::vector<Integer> stimuli; std::string line;
            while (stream >> line)
            {
                require(!line.empty() && line.find_first_not_of("0123456789abcdefABCDEF") == std::string::npos, "invalid stimulus hex");
                Integer value("0x" + line); require(value < (Integer(1) << stimulus_order.size()), "oversized stimulus word"); stimuli.push_back(value);
            }
            require(stimuli.size() == trace.pre.size(), "stimulus event count mismatch");
            const std::vector<std::string> command{compiler, "-std=c++17", "-O0", std::filesystem::absolute(program / "state_machine.cpp").string(), "-o", (out / "state_machine").string()};
            std::cout << "STATE_COMPILE " << program << std::endl;
            process::run(command, out / "compile.log", timeout);
            require(unwrap(check_state_artifacts(program)) == schema, "state program changed during compilation");
            process::JsonProcess executable({(out / "state_machine").string()}, out / "execute.log", timeout);
            require(executable.request({{"command", "metadata"}}) == schema, "compiled state schema differs");
            const auto initial = executable.request({{"command", "initial"}}); auto state = initial.at("state");
            Json external = Json::object(); for (auto bit : input_set) external["pad" + std::to_string(bit)] = "0";
            std::map<std::string, uint64_t> counts;
            const unsigned discard = trace.metadata.value("initial_discard", 0u); unsigned max_delta = 0;
            const auto compare = [&](const Integer& value, const std::vector<PhysicalBit>& bits, size_t t, bool after, const std::string& kind, const std::string& name) {
                require(value >= 0 && value < (Integer(1) << bits.size()), "state result outside width");
                for (unsigned i = 0; i < bits.size(); ++i)
                {
                    const int expected = trace.value(bits[i], t, after);
                    if (expected < 0) { ++counts["unknown_" + kind + "_bits"]; continue; }
                    ++counts["known_" + kind + "_bits"];
                    require(boost::multiprecision::bit_test(value, i) == bool(expected), "state original-gate mismatch " + name + " bit=" + std::to_string(i) + " event=" + std::to_string(t) + (after ? " post" : " pre"));
                }
            };
            for (size_t t = 0; t < stimuli.size(); ++t)
            {
                for (unsigned stage = 0; stage < 2; ++stage)
                {
                    const size_t begin = stage == 0 ? 0 : pads.size(), end = stage == 0 ? pads.size() : stimulus_order.size();
                    for (size_t i = begin; i < end; ++i) external["pad" + std::to_string(stimulus_order[i])] = boost::multiprecision::bit_test(stimuli[t], i) ? "1" : "0";
                    auto response = executable.request({{"command", "drive"}, {"state", state}, {"pads", external}}); state = std::move(response.at("state"));
                    max_delta = std::max(max_delta, response.at("delta_events").get<unsigned>()); ++counts["executed_pad_events"];
                    if (t < discard) continue; // execute warmup continuously, never seed from observations
                    for (auto word = schema.at("words").begin(); word != schema.at("words").end(); ++word)
                        compare(decimal(state.at("words").at(word.key())), word.value().at("q_bits"), t, stage == 1, "FF", word.key());
                    for (auto ram = schema.at("memories").begin(); ram != schema.at("memories").end(); ++ram)
                        compare(Integer(state.at("memories").at(ram.key()).at("read").get<unsigned>()), ram.value().at("rdata_bits"), t, stage == 1, "RAM_read", ram.key());
                    for (auto pad = schema.at("pads").begin(); pad != schema.at("pads").end(); ++pad)
                        compare(decimal(state.at("pads").at(pad.key())), {pad.value().at("physical").get<PhysicalBit>()}, t, stage == 1, "resolved_pad", pad.key());
                }
                if (t % 256 == 255) std::cout << "STATE_REPLAY " << t + 1 << '/' << stimuli.size() << std::endl;
            }
            executable.finish();
            // Map original RAM instance names by physical read bus; no design hints.
            const auto source = Json::parse(artifacts::read(schema.at("source_json").get<std::string>()));
            std::map<std::vector<PhysicalBit>, std::string> native;
            for (auto ram = schema.at("memories").begin(); ram != schema.at("memories").end(); ++ram) require(native.emplace(ram.value().at("rdata_bits").get<std::vector<PhysicalBit>>(), ram.key()).second, "aliased RAM read bus");
            std::map<std::string, std::string> names;
            const auto& cells = source.at("modules").begin().value().at("cells");
            for (auto cell = cells.begin(); cell != cells.end(); ++cell) if (cell.value().at("type") == "SB_RAM40_4K")
            { std::vector<PhysicalBit> bits; for (const auto& bit : cell.value().at("connections").at("RDATA")) bits.push_back(physical_bit(bit)); names[cell.key()] = native.at(bits); }
            const auto dump = std::filesystem::absolute(trace_directory / "native_ram_final.txt");
            require(trace.metadata.at("files").at(dump.string()) == artifacts::file_hash(dump), "RAM dump changed");
            std::istringstream rows(artifacts::read(dump)); std::string cell, bits; unsigned address; std::set<std::pair<std::string, unsigned>> seen;
            while (rows >> cell >> address >> bits)
            {
                require(names.count(cell) && address < 256 && bits.size() == 16 && bits.find_first_not_of("01xz") == std::string::npos && seen.emplace(cell, address).second, "invalid final RAM dump row");
                const unsigned actual = state.at("memories").at(names.at(cell)).at("data").at(address);
                for (unsigned i = 0; i < 16; ++i)
                {
                    const char bit = bits[15 - i]; if (bit == 'x' || bit == 'z') { ++counts["unknown_final_RAM_bits"]; continue; }
                    ++counts["known_final_RAM_bits"]; require(((actual >> i) & 1) == unsigned(bit - '0'), "state final RAM mismatch " + cell + " address=" + std::to_string(address));
                }
            }
            require(seen.size() == names.size() * 256, "incomplete final RAM dump");
            Json report{{"status", "SAVED_STATE_PROGRAM_ORIGINAL_GATE_REPLAY_PASS"}, {"events", stimuli.size()}, {"warmup_events_executed", discard}, {"counts", counts}, {"max_delta_events_observed", max_delta},
                {"initial_choices", initial}, {"compile_command", command}, {"program_manifest_sha256", artifacts::file_hash(program / "state_program.json")}, {"executable_sha256", artifacts::file_hash(out / "state_machine")},
                {"observation_files", trace.metadata.at("files")}, {"scope", "Continuous own FF/RAM execution from primitive initialization with explicit two-state X completions; external native pad stimulus only, automatic release while device drives inout. All known FF/read/pad observations after the original warmup and all known final RAM bits compared. Unknown observations counted, not proved. Finite replay, not a universal whole-device equivalence proof."}};
            artifacts::write(out / "replay.json", report.dump(2) + '\n'); return OK(report);
        }
        catch (const std::exception& error) { return ERR(error.what()); }
    }
}
