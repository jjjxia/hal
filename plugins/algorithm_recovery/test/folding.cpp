#include "algorithm_recovery/folding.h"
#include <boost/multiprecision/cpp_int.hpp>
#include <iostream>
#include <stdexcept>

using namespace hal::algorithm_recovery;
using boost::multiprecision::cpp_int;
static void check(bool value, const char* message) { if (!value) throw std::runtime_error(message); }
template<class T> static T unwrap(hal::Result<T>&& result)
{
    if (result.is_error()) throw std::runtime_error(result.get_error().get());
    return std::move(result.get());
}
static std::vector<std::string> bits(const std::string& base, unsigned width)
{
    std::vector<std::string> result;
    for (unsigned i = 0; i < width; ++i) result.push_back(base + std::to_string(i));
    return result;
}
static z3::expr word(z3::context& ctx, const std::vector<std::string>& names)
{
    z3::expr_vector values(ctx);
    for (auto it = names.rbegin(); it != names.rend(); ++it) values.push_back(ctx.bv_const(it->c_str(), 1));
    return values.size() == 1 ? values[0] : z3::concat(values);
}
static void sequential_test(z3::context& ctx)
{
    FoldInput in; in.start = 101; in.length = 12;
    WordWindow a, b;
    a.bits = bits("a", 80); b.bits = bits("b", 80);
    in.inputs["x"] = bits("x", 80);
    const auto av = word(ctx, a.bits), bv = word(ctx, b.bits), x = word(ctx, in.inputs.at("x"));
    for (unsigned t = 0; t < in.length; ++t)
    {
        a.writes.push_back(t % 2 ? Write::hold : Write::full);
        b.writes.push_back(t % 2 ? Write::full : Write::hold);
        a.equations.push_back(bv + x);
        b.equations.push_back(av + ctx.bv_val(5, 80));
    }
    in.words.emplace("a", a); in.words.emplace("b", b);
    const auto graph = unwrap(fold_observed(in, ctx));
    check(graph.available && graph.period == 2 && graph.origin == 103 && graph.nodes.size() == 2, "bad two-phase graph");
    const Source previous_b{SourceKind::signal, "b", 1, 1};
    check(graph.histories == std::set<Source>{previous_b}, "last write should cross frame only for b");
    cpp_int expected = (cpp_int(1) << 80) - 7;
    History history;
    history.emplace(previous_b, ctx.bv_val(expected.str().c_str(), 80));
    const cpp_int mask = (cpp_int(1) << 80) - 1;
    for (unsigned n = 0; n < 20; ++n)
    {
        FrameValues inputs;
        inputs.emplace(std::make_pair("x", 0), ctx.bv_val(3, 80));
        const auto out = unwrap(run_frame(graph, history, inputs, {}, ctx));
        cpp_int ea = (expected + 3) & mask;
        expected = (ea + 5) & mask;
        check(z3::eq(out.values.at({"a", 0}), ctx.bv_val(ea.str().c_str(), 80)), "wrong phase-zero result");
        check(z3::eq(out.values.at({"b", 1}), ctx.bv_val(expected.str().c_str(), 80)), "wrong phase-one result");
        history = out.history;
    }
    check(run_frame(graph, {}, {}, {}, ctx).is_error(), "missing boundary state was silently initialized");
    FrameValues narrow; narrow.emplace(std::make_pair("x", 0), ctx.bv_val(1, 1));
    check(run_frame(graph, history, narrow, {}, ctx).is_error(), "wrong-width input was silently coerced");
    auto changed = in;
    changed.words.at("a").equations.back() = ctx.bv_val(9, 80); // a holds here: must not change period
    check(unwrap(fold_observed(changed, ctx)).period == 2, "hold expression affected period");
    changed.words.at("a").equations[10] = ctx.bv_val(9, 80); // only last observed write changes
    const auto late = unwrap(fold_observed(changed, ctx));
    check(late.excluded.at("a") == "NO_OBSERVED_PERIOD", "period checked only a short prefix");
    changed = in; changed.words.at("b").writes[7] = Write::mixed_or_unknown;
    check(unwrap(fold_observed(changed, ctx)).excluded.at("b") == "MIXED_OR_UNKNOWN_PHYSICAL_WRITES", "mixed writes accepted");
    changed = in;
    for (auto& w : changed.words.at("b").writes) w = Write::hold;
    check(unwrap(fold_observed(changed, ctx)).excluded.at("b") == "NO_WRITES_IN_WINDOW", "inactive word accepted");
    changed = in; changed.inputs.clear();
    check(fold_observed(changed, ctx).is_error(), "unbound physical bits accepted");
    changed = in; changed.inputs["collision"] = a.bits;
    check(fold_observed(changed, ctx).is_error(), "overlapping cutpoints accepted");
    changed = in;
    for (auto& write : changed.words.at("b").writes) write = Write::hold;
    changed.inputs["cut_b"] = bits("other", 1);
    check(fold_observed(changed, ctx).is_error(), "ambiguous boundary name accepted");
    changed = in;
    for (unsigned t = 0; t < changed.length; ++t) changed.words.at("b").writes[t] = t % 3 ? Write::hold : Write::full;
    const auto short_window = unwrap(fold_observed(changed, ctx));
    check(!short_window.available && short_window.period == 6, "insufficient common-period window yielded a graph");
}
static void ram_test(z3::context& ctx)
{
    FoldInput in; in.length = 8;
    WordWindow r; r.bits = bits("r", 16);
    MemoryWindow a, b;
    a.model.name = "A"; b.model.name = "B";
    a.model.initial[0] = 10; b.model.initial[0] = 20;
    a.read_bits = bits("ma", 16); b.read_bits = bits("mb", 16);
    const auto old_a = word(ctx, a.read_bits), old_b = word(ctx, b.read_bits), old_r = word(ctx, r.bits);
    for (unsigned t = 0; t < in.length; ++t) { r.writes.push_back(Write::full); r.equations.push_back(old_a); }
    in.words.emplace("r", std::move(r));
    for (auto* mem : {&a, &b})
    {
        mem->model.read_clock = mem->model.write_clock = 7;
        for (const auto& port : {"RE", "RCLKE", "WE", "WCLKE"}) mem->model.ports.emplace(port, ctx.bv_val(1, 1));
        for (const auto& port : {"RADDR", "WADDR"}) mem->model.ports.emplace(port, ctx.bv_val(0, 11));
        mem->model.ports.emplace("MASK", ctx.bv_val(0, 16));
    }
    a.model.ports.emplace("WDATA", old_b);
    b.model.ports.emplace("WDATA", old_r + ctx.bv_val(1, 16));
    in.memories.emplace("A", a); in.memories.emplace("B", b);
    const auto graph = unwrap(fold_observed(in, ctx));
    check(graph.available && graph.period == 1 && graph.ram_nodes.size() == 16, "bad coupled RAM graph");
    History history; history.emplace(Source{SourceKind::signal, "r", 0, 1}, ctx.bv_val(30, 16));
    auto state = initial_memories(graph, 37);
    check(state.at("A").memory[0] == 10 && state.at("B").memory[0] == 20 && state.at("A").read == 37, "wrong extracted INIT or initial read choice");
    state["A"].read = 100; state["B"].read = 200;
    unsigned previous_r = 30;
    for (unsigned event = 0; event < 10; ++event)
    {
        FrameValues inputs; inputs.emplace(std::make_pair("event_rise_7", 0), ctx.bv_val(1, 1));
        const auto out = unwrap(run_frame(graph, history, inputs, state, ctx));
        check(out.values.at({"r", 0}).get_numeral_uint64() == state.at("A").read, "FF sampled new rather than old RAM read");
        check(out.memories.at("A").read == state.at("A").memory[0] && out.memories.at("B").read == state.at("B").memory[0], "RAM read-after-write bug");
        check(out.memories.at("A").memory[0] == state.at("B").read, "RAM-to-RAM update was not simultaneous");
        check(out.memories.at("B").memory[0] == (previous_r + 1) % 65536, "RAM used current rather than pre-event FF state");
        previous_r = state.at("A").read; history = out.history; state = out.memories;
    }
}
int main()
{
    try { z3::context ctx; sequential_test(ctx); ram_test(ctx); std::cout << "PASS wide-word folding, full-window exclusions, last writes and coupled RAM frames\n"; }
    catch (const std::exception& e) { std::cerr << e.what() << '\n'; return 1; }
}
