#include "structured_schedule.h"
#include "artifacts.h"
#include <algorithm>
#include <map>
#include <set>
#include <sstream>
#include <vector>

namespace hal::algorithm_recovery
{
    namespace
    {
        using Json = nlohmann::json;
        using artifacts::require;
        struct Rule { Json operation; std::set<unsigned> phases; };
        struct Rules
        {
            std::vector<Rule> rules;
            std::vector<std::vector<unsigned>> phases;
            std::vector<unsigned> order;
            bool acyclic = true;
        };
        Json semantic(const Json& row)
        {
            Json result{{"name", row.at("name")}, {"width", row.at("width")}, {"kernel", row.at("kernel")},
                        {"sources", row.at("sources")}};
            if (row.contains("port")) result["port"] = row.at("port");
            return result;
        }
        Json normalize(const Json& row, unsigned period)
        {
            auto result = semantic(row); const uint64_t phase = row.at("phase");
            for (auto& source : result["sources"])
            {
                const uint64_t previous = source.at("phase"), delay = source.at("delay");
                require(previous < period && delay <= (UINT64_MAX - phase) / period, "invalid/overflowing source time");
                require(phase + period * delay >= previous, "forward structured source");
                source["lag"] = phase + period * delay - previous;
                if (source.at("kind") != "signal") require(source.at("lag") == 0, "noninstantaneous boundary/RAM source");
                source.erase("phase"); source.erase("delay");
            }
            return result;
        }
        Json instantiate(const Json& operation, unsigned phase, unsigned period)
        {
            auto result = operation;
            for (auto& source : result["sources"])
            {
                const uint64_t lag = source.at("lag"); uint64_t delay = 0, previous = 0;
                if (lag <= phase) previous = phase - lag;
                else
                {
                    // Avoid lag+period overflow; ceil((lag-phase)/period).
                    const uint64_t delta = lag - phase; delay = delta / period + (delta % period != 0);
                    previous = delta % period ? period - delta % period : 0;
                }
                source["phase"] = previous; source["delay"] = delay; source.erase("lag");
            }
            return result;
        }
        Rules recover(const Json& nodes, unsigned period)
        {
            Rules result; result.phases.resize(period); std::map<std::string, unsigned> ids;
            for (const auto& row : nodes)
            {
                const unsigned phase = row.at("phase"); require(phase < period, "schedule phase out of bounds");
                const auto operation = normalize(row, period); const auto key = operation.dump(); unsigned id;
                if (ids.count(key)) id = ids.at(key);
                else { id = result.rules.size(); ids.emplace(key, id); result.rules.push_back({operation, {}}); }
                require(result.rules.at(id).phases.insert(phase).second, "duplicate structured operation at a phase");
                result.phases.at(phase).push_back(id);
                require(instantiate(operation, phase, period) == semantic(row), "normalized temporal binding changed");
            }
            std::vector<std::set<unsigned>> edges(result.rules.size()); std::vector<unsigned> degree(result.rules.size());
            for (const auto& sequence : result.phases)
                for (size_t i = 1; i < sequence.size(); ++i)
                    if (edges[sequence[i - 1]].insert(sequence[i]).second) ++degree[sequence[i]];
            std::set<unsigned> ready;
            for (unsigned i = 0; i < degree.size(); ++i) if (!degree[i]) ready.insert(i);
            while (!ready.empty())
            {
                const auto id = *ready.begin(); ready.erase(ready.begin()); result.order.push_back(id);
                for (const auto successor : edges[id]) if (!--degree[successor]) ready.insert(successor);
            }
            result.acyclic = result.order.size() == result.rules.size();
            if (result.acyclic)
                for (unsigned phase = 0; phase < period; ++phase)
                {
                    std::vector<unsigned> expanded;
                    for (const auto id : result.order) if (result.rules[id].phases.count(phase)) expanded.push_back(id);
                    require(expanded == result.phases[phase], "structured ordering changed an original event");
                }
            return result;
        }
        std::string quoted(const Json& value)
        {
            const auto name = value.get<std::string>();
            require(!name.empty() && name.find_first_not_of("abcdefghijklmnopqrstuvwxyzABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789_:") == std::string::npos,
                    "unsupported generated schedule identifier");
            return Json(name).dump();
        }
        Json guard(const std::set<unsigned>& phases, unsigned period)
        {
            require(!phases.empty(), "empty update rule"); unsigned local = period;
            for (unsigned candidate = 1; candidate <= period; ++candidate)
            {
                if (period % candidate) continue; bool matches = true;
                for (unsigned phase = 0; phase < period && matches; ++phase)
                    matches = bool(phases.count(phase)) == bool(phases.count(phase % candidate));
                if (matches) { local = candidate; break; }
            }
            Json residues = Json::array();
            for (unsigned phase = 0; phase < local; ++phase) if (phases.count(phase)) residues.push_back(phase);
            return {{"period", local}, {"residues", residues}};
        }
        std::string condition(const Json& guard)
        {
            const unsigned period = guard.at("period"); if (period == 1) return "true";
            std::ostringstream out; bool first = true;
            for (const auto& residue : guard.at("residues"))
            { if (!first) out << " || "; first = false; out << "phase % " << period << " == " << residue; }
            return out.str();
        }
        std::string call(const Json& operation, unsigned period)
        {
            std::ostringstream out; out << "kernel_" << operation.at("kernel") << "({"; bool first = true;
            for (const auto& source : operation.at("sources"))
            {
                if (!first) out << ", "; first = false;
                const std::string kind = source.at("kind"); const auto name = quoted(source.at("name"));
                if (kind == "signal")
                    out << "schedule_signal(state, history, " << name << ", phase, " << period << ", " << source.at("lag") << "ULL, " << source.at("width") << ')';
                else if (kind == "input") out << "schedule_input(inputs, " << name << ", phase, " << source.at("width") << ')';
                else { require(kind == "ram" && source.at("width") == 16, "invalid structured RAM read"); out << "Integer(state.memories.at(" << name << ").read)"; }
            }
            return out.str() + "})";
        }
        std::string statement(const Json& operation, unsigned period)
        {
            const auto name = quoted(operation.at("name")), expression = call(operation, period);
            if (operation.contains("port"))
                return "ports[" + name + "][" + quoted(operation.at("port")) + "] = " + expression + ".convert_to<unsigned>();";
            return "schedule_write(state, " + name + ", phase, " + expression + ");";
        }
        void emit(std::ostringstream& out, const Rules& rules, unsigned period)
        {
            if (rules.acyclic)
            {
                for (const auto id : rules.order)
                {
                    const auto& rule = rules.rules[id]; const auto predicate = guard(rule.phases, period);
                    out << "        if (" << condition(predicate) << ")\n            " << statement(rule.operation, period) << '\n';
                }
            }
            else
            {
                // A union of orders can cycle even when every concrete phase is
                // valid. Preserve each original sequence instead of guessing a
                // globally valid reordering of mutually exclusive transitions.
                out << "        switch (phase) {\n";
                for (unsigned phase = 0; phase < period; ++phase)
                {
                    out << "        case " << phase << ":\n";
                    for (const auto id : rules.phases[phase]) out << "            " << statement(rules.rules[id].operation, period) << '\n';
                    out << "            break;\n";
                }
                out << "        default: throw std::runtime_error(\"phase outside recovered schedule\");\n        }\n";
            }
        }
        Json describe(const Rules& rules, unsigned period)
        {
            Json result = Json::array();
            for (const auto& rule : rules.rules)
                result.push_back({{"operation", rule.operation}, {"phases", rule.phases}, {"guard", guard(rule.phases, period)}});
            return {{"rules", result}, {"order", rules.order}, {"mode", rules.acyclic ? "REPEATED_UPDATE_RULES" : "EXACT_PHASE_DISPATCH"}};
        }
    }
    Json export_structured_schedule(const Json& model, const std::filesystem::path& output)
    {
        const unsigned period = model.at("period"); require(period > 0, "empty structured schedule");
        const auto words = recover(model.at("nodes"), period), ports = recover(model.at("ram_nodes"), period);
        std::ostringstream cpp;
        cpp << "// Recovered repeated state updates. Phase is the observed execution schedule,\n"
               "// not an inferred original FSM encoding. See schedule.json and the inherited control contract.\n"
               "void execute_recovered_schedule(Frame& state, const InputValues& inputs, const HistoryValues& history)\n{\n"
            << "    for (unsigned phase = 0; phase < " << period << "; ++phase) {\n";
        emit(cpp, words, period);
        cpp << "        std::map<std::string, std::map<std::string, unsigned>> ports;\n";
        emit(cpp, ports, period);
        cpp << "        Json reads = Json::object();\n";
        for (auto memory = model.at("memories").begin(); memory != model.at("memories").end(); ++memory)
            cpp << "        schedule_memory(state, inputs, phase, " << quoted(Json(memory.key())) << ", "
                << memory.value().at("read_clock") << ", " << memory.value().at("write_clock") << ", ports.at("
                << quoted(Json(memory.key())) << "), reads);\n";
        cpp << "        state.reads.push_back(std::move(reads));\n    }\n}\n";
        const auto source = cpp.str(); artifacts::write(output / "recovered_schedule.h", source);
        require(artifacts::read(output / "recovered_schedule.h") == source, "structured source write differs");
        Json report{{"status", "EXACT_SCHEDULE_EXPANSION_CHECKED"}, {"period", period}, {"origin", model.at("origin")},
            {"original_ff_nodes", model.at("nodes").size()}, {"original_ram_nodes", model.at("ram_nodes").size()},
            {"ff_update_rules", words.rules.size()}, {"ram_update_rules", ports.rules.size()},
            {"words", describe(words, period)}, {"ram_ports", describe(ports, period)},
            {"source_sha256", artifacts::sha256(source)}, {"model_sha256", artifacts::sha256(model.dump())},
            {"scope", "Every rule/source time binding expands exactly to the original ordered operations. Emitted schedule code and runtime helpers are trusted/tested by compiled differential replay; this is not an unrestricted original-FSM reconstruction."}};
        artifacts::write(output / "schedule.json", report.dump(2) + '\n'); return report;
    }
}
