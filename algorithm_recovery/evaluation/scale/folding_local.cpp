#include "algorithm_recovery/folding.h"
#include <algorithm>
#include <limits>
#include <numeric>
#include <stdexcept>

namespace hal::algorithm_recovery
{
    namespace
    {
        void require(bool condition, const std::string& message)
        {
            if (!condition) throw std::runtime_error(message);
        }
        std::map<std::string, z3::expr> variables(const z3::expr& term)
        {
            std::map<std::string, z3::expr> result;
            std::vector<z3::expr> pending{term};
            std::set<unsigned> seen;
            while (!pending.empty())
            {
                auto value = pending.back(); pending.pop_back();
                if (!seen.insert(value.id()).second) continue;
                require(value.is_app(), "non-application expression in recovery equation");
                if (value.decl().decl_kind() == Z3_OP_UNINTERPRETED)
                {
                    require(value.is_const() && value.is_bv(), "unsupported symbolic function/sort");
                    const auto name = value.decl().name().str();
                    auto [it, inserted] = result.emplace(name, value);
                    require(inserted || z3::eq(it->second, value), "ambiguous symbol sorts: " + name);
                }
                for (unsigned i = 0; i < value.num_args(); ++i) pending.push_back(value.arg(i));
            }
            return result;
        }
        struct Endpoint { SourceKind kind; std::string name; unsigned width; };
    }

