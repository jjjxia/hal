#include <nlohmann/json.hpp>
#include <filesystem>
#include <fstream>
#include <iostream>
#include <map>
#include <set>

using Json = nlohmann::json;
static Json read(const std::filesystem::path& path)
{
    std::ifstream stream(path); if (!stream) throw std::runtime_error("missing " + path.string());
    Json result; stream >> result; return result;
}
static void check(bool value, const std::string& message) { if (!value) throw std::runtime_error(message); }
int main(int argc, char** argv)
{
    if (argc != 4) { std::cerr << "Usage: check-recovery-frontend-fixture native_import_directory native_groups_directory reference_directory\n"; return 2; }
    try
    {
        const std::filesystem::path imported(argv[1]), grouped(argv[2]), original(argv[3]);
        for (const auto& file : {"mapping.json", "hal_graph.json"})
            check(read(imported / file) == read(original / file), std::string("import graph differs: ") + file);
        const auto receipt = read(imported / "prepare.json"), previous = read(original / "prepare.json");
        check(receipt.at("input_sha256") == previous.at("input_sha256") && receipt.at("library_sha256") == previous.at("library_sha256"), "input identity differs");
        check(receipt.at("counts") == read(original / "verify_import.json").at("counts"), "import verification coverage differs");
        check(read(imported / "import.hal").at("netlist").at("gates") == read(grouped / "ordered.hal").at("netlist").at("gates"),
              "grouping modified primitive gates, parameters or functions");
        std::cout << "PASS native import: exact all original cells/pins/functions and source mapping, parameters/truth-table coverage and SHA256\n";
        const auto groups = read(grouped / "groups.json"), old = read(original / "groups.json");
        for (const auto& key : {"groups", "FF_bits", "order_groups", "order_bits"}) check(groups.at(key) == old.at(key), std::string("group coverage differs: ") + key);
        auto canonical = [](const Json& words) {
            std::map<std::set<unsigned>, Json> result;
            for (const auto& word : words)
                check(result.emplace(word.at("gates").get<std::set<unsigned>>(), word).second, "duplicate group membership");
            return result;
        };
        const auto before = canonical(old.at("words")), after = canonical(groups.at("words"));
        check(before.size() == after.size(), "DANA group inventory differs");
        for (const auto& [members, word] : after)
        {
            const auto& prior = before.at(members);
            check(word.at("order_origin") == prior.at("order_origin"), "order provenance differs");
            // Compare numeric orders exactly. Unresolved lists are storage
            // coordinates and may differ by pin enumeration after re-import.
            if (word.at("order_origin") != "UNRESOLVED")
                check(word.at("q_nets") == prior.at("q_nets") && word.at("d_nets") == prior.at("d_nets"), "recovered numeric ordering differs");
            std::map<unsigned, std::pair<unsigned, unsigned>> wiring, old_wiring;
            for (unsigned i = 0; i < word.at("gates").size(); ++i) wiring.emplace(word.at("gates")[i], std::make_pair(word.at("q_nets")[i], word.at("d_nets")[i]));
            for (unsigned i = 0; i < prior.at("gates").size(); ++i) old_wiring.emplace(prior.at("gates")[i], std::make_pair(prior.at("q_nets")[i], prior.at("d_nets")[i]));
            check(wiring == old_wiring, "FF Q/D wiring differs");
        }
        auto seeds = [](const Json& all) {
            std::multiset<std::pair<std::string, Json>> result;
            for (const auto& seed : all) result.emplace(seed.at("origin"), seed.at("order"));
            return result;
        };
        check(seeds(groups.at("seeds")) == seeds(old.at("seeds")), "MI/library seed set differs");
        std::cout << "PASS native DANA/MI/bitorder: " << groups.at("groups") << " groups, " << groups.at("FF_bits")
                  << " FF bits, " << groups.at("seeds").size() << " inferred/library seeds; exact recovered orders and unresolved membership\n";
    }
    catch (const std::exception& error) { std::cerr << error.what() << '\n'; return 1; }
}
