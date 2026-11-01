#include "algorithm_recovery/routing.h"
#include "algorithm_recovery/evaluation.h"
#include "artifacts.h"
#include <algorithm>
#include <numeric>
#include <optional>

namespace hal::algorithm_recovery
{
    namespace
    {
        BitPermutation inverse(const BitPermutation& permutation, unsigned width)
        {
            artifacts::require(width && permutation.size() == width, "routing permutation width mismatch");
            BitPermutation result(width, width);
            for (unsigned bit = 0; bit < width; ++bit)
            {
                artifacts::require(permutation[bit] < width && result[permutation[bit]] == width, "invalid routing permutation");
                result[permutation[bit]] = bit;
            }
            return result;
        }
        Integer to_raw(const Integer& canonical, const BitPermutation& permutation)
        {
            Integer value = 0;
            for (unsigned bit = 0; bit < permutation.size(); ++bit)
                if (boost::multiprecision::bit_test(canonical, permutation[bit])) boost::multiprecision::bit_set(value, bit);
            return value;
        }

        // Deliberately only a proposal: zeros, omitted bits and nonlinear terms
        // can pass one-hot probes. They cannot bypass the universal proof below.
        std::optional<BitPermutation> propose(const std::vector<const FoldNode*>& nodes,
                                             const std::map<std::string, BitPermutation>& accepted)
        {
            std::optional<std::map<unsigned, unsigned>> mapping;
            for (const auto* node : nodes)
            {
                const BitVectorProgram program(node->equation);
                if (program.inputs().size() != 1) return {};
                const auto& [symbol, width] = program.inputs().front();
                auto source = node->sources.find(symbol);
                if (!width || source == node->sources.end() || source->second.kind != SourceKind::signal) return {};
                if (program.evaluate(std::vector<Integer>{0}) != 0) return {};
                std::map<unsigned, unsigned> current;
                const auto order = accepted.find(source->second.name);
                for (unsigned bit = 0; bit < width; ++bit)
                {
                    const Integer hot = Integer(1) << bit;
                    const auto value = program.evaluate(std::vector<Integer>{order == accepted.end() ? hot : to_raw(hot, order->second)});
                    if (value == 0) continue;
                    if ((value & (value - 1)) != 0) return {};
                    current[boost::multiprecision::msb(value)] = bit;
                }
                if (mapping && *mapping != current) return {};
                mapping = std::move(current);
            }
            if (!mapping) return {};
            const unsigned width = nodes.front()->equation.get_sort().bv_size();
            BitPermutation sorted(width), permutation(width); std::iota(sorted.begin(), sorted.end(), 0);
            const auto rank = [&](unsigned bit) { const auto pos = mapping->find(bit); return pos == mapping->end() ? std::numeric_limits<unsigned>::max() : pos->second; };
            std::sort(sorted.begin(), sorted.end(), [&](unsigned a, unsigned b) { return std::make_pair(rank(a), a) < std::make_pair(rank(b), b); });
            for (unsigned bit = 0; bit < width; ++bit) permutation[sorted[bit]] = bit;
            return permutation;
        }
    }

    z3::expr permute_bits(const z3::expr& value, const BitPermutation& permutation)
    {
        artifacts::require(value.is_bv(), "routing requires bit vectors");
        const auto indices = inverse(permutation, value.get_sort().bv_size());
        z3::expr_vector bits(value.ctx());
        for (auto bit = indices.rbegin(); bit != indices.rend(); ++bit) bits.push_back(value.extract(*bit, *bit));
        return (bits.size() == 1 ? bits[0] : z3::concat(bits)).simplify();
    }
    z3::expr unpermute_bits(const z3::expr& value, const BitPermutation& permutation)
    {
        artifacts::require(value.is_bv(), "routing requires bit vectors");
        return permute_bits(value, inverse(permutation, value.get_sort().bv_size()));
    }

