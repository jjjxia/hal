// Reproduce the constant-cone crash encountered when enabling HAL's full MI
// configuration, and preserve control proposals for nonconstant cones.
#include "algorithm_recovery/frontend.h"
#include "module_identification/candidates/base_candidate.h"
#include "module_identification/candidates/functional_candidate.h"
#include "module_identification/types/candidate_types.h"
#include "hal_core/netlist/netlist.h"
#include <fstream>
#include <iostream>

using namespace hal;
using namespace hal::algorithm_recovery;
namespace mi = hal::module_identification;
namespace fs = std::filesystem;
using Json = nlohmann::json;
static void check(bool value, const char* why) { if (!value) throw std::runtime_error(why); }
template<class T> static T unwrap(hal::Result<T>&& value)
{ if (value.is_error()) throw std::runtime_error(value.get_error().get()); return std::move(value.get()); }
int main(int argc, char** argv)
{
    if (argc != 3) return 2;
    try
    {
        const fs::path out = fs::absolute(argv[2]); fs::create_directories(out);
        for (const auto& [name, init] : std::map<std::string, std::string>{{"constant", "0000000000000000"}, {"buffer", "1010101010101010"}, {"and", "1000100010001000"}})
        {
            Json module{{"ports", {{"A", {{"direction", "input"}, {"bits", {2}}}}, {"B", {{"direction", "input"}, {"bits", {3}}}}, {"Q", {{"direction", "output"}, {"bits", {4}}}}}}, {"cells", Json::object()}};
            module["cells"]["lut"] = {{"type", "SB_LUT4"}, {"parameters", {{"LUT_INIT", init}}},
                {"connections", {{"I0", {2}}, {"I1", {3}}, {"I2", {"0"}}, {"I3", {"0"}}, {"O", {4}}}}};
            const auto source = out / (name + ".json"), destination = out / name; fs::remove_all(destination);
            { std::ofstream file(source); file << Json{{"modules", {{"recovered", module}}}}.dump(); check(bool(file), "cannot save MI fixture"); }
            auto imported = unwrap(import_recovered(source, argv[1], destination));
            std::vector<Gate*> gates;
            for (auto* gate : imported.netlist->get_gates()) if (gate->get_type()->get_name() == "SB_LUT4") gates.push_back(gate);
            check(gates.size() == 1, "missing fixture LUT"); mi::BaseCandidate base(gates); mi::StructuralCandidate structural(&base, gates);
            mi::FunctionalCandidate candidate(&structural, 3, mi::CandidateType::less_equal);
            check(candidate.m_output_nets.size() == 1, "fixture output is not visible");
            const auto influences = unwrap(structural.ctx.get_boolean_influence(candidate.m_output_nets.front(), {}));
            check(influences.size() == (name == "constant" ? 0 : name == "buffer" ? 1 : 2), "unexpected real Boolean influence support");
            const auto proposals = unwrap(mi::FunctionalCandidate::find_control_signals(structural.ctx, candidate));
            check(proposals.size() == (name == "constant" ? 1 : 2), "control proposal inventory changed");
            check(proposals.front().m_control_signals.empty(), "unconditioned candidate was lost");
            if (name != "constant") check(proposals.back().m_control_signals.size() == 1, "nonconstant control proposal missing");
            candidate.m_max_control_signals = 0;
            check(unwrap(mi::FunctionalCandidate::find_control_signals(structural.ctx, candidate)).size() == 1, "disabled controls were introduced");
            candidate.m_max_control_signals = 3; candidate.m_candidate_type = mi::CandidateType::counter; candidate.m_max_operands = 1;
            candidate.m_influence_count_to_input_nets = {{1, {candidate.m_input_nets.front()}}};
            check(!unwrap(mi::FunctionalCandidate::identify_control_signals(structural.ctx, candidate)).empty(), "single influence bin was not handled");
        }
        std::cout << "PASS actual constant/buffer/AND cones: empty influence, positive/disabled control proposals, single-bin arithmetic controls\n";
    }
    catch (const std::exception& error) { std::cerr << error.what() << '\n'; return 1; }
}
