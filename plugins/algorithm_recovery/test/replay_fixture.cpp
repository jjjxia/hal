// Legacy results are regression evidence only. Production never opens them.
#include "algorithm_recovery/program.h"
#include "../src/artifacts.h"
#include <iostream>

using namespace hal::algorithm_recovery;
using Json = nlohmann::json;
namespace fs = std::filesystem;
using artifacts::require;
static Json read(const fs::path& path) { return Json::parse(artifacts::read(path)); }
static void same_counts(const Json& old, const Json& actual)
{
    std::set<std::string> keys;
    for (auto it = old.begin(); it != old.end(); ++it) keys.insert(it.key());
    for (auto it = actual.begin(); it != actual.end(); ++it) keys.insert(it.key());
    for (const auto& key : keys) require(old.value(key, uint64_t(0)) == actual.value(key, uint64_t(0)), "replay coverage changed: " + key);
}
int main(int argc, char** argv)
{
    if (argc != 3) { std::cerr << "Usage: check-recovery-replay-fixture old_case fresh_native_analysis\n"; return 2; }
    try
    {
        const fs::path root(argv[2]); const auto old = read(fs::path(argv[1]) / "native_verified.json"), actual = read(root / "native_verified.json");
        require(actual.at("status") == "NATIVE_CPP_CHAIN_AUDITED" && actual.at("manual_semantic_inputs").empty(), "incorrect audit/manual-input status");
        for (const auto* key : {"status", "instances", "events", "RAM_read_observations_used_as_inputs"}) require(actual.at("ram").at(key) == old.at("ram").at(key), "RAM replay contract changed");
        same_counts(old.at("ram").at("counts"), actual.at("ram").at("counts"));
        require(actual.at("ram").at("RAM_read_observations_used_as_inputs") == 0, "observed RAM read oracle introduced");
        require(actual.at("ram") == read(root / "memory_replay" / "memory_replay.json"), "warmup receipt differs");
        size_t available = 0, frames = 0;
        require(actual.at("profiles").size() == old.at("profiles").size(), "profile inventory changed");
        for (auto it = old.at("profiles").begin(); it != old.at("profiles").end(); ++it)
        {
            const auto& reference = it.value(); const auto& profile = actual.at("profiles").at(it.key());
            const auto dir = root / ("replay_" + it.key()), program = root / ("program_" + it.key());
            require(profile == read(dir / "replay.json") && profile.at("status") == reference.at("status"), "profile receipt/status differs");
            if (profile.at("status") == "NO_GRAPH")
            { require(read(program / "program.json").at("status") == "NO_GRAPH" && !fs::exists(dir / "recovered"), "absent graph acquired executable"); continue; }
            ++available;
            for (const auto* key : {"period", "nodes", "mismatching_known_bits", "generated_RAM_initialization_checked", "RAM_read_inputs"})
                require(profile.at(key) == reference.at(key), "replay result/contract changed: " + std::string(key));
            same_counts(reference.at("counts"), profile.at("counts"));
            require(profile.at("minimum_training_frames") >= 3 && profile.at("minimum_withheld_frames") >= 1, "coverage threshold weakened");
            auto checked = check_program_artifacts(program); require(!checked.is_error(), "program artifacts changed");
            for (const auto& [field, path] : std::map<std::string, fs::path>{{"executable_sha256", dir / "recovered"}, {"program_manifest_sha256", program / "program.json"},
                {"contract_sha256", dir / "contract.json"}, {"frames_sha256", dir / "frames.jsonl"}, {"compiler_version_sha256", dir / "compiler_version.txt"}})
                require(profile.at(field) == artifacts::file_hash(path), "replay artifact changed: " + field);
            std::istringstream lines(artifacts::read(dir / "frames.jsonl")); std::string line; size_t training = 0, withheld = 0; std::set<unsigned> starts;
            while (std::getline(lines, line))
            {
                const auto row = Json::parse(line); require(starts.insert(row.at("start").get<unsigned>()).second, "duplicate replay frame");
                require(row.at("request_sha256").get<std::string>().size() == 64 && row.at("response_sha256").get<std::string>().size() == 64, "missing frame identity");
                if (row.at("kind") == "training") ++training;
                else { require(row.at("kind") == "withheld", "invalid frame kind"); ++withheld; }
            }
            require(training == profile.at("counts").at("training_frames") && withheld == profile.at("counts").at("withheld_frames"), "frame receipt inventory differs");
            frames += training + withheld;
            std::cout << "PASS " << it.key() << " all known/unknown counts preserved; " << training << " training, " << withheld << " withheld\n";
        }
        std::cout << "PASS " << available << " actual compiled programs, " << frames << " frames, full INIT-to-final RAM and saved artifact identities\n";
    }
    catch (const std::exception& error) { std::cerr << error.what() << '\n'; return 1; }
}
