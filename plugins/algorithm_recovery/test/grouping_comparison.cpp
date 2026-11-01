// Compare expanded HAL results in physical Q-net coordinates. Module labels
// and unresolved enumeration order are not evidence of numeric significance.
#include "../src/artifacts.h"
#include <nlohmann/json.hpp>
#include <algorithm>
#include <iostream>
#include <map>
#include <set>
#include <vector>

using namespace hal::algorithm_recovery;
using Json = nlohmann::json;
using Bus = std::vector<unsigned>;
using artifacts::require;
static Json read(const std::filesystem::path& path) { return Json::parse(artifacts::read(path)); }
struct Snapshot
{
    std::map<Bus, Json> groups;
    std::map<unsigned, std::pair<unsigned, unsigned>> wiring;
    unsigned multibit_ordered = 0, multibit_bits = 0, unresolved = 0, unresolved_bits = 0;
};
static Snapshot snapshot(const Json& report)
{
    require(report.at("status") == "HAL_DANA_BITORDER_EXTRACTED" && report.at("manual_semantic_inputs").empty(), "unexpected group source/manual inputs");
    Snapshot result;
    for (auto it = report.at("words").begin(); it != report.at("words").end(); ++it)
    {
        auto row = it.value(); const Bus q = row.at("q_nets"), d = row.at("d_nets"), gates = row.at("gates");
        require(!q.empty() && q.size() == d.size() && q.size() == gates.size() && row.at("width") == q.size(), "bad group dimensions");
        for (size_t i = 0; i < q.size(); ++i) require(result.wiring.emplace(q[i], std::make_pair(d[i], gates[i])).second, "duplicate physical FF output");
        auto key = q; std::sort(key.begin(), key.end()); row["group_name"] = it.key();
        require(result.groups.emplace(key, row).second, "duplicate physical group");
        if (row.at("order_origin") == "UNRESOLVED") { ++result.unresolved; result.unresolved_bits += q.size(); }
        else if (q.size() > 1) { ++result.multibit_ordered; result.multibit_bits += q.size(); }
    }
    require(report.at("groups") == result.groups.size() && report.at("FF_bits") == result.wiring.size(), "reported FF/group inventory differs");
    return result;
}
int main(int argc, char** argv)
{
    if (argc != 4) { std::cerr << "Usage: check-recovery-grouping-comparison old_groups.json new_groups.json output.json\n"; return 2; }
    try
    {
        const auto old = snapshot(read(argv[1])), now = snapshot(read(argv[2]));
        require(old.wiring == now.wiring && old.groups.size() == now.groups.size(), "physical DANA grouping/wiring changed; cannot isolate configuration effect");
        Json gained = Json::array(), lost = Json::array(), changed = Json::array(); unsigned gained_bits = 0, preserved = 0;
        for (const auto& [key, previous] : old.groups)
        {
            require(now.groups.count(key), "physical DANA membership changed"); const auto& current = now.groups.at(key);
            const bool a = previous.at("order_origin") != "UNRESOLVED", b = current.at("order_origin") != "UNRESOLVED";
            const Json detail{{"old_group", previous.at("group_name")}, {"new_group", current.at("group_name")}, {"width", key.size()},
                {"old_origin", previous.at("order_origin")}, {"new_origin", current.at("order_origin")}, {"old_q", previous.at("q_nets")}, {"new_q", current.at("q_nets")}};
            if (!a && b) { gained.push_back(detail); gained_bits += key.size(); }
            if (a && !b) lost.push_back(detail);
            if (a && b)
            { if (previous.at("q_nets") != current.at("q_nets")) changed.push_back(detail); else ++preserved; }
        }
        Json result{{"status", lost.empty() && changed.empty() ? "EXISTING_ORDERS_PRESERVED" : "ORDER_REGRESSION_OR_CONFLICT"},
            {"old_sha256", artifacts::file_hash(argv[1])}, {"new_sha256", artifacts::file_hash(argv[2])},
            {"groups", now.groups.size()}, {"FF_bits", now.wiring.size()}, {"preserved_known_groups_including_singletons", preserved},
            {"old_multibit_ordered", old.multibit_ordered}, {"new_multibit_ordered", now.multibit_ordered}, {"new_multibit_ordered_bits", now.multibit_bits},
            {"old_unresolved", old.unresolved}, {"new_unresolved", now.unresolved}, {"new_unresolved_bits", now.unresolved_bits},
            {"gained_groups", gained.size()}, {"gained_bits", gained_bits}, {"gained", gained}, {"lost", lost}, {"changed", changed}};
        artifacts::write(argv[3], result.dump(2) + '\n');
        std::cout << result.at("status") << ": " << old.multibit_ordered << " -> " << now.multibit_ordered << " ordered multi-bit groups; "
            << gained.size() << " gained, " << lost.size() << " lost, " << changed.size() << " changed\n";
        return lost.empty() && changed.empty() ? 0 : 1;
    }
    catch (const std::exception& error) { std::cerr << error.what() << '\n'; return 1; }
}