    Result<FoldGraph> fold_observed(const FoldInput& in, z3::context& ctx)
    {
        try
        {
            require(in.length > 0, "empty observation window");
            require(in.length <= static_cast<u32>(std::numeric_limits<int>::max()), "observation window exceeds last-write index range");
            require(in.start <= std::numeric_limits<u64>::max() - in.length, "window time overflow");
            FoldGraph graph;
            for (const auto& [name, w] : in.words)
            {
                require(!w.bits.empty() && w.writes.size() == in.length && w.equations.size() == in.length, "invalid window for " + name);
                for (const auto& e : w.equations)
                    require(&e.ctx() == &ctx && e.is_bv() && e.get_sort().bv_size() == w.bits.size(), "equation context/width mismatch: " + name);
                if (std::find(w.writes.begin(), w.writes.end(), Write::mixed_or_unknown) != w.writes.end())
                { graph.excluded[name] = "MIXED_OR_UNKNOWN_PHYSICAL_WRITES"; continue; }
                if (std::find(w.writes.begin(), w.writes.end(), Write::full) == w.writes.end())
                { graph.excluded[name] = "NO_WRITES_IN_WINDOW"; continue; }
                for (u32 p = 1; p <= in.length / 3; ++p)
                {
                    bool matches = true;
                    for (u32 t = 0; t < in.length - p && matches; ++t)
                        matches = w.writes[t] == w.writes[t + p] &&
                                  (w.writes[t] == Write::hold || z3::eq(w.equations[t], w.equations[t + p]));
                    if (matches) { graph.periods[name] = p; break; }
                }
                if (!graph.periods.count(name)) graph.excluded[name] = "NO_OBSERVED_PERIOD";
            }
            if (graph.periods.empty()) return OK(std::move(graph));
            graph.period = 1;
            for (const auto& [name, period] : graph.periods)
            {
                auto quotient = graph.period / std::gcd(graph.period, static_cast<u64>(period));
                require(quotient <= std::numeric_limits<u64>::max() / period, "observed period LCM overflow");
                graph.period = quotient * period;
            }
            if (in.period_multiple)  // unfolding to a multiple of the certificate period (Theorem: period multiples are unfoldings)
            {
                auto quotient = graph.period / std::gcd(graph.period, in.period_multiple);
                require(quotient <= std::numeric_limits<u64>::max() / in.period_multiple, "aligned period overflow");
                graph.period = quotient * in.period_multiple;
            }
            if (graph.period > in.length / 4) return OK(std::move(graph));
            const u32 P = static_cast<u32>(graph.period);
            graph.origin = in.start + P;

            z3::expr_vector from(ctx), to(ctx);
            std::map<std::string, Endpoint> endpoints;
            std::set<std::string> occupied;
            std::set<std::pair<SourceKind, std::string>> logical;
            auto pack = [&](const std::vector<std::string>& bits, SourceKind kind, const std::string& name, const std::string& symbol) {
                require(!bits.empty(), "zero-width endpoint: " + name);
                require(logical.emplace(kind, name).second, "duplicate logical endpoint: " + name);
                require(endpoints.emplace(symbol, Endpoint{kind, name, static_cast<unsigned>(bits.size())}).second, "duplicate endpoint: " + name);
                auto word = ctx.bv_const(symbol.c_str(), bits.size());
                for (unsigned bit = 0; bit < bits.size(); ++bit)
                {
                    require(occupied.insert(bits[bit]).second, "multiply owned physical bit: " + bits[bit]);
                    from.push_back(ctx.bv_const(bits[bit].c_str(), 1));
                    to.push_back(word.extract(bit, bit));
                }
            };
            for (const auto& [name, word] : in.words)
                pack(word.bits, graph.periods.count(name) ? SourceKind::signal : SourceKind::input,
                     graph.periods.count(name) ? name : "cut_" + name, "word:" + name);
            for (const auto& [name, bits] : in.inputs) pack(bits, SourceKind::input, name, "input:" + name);
            for (const auto& [name, memory] : in.memories)
            {
                require(memory.read_bits.size() == 16, "unsupported RAM read width");
                pack(memory.read_bits, SourceKind::ram, name, "ram:" + name);
                graph.memories.emplace(name, memory.model);
            }

            // Store actual last writes strictly before each event, including the
            // real prefix. This also makes RAM port binding independent of scans.
            std::map<std::string, std::vector<int>> previous;
            for (const auto& [name, p] : graph.periods)
            {
                int last = -1;
                auto& rows = previous[name];
                for (u32 t = 0; t < in.length; ++t)
                {
                    rows.push_back(last);
                    if (in.words.at(name).writes[t] == Write::full) last = static_cast<int>(t);
                }
            }
            std::map<unsigned, z3::expr> normalized;
            std::map<unsigned, std::map<std::string, z3::expr>> supports;
            // E6 variant "local" (E6_SCALING_PROTOCOL.txt): substitute only the physical bit symbols that occur in
            // the expression; pairs whose symbol does not occur cannot change the result, so the output is identical.
            std::map<std::string, unsigned> bit_index;
            for (unsigned i = 0; i < from.size(); ++i) bit_index.emplace(from[i].decl().name().str(), i);
            auto translate = [&](const z3::expr& value) {
                if (!normalized.count(value.id()))
                {
                    auto e = value;
                    z3::expr_vector lf(ctx), lt(ctx);
                    for (const auto& [symbol, var] : variables(value))
                        if (auto it = bit_index.find(symbol); it != bit_index.end()) { lf.push_back(from[it->second]); lt.push_back(to[it->second]); }
                    e = (lf.size() ? e.substitute(lf, lt) : e).simplify();
                    auto vars = variables(e);
                    for (const auto& [symbol, var] : vars)
                        require(endpoints.count(symbol) && endpoints.at(symbol).width == var.get_sort().bv_size(), "unresolved physical symbol: " + symbol);
                    supports.emplace(e.id(), std::move(vars));
                    normalized.emplace(value.id(), e);
                }
                return normalized.at(value.id());
            };
            auto resolve = [&](const z3::expr& value, u32 t) {
                std::map<std::string, Source> sources;
                const auto n = static_cast<int>(t / P) - 1;
                for (const auto& [symbol, var] : supports.at(value.id()))
                {
                    const auto& endpoint = endpoints.at(symbol);
                    Source src{endpoint.kind, endpoint.name, t % P, 0};
                    if (src.kind == SourceKind::signal)
                    {
                        const auto tw = previous.at(src.name).at(t);
                        require(tw >= 0, "read without a real preceding write: " + src.name);
                        src.phase = tw % P;
                        src.delay = n - (tw / static_cast<int>(P) - 1);
                        if (src.delay) graph.histories.insert(src);
                    }
                    sources.emplace(symbol, std::move(src));
                }
                return sources;
            };
            // Compare all three repetitions, not just counts or phase-zero wiring.
            for (u32 phase = 0; phase < P; ++phase)
                for (const auto& [name, period] : graph.periods)
                {
                    const auto& word = in.words.at(name);
                    if (word.writes[P + phase] != Write::full) continue;
                    auto value = translate(word.equations[P + phase]);
                    auto sources = resolve(value, P + phase);
                    for (u32 repetition = 2; repetition <= 3; ++repetition)
                    {
                        auto other = translate(word.equations[repetition * P + phase]);
                        require(z3::eq(value, other) && sources == resolve(other, repetition * P + phase), "inconsistent cross-period binding: " + name);
                    }
                    graph.nodes.push_back({name, phase, value, std::move(sources), word.numeric_order_known});
                }
            const std::map<std::string, unsigned> widths{{"RADDR", 11}, {"WADDR", 11}, {"WDATA", 16}, {"MASK", 16},
                                                       {"RE", 1}, {"RCLKE", 1}, {"WE", 1}, {"WCLKE", 1}};
            for (const auto& [name, memory] : in.memories)
            {
                require(memory.model.ports.size() == widths.size(), "incomplete RAM port functions: " + name);
                for (const auto& [port, width] : widths)
                {
                    const auto& raw = memory.model.ports.at(port);
                    require(&raw.ctx() == &ctx && raw.is_bv() && raw.get_sort().bv_size() == width, "RAM port width/context mismatch");
                    auto value = translate(raw);
                    for (u32 phase = 0; phase < P; ++phase)
                    {
                        auto sources = resolve(value, P + phase);
                        for (u32 repetition = 2; repetition <= 3; ++repetition)
                            require(sources == resolve(value, repetition * P + phase), "RAM binding varies between periods: " + name);
                        graph.ram_nodes.push_back({name, port, phase, value, std::move(sources)});
                    }
                }
            }
            graph.available = true;
            return OK(std::move(graph));
        }
        catch (const std::exception& e) { return ERR(e.what()); }
    }