    Result<RoutingOrders> recover_routing(const FoldGraph& graph, const FoldInput& input,
                                         const std::filesystem::path& output, const RoutingOptions& options)
    {
        try
        {
            using Json = nlohmann::json;
            const auto out = std::filesystem::absolute(output);
            artifacts::require(!std::filesystem::exists(out) || std::filesystem::is_empty(out), "routing output directory must be empty");
            std::filesystem::create_directories(out / "proofs");
            std::map<std::string, std::vector<const FoldNode*>> by_word;
            std::set<std::string> established, pending;
            std::set<std::pair<std::string, unsigned>> ids;
            for (const auto& [name, word] : input.words)
            {
                artifacts::require(!word.bits.empty(), "empty routing word: " + name);
                (word.numeric_order_known ? established : pending).insert(name);
            }
            for (const auto& node : graph.nodes)
            {
                const auto& word = input.words.at(node.name);
                artifacts::require(ids.insert({node.name, node.phase}).second, "duplicate routing write");
                artifacts::require(node.equation.is_bv() && node.equation.get_sort().bv_size() == word.bits.size(), "routing word width mismatch");
                artifacts::require(node.numeric_order_known == word.numeric_order_known, "routing order provenance mismatch");
                const BitVectorProgram program(node.equation);
                for (const auto& [symbol, width] : program.inputs())
                {
                    const auto& source = node.sources.at(symbol);
                    if (source.kind == SourceKind::signal)
                        artifacts::require(input.words.at(source.name).bits.size() == width, "routing source width mismatch");
                }
                by_word[node.name].push_back(&node);
            }
            artifacts::require(graph.available || graph.nodes.empty(), "unavailable routing graph contains writes");
            RoutingOrders result; size_t calls = 0, accepted_bits = 0;
            Json rejected = Json::object(), relative = Json::object(), proofs = Json::object();
            while (graph.available && !pending.empty())
            {
                bool changed = false;
                const auto candidates = pending;
                for (const auto& name : candidates)
                {
                    if (!by_word.count(name)) { rejected[name] = "NO_WRITES_IN_GRAPH"; continue; }
                    const auto& nodes = by_word.at(name);
                    bool eligible = true;
                    for (const auto* node : nodes)
                    {
                        if (node->sources.size() != 1) { eligible = false; break; }
                        const auto& source = node->sources.begin()->second;
                        if (source.kind != SourceKind::signal || !established.count(source.name)
                            || input.words.at(source.name).bits.size() != input.words.at(name).bits.size()) { eligible = false; break; }
                    }
                    ++calls; const auto candidate = propose(nodes, result.accepted);
                    if (!candidate) { relative.erase(name); rejected[name] = "NATIVE_INFERENCE_NO_ORDER"; continue; }
                    if (!eligible)
                    {
                        relative[name] = *candidate; rejected[name] = "RELATIVE_ONLY_OR_NOT_FULL_WIDTH"; continue;
                    }
                    bool good = true; Json rows = Json::array();
                    for (const auto* node : nodes)
                    {
                        const auto& [symbol, source] = *node->sources.begin();
                        auto wanted = node->equation.ctx().bv_const(symbol.c_str(), candidate->size());
                        if (result.accepted.count(source.name)) wanted = permute_bits(wanted, result.accepted.at(source.name));
                        z3::solver solver(node->equation.ctx()); solver.set("timeout", options.proof_timeout_ms);
                        if (options.proof_resource_limit) solver.set("rlimit", options.proof_resource_limit);
                        solver.add(permute_bits(node->equation, *candidate) != wanted);
                        const auto query = solver.to_smt2(), hash = artifacts::sha256(query), file = hash + ".smt2";
                        if (!std::filesystem::exists(out / "proofs" / file)) artifacts::write(out / "proofs" / file, query);
                        const auto checked = solver.check();
                        Json row = {{"id", {name, node->phase}}, {"source", source.name}, {"query", (out / "proofs" / file).string()},
                            {"sha256", hash}, {"result", checked == z3::unsat ? "unsat" : checked == z3::sat ? "sat" : "unknown"}};
                        if (checked == z3::unknown) row["reason"] = solver.reason_unknown();
                        rows.push_back(std::move(row));
                        if (checked != z3::unsat) { good = false; break; }
                    }
                    proofs[name] = std::move(rows); relative.erase(name);
                    if (!good) { rejected[name] = "NATIVE_PROPOSAL_NOT_UNIVERSALLY_PROVED"; continue; }
                    result.accepted[name] = *candidate; established.insert(name); pending.erase(name); rejected.erase(name);
                    accepted_bits += candidate->size(); changed = true;
                }
                if (!changed) break;
            }
            result.report = {{"status", graph.available ? "INVFOLD_ROUTING_ORDERS_CHECKED" : "NO_GRAPH"},
                {"accepted", result.accepted}, {"rejected", rejected}, {"relative_candidates", relative}, {"unresolved", pending},
                {"native_calls", calls}, {"proofs", proofs}, {"accepted_groups", result.accepted.size()}, {"accepted_bits", accepted_bits},
                {"scope", "One-hot proposals with universal full-width copy proofs at every folded write, propagated from established HAL orders. Relative candidates confer no numeric order; raw graph coordinates remain unchanged."}};
            artifacts::write(out / "routing.json", result.report.dump(2) + '\n');
            return OK(std::move(result));
        }
        catch (const std::exception& error) { return ERR(error.what()); }
    }

    z3::expr canonical_equation(const FoldNode& node, const RoutingOrders& orders)
    {
        auto term = node.equation;
        z3::expr_vector from(term.ctx()), to(term.ctx());
        const BitVectorProgram program(term);
        for (const auto& [symbol, width] : program.inputs())
        {
            const auto source = node.sources.find(symbol);
            if (source == node.sources.end() || source->second.kind != SourceKind::signal) continue;
            const auto order = orders.accepted.find(source->second.name);
            if (order == orders.accepted.end()) continue;
            const auto variable = term.ctx().bv_const(symbol.c_str(), width);
            from.push_back(variable); to.push_back(unpermute_bits(variable, order->second));
        }
        if (!from.empty()) term = term.substitute(from, to);
        const auto order = orders.accepted.find(node.name);
        if (order != orders.accepted.end()) term = permute_bits(term, order->second);
        return term.simplify();
    }
}
