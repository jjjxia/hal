#include "algorithm_recovery/program.h"
#include "algorithm_recovery/evaluation.h"
#include "artifacts.h"
#include "process.h"
#include <iostream>
#include <random>

namespace hal::algorithm_recovery
{
    Result<nlohmann::json> replay_resynthesis(const std::filesystem::path& original,
        const std::filesystem::path& synthesized, const std::filesystem::path& output,
        unsigned frames, const std::string& compiler, unsigned process_timeout_ms)
    {
        try
        {
            using Json = nlohmann::json; using artifacts::require;
            require(frames >= 2 && process_timeout_ms > 0, "resynthesis replay needs multiple frames and a finite process timeout");
            const auto out = std::filesystem::absolute(output);
            require(!std::filesystem::exists(out) || std::filesystem::is_empty(out), "resynthesis replay directory must be empty");
            std::filesystem::create_directories(out);
            auto before = check_program_artifacts(original), after = check_program_artifacts(synthesized);
            if (before.is_error()) return ERR(before.get_error().get()); if (after.is_error()) return ERR(after.get_error().get());
            const auto& reference = before.get(); const auto& candidate = after.get();
            require(reference.value("execution_kind", std::string("OBSERVED_PERIODIC")) == candidate.value("execution_kind", std::string("OBSERVED_PERIODIC")),
                    "resynthesis changed execution semantics");
            for (const auto* field : {"period", "origin", "memories", "inputs", "histories", "excluded"})
                require(reference.at(field) == candidate.at(field), std::string("resynthesis changed ") + field);
            for (const auto* collection : {"nodes", "ram_nodes"})
            {
                require(reference.at(collection).size() == candidate.at(collection).size(), "resynthesis changed operation inventory");
                for (size_t i = 0; i < reference.at(collection).size(); ++i)
                {
                    const auto& a = reference.at(collection)[i]; const auto& b = candidate.at(collection)[i];
                    for (const auto* field : {"name", "phase", "width", "sources"}) require(a.at(field) == b.at(field), "resynthesis changed a source/target binding");
                    if (a.contains("port")) require(a.at("port") == b.at("port"), "resynthesis changed a RAM port");
                    else require(a.at("numeric_order_known") == b.at("numeric_order_known"), "resynthesis changed numeric-order provenance");
                }
            }
            std::map<std::string, unsigned> widths;
            for (const auto& node : reference.at("nodes"))
            {
                const auto [it, added] = widths.emplace(node.at("name"), node.at("width"));
                require(added || it->second == node.at("width"), "inconsistent reference word width");
            }
            std::mt19937_64 rng(20261101);
            const auto value = [&](unsigned width, unsigned frame) {
                Integer result = 0;
                if (frame == 1) result = (Integer(1) << width) - 1;
                else if (frame != 0)
                {
                    for (unsigned bit = 0; bit < width; bit += 64) result |= Integer(rng()) << bit;
                    result &= (Integer(1) << width) - 1;
                }
                return result.str();
            };
            const auto compile = [&](const std::filesystem::path& program, const std::string& name) {
                const std::vector<std::string> command{compiler, "-std=c++17", "-O0", std::filesystem::absolute(program / "recovered.cpp").string(), "-o", (out / name).string()};
                process::run(command, out / (name + "_compile.log"), process_timeout_ms); return command;
            };
            // Compile and execute reference and candidate sequentially. Never
            // run two firmware executables concurrently merely for comparison.
            const auto reference_command = compile(original, "reference");
            require(!check_program_artifacts(original).is_error(), "reference artifacts changed during compilation");
            std::vector<Json> requests, expected; Json initial, records = Json::array();
            {
                process::JsonProcess executable({(out / "reference").string()}, out / "reference_stderr.log", process_timeout_ms);
                require(executable.request({{"command", "metadata"}}) == reference, "compiled reference metadata mismatch");
                initial = executable.request({{"command", "initial"}});
                Json history = Json::array(), memories = initial.at("memories");
                for (const auto& source : reference.at("histories"))
                    history.push_back({source[0], source[1], source[2], value(widths.at(source[0].get<std::string>()), 0)});
                for (unsigned frame = 0; frame < frames; ++frame)
                {
                    Json inputs = Json::array();
                    for (const auto& source : reference.at("inputs")) inputs.push_back({source[0], source[1], value(source[2], frame)});
                    Json request{{"command", "run"}, {"inputs", inputs}, {"history", history}, {"memories", memories}};
                    const auto response = executable.request(request);
                    history = response.at("history"); memories = response.at("memories");
                    records.push_back({{"frame", frame}, {"request_sha256", artifacts::sha256(request.dump())}, {"response_sha256", artifacts::sha256(response.dump())}});
                    requests.push_back(std::move(request)); expected.push_back(response);
                }
                executable.finish();
            }
            const auto candidate_command = compile(synthesized, "synthesized");
            require(!check_program_artifacts(synthesized).is_error(), "synthesized artifacts changed during compilation");
            {
                process::JsonProcess executable({(out / "synthesized").string()}, out / "synthesized_stderr.log", process_timeout_ms);
                require(executable.request({{"command", "metadata"}}) == candidate, "compiled synthesized metadata mismatch");
                require(executable.request({{"command", "initial"}}) == initial, "compiled synthesized RAM INIT mismatch");
                for (unsigned frame = 0; frame < frames; ++frame)
                {
                    const auto response = executable.request(requests.at(frame));
                    if (response != expected.at(frame))
                    {
                        artifacts::write(out / "failed_request.json", requests.at(frame).dump());
                        artifacts::write(out / "failed_expected.json", expected.at(frame).dump());
                        artifacts::write(out / "failed_actual.json", response.dump());
                        throw std::runtime_error("compiled resynthesis differs from original program at frame " + std::to_string(frame));
                    }
                }
                executable.finish();
            }
            Json report{{"status", "COMPILED_RESYNTHESIS_DIFFERENTIAL_PASS"}, {"frames", frames}, {"seed", 20261101},
                {"static_binding_conservation_checked", true}, {"records", records},
                {"reference_manifest_sha256", artifacts::file_hash(original / "program.json")},
                {"synthesized_manifest_sha256", artifacts::file_hash(synthesized / "program.json")},
                {"reference_executable_sha256", artifacts::file_hash(out / "reference")},
                {"synthesized_executable_sha256", artifacts::file_hash(out / "synthesized")},
                {"reference_compile_command", reference_command}, {"synthesized_compile_command", candidate_command},
                {"scope", "Compiled consecutive frames under zero/all-one/random boundary values, with complete FF values, history and RAM states/read traces compared to the original saved program. Inherits its control/schedule contract; not a fresh original-gate trace or whole-device proof."}};
            artifacts::write(out / "replay.json", report.dump(2) + '\n');
            std::cout << "RESYNTHESIS_REPLAY_PASS " << frames << " complete frames\n"; return OK(std::move(report));
        }
        catch (const std::exception& error) { return ERR(error.what()); }
    }
}
