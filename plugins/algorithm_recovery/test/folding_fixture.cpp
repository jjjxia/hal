// Real-input migration test. The production folder takes typed HAL expressions;
// only this test reads the previous adapter's preserved JSON stage artifacts.
#include "algorithm_recovery/folding.h"
#include <nlohmann/json.hpp>
#include <filesystem>
#include <fstream>
#include <iostream>
#include <regex>
#include <stdexcept>

using namespace hal::algorithm_recovery;
using Json = nlohmann::json;
namespace fs = std::filesystem;

static Json read(const fs::path& path)
{
    std::ifstream stream(path);
    if (!stream) throw std::runtime_error("cannot open " + path.string());
    Json value; stream >> value; return value;
}
static void check(bool condition, const std::string& why)
{
    if (!condition) throw std::runtime_error(why);
}
static z3::expr parse(z3::context& ctx, const std::string& text, const std::map<std::string, unsigned>& widths)
{
    std::string decl;
    for (const auto& [name, width] : widths)
        decl += "(declare-const |" + name + "| (_ BitVec " + std::to_string(width) + "))\n";
    return ctx.parse_string((decl + "(assert (= " + text + " " + text + "))").c_str())[0].arg(0);
}
static z3::expr physical(z3::context& ctx, const std::string& text)
{
    std::map<std::string, unsigned> widths;
    const std::regex pattern("\\bb[0-9]+\\b");
    for (std::sregex_iterator it(text.begin(), text.end(), pattern), end; it != end; ++it) widths.emplace(it->str(), 1);
    return parse(ctx, text, widths);
}
static Source source(const Json& row)
{
    const std::string kind = row[0];
    check(kind == "sig" || kind == "in" || kind == "ram", "unsupported source in reference");
    return {kind == "sig" ? SourceKind::signal : kind == "in" ? SourceKind::input : SourceKind::ram, row[1], row[2], row[3]};
}