    std::map<std::string, RamState> initial_memories(const FoldGraph& graph, u16 initial_read)
    {
        std::map<std::string, RamState> result;
        for (const auto& [name, model] : graph.memories)
            result.emplace(name, RamState{model.initial, initial_read});
        return result;
    }

    Result<FrameResult> run_frame(const FoldGraph& graph, const History& history, const FrameValues& inputs,
                                 const std::map<std::string, RamState>& memories, z3::context& ctx)
    {
        try
        {
            require(graph.available && graph.period > 0, "no executable graph");
            FrameResult result;
            result.memories = memories;
            require(memories.size() == graph.memories.size(), "RAM state count mismatch");
            for (const auto& [name, model] : graph.memories) require(memories.count(name), "missing RAM state: " + name);
            auto evaluate = [&](const auto& node) {
                z3::expr_vector from(ctx), to(ctx);
                for (const auto& [symbol, variable] : variables(node.equation))
                {
                    const auto& src = node.sources.at(symbol);
                    const z3::expr value = src.kind == SourceKind::input ? inputs.at({src.name, src.phase}) :
                        src.kind == SourceKind::ram ? ctx.bv_val(result.memories.at(src.name).read, 16) :
                        src.delay ? history.at(src) : result.values.at({src.name, src.phase});
                    require(&value.ctx() == &ctx && value.is_numeral() && z3::eq(variable.get_sort(), value.get_sort()), "non-concrete or wrong-width frame operand: " + symbol);
                    from.push_back(variable); to.push_back(value);
                }
                auto value = node.equation;
                value = value.substitute(from, to).simplify();
                require(value.is_numeral(), "frame evaluation left symbolic state");
                return value;
            };
            auto edge = [&](u32 clock, u32 phase) {
                const auto& value = inputs.at({"event_rise_" + std::to_string(clock), phase});
                require(&value.ctx() == &ctx && value.is_bv() && value.get_sort().bv_size() == 1 && value.is_numeral(), "invalid clock edge input");
                return value.get_numeral_uint64() != 0;
            };
            for (u32 phase = 0; phase < graph.period; ++phase)
            {
                for (const auto& node : graph.nodes)
                    if (node.phase == phase) result.values.emplace(std::make_pair(node.name, phase), evaluate(node));
                std::map<std::string, std::map<std::string, u16>> ports;
                for (const auto& node : graph.ram_nodes)
                    if (node.phase == phase) ports[node.memory][node.port] = evaluate(node).get_numeral_uint64();
                // Evaluate every RAM port against old read states before changing
                // any RAM, including when RAMs feed one another.
                std::map<std::string, std::pair<u16, u16>> reads;
                for (const auto& [name, model] : graph.memories)
                {
                    const auto& p = ports.at(name);
                    RamPorts values{p.at("RADDR"), p.at("WADDR"), p.at("WDATA"), p.at("MASK"),
                                    bool(p.at("RE")), bool(p.at("RCLKE")), bool(p.at("WE")), bool(p.at("WCLKE"))};
                    auto& state = result.memories.at(name);
                    const auto old = state.read;
                    step_ram(state, values, edge(model.read_clock, phase), edge(model.write_clock, phase));
                    reads.emplace(name, std::make_pair(old, state.read));
                }
                result.reads.push_back(std::move(reads));
            }
            for (const auto& src : graph.histories)
            {
                auto prev = src;
                require(prev.delay > 0, "invalid history entry");
                --prev.delay;
                result.history.emplace(src, prev.delay == 0 ? result.values.at({src.name, src.phase}) : history.at(prev));
            }
            return OK(std::move(result));
        }
        catch (const std::exception& e) { return ERR(e.what()); }
    }
}
