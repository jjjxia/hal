#include "algorithm_recovery/grouping.h"
#include "hal_core/netlist/gate.h"
#include "hal_core/netlist/module.h"
#include "hal_core/netlist/net.h"
#include "hal_core/netlist/netlist.h"
#include "hal_core/utilities/enums.h"
#include "dataflow_analysis/api/dataflow.h"
#include "module_identification/api/configuration.h"
#include "module_identification/api/module_identification.h"
#include "module_identification/api/result.h"
#include "bitorder_propagation/bitorder_propagation.h"
#include <algorithm>
#include <iostream>
#include <regex>

namespace hal::algorithm_recovery
{
    namespace
    {
        using Json = nlohmann::json;
        template<class T> T unwrap(hal::Result<T>&& result)
        {
            if (result.is_error()) throw std::runtime_error(result.get_error().get());
            return std::move(result.get());
        }
        void require(bool condition, const std::string& why) { if (!condition) throw std::runtime_error(why); }
        template<class Container> Json ids(const Container& objects)
        {
            Json result = Json::array();
            for (const auto* object : objects) { require(object != nullptr, "null recovery endpoint"); result.push_back(object->get_id()); }
            return result;
        }
    }

    Result<GroupingResult> recover_groups(Netlist* nl, const GroupingOptions& options)
    {
        namespace mi = module_identification;
        namespace bo = bitorder_propagation;
        try
        {
            require(nl != nullptr && options.dana_threads > 0 && options.mi_threads > 0, "missing netlist or invalid thread limit");
            require(!options.types.empty(), "no module-identification candidate types selected");
            std::set<mi::CandidateType> selected; Json type_names = Json::array();
            for (auto type : options.types)
            {
                require(std::find(mi::all_checkable_candidate_types.begin(), mi::all_checkable_candidate_types.end(), type) != mi::all_checkable_candidate_types.end(),
                    "not a selectable HAL candidate type: " + enum_to_string(type) + "; subtraction is checked via addition");
                require(selected.insert(type).second, "duplicate module-identification candidate type"); type_names.push_back(enum_to_string(type));
            }
            const Json mi_settings{{"types", type_names}, {"max_control_signals", options.max_control_signals}, {"threads", options.mi_threads}};
            require(nl->get_modules().size() == 1, "group recovery requires an imported flat netlist");
            dataflow::Configuration config(nl);
            config.with_flip_flops().with_min_group_size(8);
            config.max_thread_count = options.dana_threads;
            std::vector<Gate*> rams;
            for (auto* gate : nl->get_gates()) if (gate->get_type()->get_name() == "SB_RAM40_4K") rams.push_back(gate);
            config.with_known_structures(rams);
            std::cout << "DANA_START threads=" << options.dana_threads << std::endl;
            auto dana = unwrap(dataflow::analyze(config));
            GroupingResult result;
            result.dana = {{"groups", Json::object()}, {"configuration", {{"min_group_size", 8}, {"threads", options.dana_threads},
                {"known_registers", Json::array()}, {"known_structures", "physical SB_RAM40_4K library pin groups"}}}};
            std::vector<std::vector<Gate*>> registers;
            std::set<u32> expected;
            for (const auto& [id, gates] : dana.get_groups())
            {
                std::vector<Gate*> word(gates.begin(), gates.end());
                std::sort(word.begin(), word.end(), [](auto* a, auto* b) { return a->get_id() < b->get_id(); });
                result.dana["groups"][std::to_string(id)] = ids(word);
                registers.push_back(word);
                for (auto* gate : word) require(expected.insert(gate->get_id()).second, "DANA overlap");
            }
            std::cout << "DANA_DONE groups=" << registers.size() << " FF=" << expected.size() << std::endl;
            // Run MI on the original flat graph, exactly as the former isolated
            // import.hal process, before creating any DANA/MI modules.
            mi::Configuration mi_config(nl);
            mi_config.with_max_thread_count(options.mi_threads).with_max_control_signals(options.max_control_signals)
                .with_types_to_check(options.types)
                .with_known_registers(registers);
            std::cout << "MI_START " << mi_settings.dump() << std::endl;
            auto identified = unwrap(mi::execute(mi_config));
            auto candidates = identified.get_candidates();
            result.candidates = {{"candidates", Json::object()}, {"configuration", mi_settings}, {"scope", "Native HAL MI; inferred DANA registers; only numeric operations can seed absolute bit significance"}};
            const auto numeric_seed = [](const auto& candidate) {
                // Equality and comparison with an inferred constant do not fix
                // numeric bit significance: a common permutation preserves them.
                return candidate.is_verified() && std::any_of(candidate.m_types.begin(), candidate.m_types.end(), [](auto type) {
                    return type != mi::CandidateType::equal && type != mi::CandidateType::value_check && type != mi::CandidateType::none && type != mi::CandidateType::mixed;
                });
            };
            for (const auto& [id, candidate] : candidates)
            {
                Json operands = Json::array();
                for (const auto& operand : candidate.m_operands) operands.push_back(ids(operand));
                Json types = Json::array();
                for (const auto type : candidate.m_types) types.push_back(enum_to_string(type));
                result.candidates["candidates"][std::to_string(id)] = {{"verified", candidate.is_verified()}, {"name", candidate.get_name()},
                    {"gates", ids(candidate.m_gates)}, {"base_gates", ids(candidate.m_base_gates)}, {"operands", operands},
                    {"outputs", ids(candidate.m_output_nets)}, {"controls", ids(candidate.m_control_signals)},
                    {"info", candidate.get_candidate_info()}, {"types", types},
                    {"merged_operation", candidate.get_merged_word_level_operation().to_string()}, {"numeric_order_seed_eligible", numeric_seed(candidate)}};
            }
            result.candidates["verified_count"] = identified.get_verified_candidates().size();
            result.candidates["verified_gates"] = identified.get_all_verified_gates().size();
            result.candidates["timing_stats"] = identified.get_timing_stats();
            std::cout << "MI_DONE candidates=" << candidates.size() << std::endl;
            const auto modules = unwrap(dana.create_modules());
            std::set<std::pair<Module*, PinGroup<ModulePin>*>> targets;
            struct Register { Module* module; PinGroup<ModulePin>* q; PinGroup<ModulePin>* d; };
            std::vector<Register> words;
            for (const auto& [id, module] : modules)
            {
                require(module != nullptr, "null DANA module");
                auto* q = module->get_pin_group_by_name("o_Q");
                auto* d = module->get_pin_group_by_name("i_D");
                require(q != nullptr, "DANA module has no Q pin group");
                for (auto* group : {q, d}) if (group && group->size() > 1) targets.emplace(module, group);
                words.push_back({module, q, d});
            }
            std::vector<bo::BitOrder> seeds;
            Json seed_rows = Json::array();
            auto seed = [&](Module* module, const std::string& name, std::vector<std::pair<Net*, u32>> order, PinDirection direction, const std::string& origin) {
                require(module != nullptr, "failed to create seed module");
                order.erase(std::remove_if(order.begin(), order.end(), [](const auto& pair) { return !pair.first || pair.first->is_gnd_net() || pair.first->is_vcc_net(); }), order.end());
                if (order.size() < 2) return;
                std::set<Net*> unique; std::vector<ModulePin*> pins; Json row = Json::array();
                for (const auto& [net, index] : order)
                {
                    if (!unique.insert(net).second || !module->get_pin_by_net(net)) return;
                    pins.push_back(module->get_pin_by_net(net)); row.push_back({net->get_id(), index});
                }
                auto* group = unwrap(module->create_pin_group(name, pins, direction, PinType::data, true, 0, true, true));
                seeds.emplace_back(module, group, std::move(order));
                seed_rows.push_back({{"module", module->get_name()}, {"pingroup", name}, {"origin", origin}, {"order", row}});
            };
            for (const auto& [id, candidate] : candidates)
            {
                if (!numeric_seed(candidate)) continue;
                for (const auto* gate : candidate.m_gates) require(!gate->get_type()->has_property(GateTypeProperty::ff), "MI seed contains FF");
                auto* module = nl->create_module("native_MI_" + std::to_string(id), nl->get_top_module(), candidate.m_gates);
                for (unsigned op = 0; op < candidate.m_operands.size(); ++op)
                {
                    std::vector<std::pair<Net*, u32>> order;
                    for (unsigned i = 0; i < candidate.m_operands[op].size(); ++i) order.emplace_back(candidate.m_operands[op][i], i);
                    seed(module, "operand" + std::to_string(op), std::move(order), PinDirection::input, "HAL_MI");
                }
                std::vector<std::pair<Net*, u32>> order;
                for (unsigned i = 0; i < candidate.m_output_nets.size(); ++i) order.emplace_back(candidate.m_output_nets[i], i);
                seed(module, "result", std::move(order), PinDirection::output, "HAL_MI");
            }
            const std::regex pin_index(".+\\(([0-9]+)\\)");
            for (auto* ram : rams)
            {
                auto* module = nl->create_module("native_macro_" + std::to_string(ram->get_id()), nl->get_top_module(), {ram});
                for (const auto* group : ram->get_type()->get_pin_groups())
                {
                    if (group->size() < 2) continue;
                    std::vector<std::pair<Net*, u32>> order;
                    for (const auto* pin : group->get_pins())
                    {
                        std::smatch match; const auto name = pin->get_name();
                        if (!std::regex_match(name, match, pin_index)) continue;
                        auto* net = pin->get_direction() == PinDirection::input ? ram->get_fan_in_net(pin) : ram->get_fan_out_net(pin);
                        order.emplace_back(net, std::stoul(match[1]));
                    }
                    std::sort(order.begin(), order.end(), [](const auto& a, const auto& b) { return a.second < b.second; });
                    // Different macro ports may use the same physical nets.
                    // HAL permits a module pin in only one pin group: creating
                    // a second overlapping group would move pins and delete the
                    // first group, leaving dangling BitOrder seed pointers.
                    // Nested views preserve the same single RAM instance while
                    // giving each library port its own stable boundary pins.
                    module = nl->create_module("native_macro_" + std::to_string(ram->get_id()) + '_' + group->get_name(), module, {ram});
                    seed(module, group->get_name(), std::move(order), group->get_direction(), "primitive_library_pin_index");
                }
            }
            std::cout << "BITORDER_START seeds=" << seeds.size() << " targets=" << targets.size() << std::endl;
            const auto strict = unwrap(bo::propagate_module_pingroup_bitorder(bo::BitOrderResult(seeds), targets, true));
            std::set<std::pair<Module*, PinGroup<ModulePin>*>> missing;
            for (const auto& [module, group] : targets) if (!strict.get(module, group)) missing.emplace(module, group);
            const auto orders = missing.empty() ? strict : unwrap(bo::propagate_module_pingroup_bitorder(strict, missing, false));
            for (const auto& order : strict.get_bit_orders())
            {
                const auto* after = orders.get(order.get_module(), order.get_pin_group());
                if (!after || after->get_order() != order.get_order())
                {
                    Json before_json = Json::array(), after_json = Json::array();
                    for (const auto& [net, index] : order.get_order()) before_json.push_back({net->get_id(), index});
                    if (after) for (const auto& [net, index] : after->get_order()) after_json.push_back({net->get_id(), index});
                    throw std::runtime_error("relaxed propagation changed established strict order: " + order.get_module()->get_name() + "/" + order.get_pin_group()->get_name() + " " + before_json.dump() + " -> " + after_json.dump());
                }
            }
            result.groups = {{"status", "HAL_DANA_BITORDER_EXTRACTED"}, {"words", Json::object()}, {"seeds", seed_rows},
                {"orders", Json::array()},
                {"manual_semantic_inputs", Json::array()}, {"configuration", {{"strict_first", true}, {"relaxed_fallback", true}, {"preserve_strict_orders", true}, {"module_identification", mi_settings}}}};
            std::set<u32> seen;
            std::map<std::string, unsigned> counts, bits;
            for (const auto& word : words)
            {
                std::map<Net*, Gate*> qgates;
                for (auto* gate : word.module->get_gates())
                {
                    require(gate->get_fan_out_net("Q") && gate->get_fan_in_net("D"), "DANA member lacks Q/D");
                    require(qgates.emplace(gate->get_fan_out_net("Q"), gate).second && seen.insert(gate->get_id()).second, "overlapping DANA state");
                }
                std::vector<Net*> native, qnets;
                for (const auto* pin : word.q->get_pins()) native.push_back(pin->get_net());
                require(qgates.size() == native.size(), "Q group does not cover native FFs");
                const auto* qo = orders.get(word.module, word.q);
                const auto* dorder = word.d ? orders.get(word.module, word.d) : nullptr;
                for (const auto& [port, order] : std::vector<std::pair<std::string, const bo::BitOrder*>>{{"Q", qo}, {"D", dorder}})
                {
                    Json value = nullptr;
                    if (order)
                    {
                        value = Json::array();
                        for (const auto& [net, index] : order->get_order()) value.push_back({net->get_id(), index});
                    }
                    result.groups["orders"].push_back({{"module", word.module->get_name()}, {"port", port}, {"order", value}});
                }
                std::string origin;
                if (qo)
                {
                    for (const auto& [net, index] : qo->get_order()) { require(index == qnets.size(), "noncontinuous recovered Q order"); qnets.push_back(net); }
                    origin = strict.get(word.module, word.q) ? "HAL_BITORDER_Q" : "HAL_BITORDER_Q_RELAXED";
                }
                else
                {
                    std::map<Net*, Net*> d2q;
                    for (const auto& [q, gate] : qgates) d2q.emplace(gate->get_fan_in_net("D"), q);
                    if (dorder && dorder->get_order().size() == native.size() && d2q.size() == native.size())
                    {
                        for (const auto& [d, index] : dorder->get_order()) { require(index == qnets.size(), "noncontinuous recovered D order"); qnets.push_back(d2q.at(d)); }
                        origin = "HAL_BITORDER_D_THROUGH_FF_WIRING";
                    }
                    else { qnets = native; origin = native.size() == 1 ? "SINGLE_BIT" : "UNRESOLVED"; }
                }
                require(qnets.size() == native.size() && std::set<Net*>(qnets.begin(), qnets.end()).size() == qgates.size(), "invalid recovered Q permutation");
                std::vector<Net*> dnets; std::vector<Gate*> gates;
                for (auto* net : qnets) { auto* gate = qgates.at(net); gates.push_back(gate); dnets.push_back(gate->get_fan_in_net("D")); }
                result.groups["words"][word.module->get_name()] = {{"module_id", word.module->get_id()}, {"gates", ids(gates)}, {"q_nets", ids(qnets)},
                    {"d_nets", ids(dnets)}, {"width", qnets.size()}, {"order_origin", origin}, {"native_q_nets", ids(native)}, {"group_origin", "HAL_DANA"}};
                ++counts[origin]; bits[origin] += qnets.size();
            }
            require(seen == expected, "DANA state coverage changed during ordering");
            std::vector<bo::BitOrder> destinations;
            for (const auto& [module, group] : targets) if (const auto* order = orders.get(module, group)) destinations.push_back(*order);
            unwrap(bo::reorder_module_pin_groups(bo::BitOrderResult(destinations)));
            result.groups["groups"] = words.size(); result.groups["FF_bits"] = seen.size();
            result.groups["order_groups"] = counts; result.groups["order_bits"] = bits;
            std::cout << "BITORDER_DONE " << Json(counts).dump() << std::endl;
            return OK(std::move(result));
        }
        catch (const std::exception& error) { return ERR(error.what()); }
    }
}