int main(int argc, char** argv)
{
    if (argc != 3) { std::cerr << "Usage: check-recovery-folding-fixture case_directory recovered.json\n"; return 2; }
    try
    {
        const fs::path root(argv[1]);
        const auto sse = read(root / "native_sse.json"), ram = read(root / "native_ram.json"), original = read(argv[2]);
        check(sse.at("status") == "HAL_GUIDED_EQUATIONS_ORIGINAL_LUT_CHECKED", "wrong SSE fixture status");
        check(ram.at("status") == "HAL_RAM_PORT_EQUATIONS_CHECKED", "wrong RAM fixture status");
        z3::context ctx;
        std::vector<z3::expr> forms;
        for (const auto& form : sse.at("forms")) forms.push_back(physical(ctx, form));
        std::map<std::pair<unsigned, std::string>, z3::expr> effective;
        FoldInput base;
        for (auto it = ram.at("memories").begin(); it != ram.at("memories").end(); ++it)
        {
            MemoryWindow memory;
            memory.model.name = it.key();
            memory.model.gate_id = it.value().at("gate_id");
            memory.model.read_clock = it.value().at("rclk");
            memory.model.write_clock = it.value().at("wclk");
            memory.model.initial = it.value().at("initial").get<std::array<u16, 256>>();
            for (const auto& bit : it.value().at("pins").at("RDATA")) memory.read_bits.push_back("b" + bit.dump());
            for (auto f = it.value().at("functions").begin(); f != it.value().at("functions").end(); ++f)
                memory.model.ports.emplace(f.key(), physical(ctx, f.value().at("smt2")));
            base.memories.emplace(it.key(), std::move(memory));
        }
        const auto& ports = original.at("modules").at("recovered").at("ports");
        for (auto it = ports.begin(); it != ports.end(); ++it)
            if (it.value().at("direction") != "output")
                for (const auto& bit : it.value().at("bits"))
                    base.inputs.emplace("pad" + bit.dump(), std::vector<std::string>{"b" + bit.dump()});
        for (auto profile = sse.at("windows").begin(); profile != sse.at("windows").end(); ++profile)
        {
            std::cout << "CHECK " << profile.key() << " preparing native window" << std::endl;
            auto input = base;
            input.start = profile.value()[0]; input.length = profile.value().size();
            for (unsigned i = 0; i < input.length; ++i) check(profile.value()[i] == input.start + i, "non-contiguous window");
            for (auto word = sse.at("words").begin(); word != sse.at("words").end(); ++word)
            {
                WordWindow w;
                const auto& row = word.value();
                w.numeric_order_known = row.at("order_origin") != "UNRESOLVED";
                for (const auto& bit : row.at("q")) w.bits.push_back("b" + bit.dump());
                for (const auto& time : profile.value())
                {
                    const unsigned t = time;
                    const auto& mask = row.at("write_masks").at(t);
                    const bool full = std::all_of(mask.begin(), mask.end(), [](const auto& v) { return v == 1; });
                    const bool hold = std::all_of(mask.begin(), mask.end(), [](const auto& v) { return v == 0; });
                    w.writes.push_back(full ? Write::full : hold ? Write::hold : Write::mixed_or_unknown);
                    const unsigned id = row.at("forms").at(std::to_string(t));
                    const auto& resets = row.at("reset_values").at(t);
                    const auto key = std::make_pair(id, resets.dump());
                    if (!effective.count(key))
                    {
                        check(resets.size() == w.bits.size(), "reset width mismatch");
                        z3::expr_vector bits(ctx);
                        for (int i = static_cast<int>(w.bits.size()) - 1; i >= 0; --i)
                            bits.push_back(resets[i].is_number_integer() ? ctx.bv_val(resets[i].get<int>(), 1) : forms.at(id).extract(i, i));
                        effective.emplace(key, (bits.size() == 1 ? bits[0] : z3::concat(bits)).simplify());
                    }
                    w.equations.push_back(effective.at(key));
                }
                input.words.emplace(word.key(), std::move(w));
            }
            auto folded = fold_observed(input, ctx);
            if (folded.is_error()) throw std::runtime_error(folded.get_error().get());
            const auto& graph = folded.get();
            const auto folder = root / ("fold_" + profile.key());
            const auto report = read(folder / "result.json");
            std::map<std::string, unsigned> excluded;
            for (const auto& [name, reason] : graph.excluded) ++excluded[reason];
            check(Json(excluded) == report.at("excluded"), "exclusion classifications differ");
            if (!graph.available)
            {
                check(report.at("status") == "INSUFFICIENT_PERIODIC_WINDOW" && report.at("global_period") == graph.period, "unexpected absent graph");
                std::cout << "PASS " << profile.key() << " NO_GRAPH period=" << graph.period << '\n';
                continue;
            }
            const auto old = read(folder / "graph.json");
            std::cout << "CHECK " << profile.key() << " comparing " << graph.nodes.size() << " nodes" << std::endl;
            check(graph.period == old.at("period") && graph.origin == old.at("origin"), "period/origin mismatch");
            check(graph.nodes.size() == old.at("nodes").size(), "node count mismatch");
            check(graph.ram_nodes.size() == old.at("ram_nodes").size(), "RAM node count mismatch");
            std::map<std::pair<std::string, unsigned>, const FoldNode*> nodes;
            for (const auto& node : graph.nodes) nodes.emplace(std::make_pair(node.name, node.phase), &node);
            size_t proven = 0;
            // Keep both AST roots alive: Z3 may reuse IDs after destruction.
            std::map<std::pair<unsigned, unsigned>, std::pair<z3::expr, z3::expr>> proof_cache;
            for (const auto& node : old.at("nodes"))
            {
                const auto& now = *nodes.at({node.at("id")[0], node.at("id")[1]});
                check(now.numeric_order_known == input.words.at(now.name).numeric_order_known, "numeric order provenance differs");
                const auto widths = node.at("widths").get<std::map<std::string, unsigned>>();
                std::map<Source, std::string> native;
                for (const auto& [symbol, src] : now.sources) check(native.emplace(src, symbol).second, "aliased native source");
                check(native.size() == node.at("sources").size(), "source count mismatch");
                z3::expr_vector from(ctx), to(ctx);
                for (auto s = node.at("sources").begin(); s != node.at("sources").end(); ++s)
                {
                    const auto symbol = native.at(source(s.value()));
                    from.push_back(ctx.bv_const(s.key().c_str(), widths.at(s.key())));
                    to.push_back(ctx.bv_const(symbol.c_str(), widths.at(s.key())));
                }
                auto prior = parse(ctx, node.at("smt2"), widths);
                prior = prior.substitute(from, to).simplify();
                // Structural equality in this context is already an exact match;
                // otherwise require a universal proof, never sampling or UNKNOWN.
                const auto query_key = std::make_pair(prior.id(), now.equation.id());
                if (!z3::eq(prior, now.equation) && !proof_cache.count(query_key))
                {
                    z3::solver solver(ctx); solver.set("timeout", 60000u);
                    solver.add(prior != now.equation);
                    check(solver.check() == z3::unsat, "node expression equivalence not proven");
                    proof_cache.emplace(query_key, std::make_pair(prior, now.equation));
                }
                ++proven;
                if (proven % 1000 == 0) std::cout << "CHECK " << profile.key() << ' ' << proven << " nodes equivalent" << std::endl;
            }
            std::set<Source> expected_history;
            for (const auto& h : old.at("histories")) expected_history.insert({SourceKind::signal, h[0], h[1], h[2]});
            check(graph.histories == expected_history, "history mismatch");
            std::map<std::tuple<std::string, std::string, unsigned>, const RamNode*> ram_nodes;
            for (const auto& node : graph.ram_nodes) ram_nodes.emplace(std::make_tuple(node.memory, node.port, node.phase), &node);
            for (const auto& node : old.at("ram_nodes"))
            {
                const auto& now = *ram_nodes.at({node.at("memory"), node.at("port"), node.at("phase")});
                std::set<Source> wanted, actual;
                for (auto it = node.at("sources").begin(); it != node.at("sources").end(); ++it) wanted.insert(source(it.value()));
                for (const auto& [symbol, src] : now.sources) actual.insert(src);
                check(wanted == actual, "RAM port binding mismatch");
            }
            std::cout << "PASS " << profile.key() << " period=" << graph.period << " nodes=" << proven
                      << " RAM bindings=" << graph.ram_nodes.size() << " histories=" << graph.histories.size() << std::endl;
        }
    }
    catch (const std::exception& error) { std::cerr << error.what() << '\n'; return 1; }
}
