// E6 driver (protocol E6_SCALING_PROTOCOL.txt): supplied word equations -> FoldInput -> fold_observed -> graph JSON.
// fold_observed is the unchanged implementation of every E1-E5 graph, linked from the plugin library.
// usage: e6_fold EQUATIONS.json OUT_GRAPH.json
// Words are supplied directly (|word:R| symbols in the equations; each word gets unique placeholder bit symbols, which
// the equations do not use), so DANA grouping is replaced by the supplied words. No period is supplied.
#include "algorithm_recovery/folding.h"
#include <nlohmann/json.hpp>
#include <chrono>
#include <fstream>
#include <iostream>
#include <sstream>

using namespace hal::algorithm_recovery;
using clk = std::chrono::steady_clock;

static double since(clk::time_point t) { return std::chrono::duration<double>(clk::now() - t).count(); }

int main(int argc, char** argv)
{
    if (argc != 3) { std::cerr << "usage: e6_fold EQUATIONS.json OUT_GRAPH.json\n"; return 2; }
    const auto t0 = clk::now();
    nlohmann::json J;
    { std::ifstream f(argv[1]); J = nlohmann::json::parse(f); }
    const double t_json = since(t0);

    z3::context ctx;
    const auto t1 = clk::now();
    const auto& eqs = J["equations"];
    std::vector<unsigned> width(eqs.size(), 0);
    for (const auto& [name, w] : J["words"].items())
        for (const auto& i : w["eq"])
            if (i.get<long>() >= 0) width[i.get<long>()] = w["width"].get<unsigned>();
    std::ostringstream script;
    for (const auto& [name, w] : J["words"].items())
        script << "(declare-const |word:" << name << "| (_ BitVec " << w["width"].get<unsigned>() << "))\n";
    for (const auto& [name, w] : J["inputs"].items())
        script << "(declare-const |input:" << name << "| (_ BitVec " << w.get<unsigned>() << "))\n";
    for (size_t i = 0; i < eqs.size(); ++i)
    {
        if (!width[i]) { std::cerr << "unused equation " << i << "\n"; return 1; }
        script << "(declare-const |__e" << i << "| (_ BitVec " << width[i] << "))\n(assert (= |__e" << i << "| "
               << eqs[i].get<std::string>() << "))\n";
    }
    const auto parsed = ctx.parse_string(script.str().c_str());
    if (parsed.size() != eqs.size()) { std::cerr << "parse count mismatch\n"; return 1; }
    std::vector<z3::expr> terms;
    terms.reserve(eqs.size());
    for (unsigned i = 0; i < parsed.size(); ++i) terms.push_back(parsed[i].arg(1));

    FoldInput in;
    in.start = J["start"].get<u64>();
    in.length = J["length"].get<u32>();
    for (const auto& [name, w] : J["words"].items())
    {
        const unsigned width_w = w["width"].get<unsigned>();
        WordWindow ww;
        for (unsigned b = 0; b < width_w; ++b) ww.bits.push_back("phys:" + name + "." + std::to_string(b));
        const auto self = ctx.bv_const(("word:" + name).c_str(), width_w);
        const auto writes = w["writes"].get<std::string>();
        const auto& idx = w["eq"];
        for (u32 t = 0; t < in.length; ++t)
        {
            ww.writes.push_back(writes[t] == 'H' ? Write::hold : Write::full);
            const long i = idx[t].get<long>();
            ww.equations.push_back(i < 0 ? self : terms[i]);
        }
        ww.numeric_order_known = true;
        in.words.emplace(name, std::move(ww));
    }
    for (const auto& [name, w] : J["inputs"].items())
    {
        std::vector<std::string> bits;
        for (unsigned b = 0; b < w.get<unsigned>(); ++b) bits.push_back("phys:in." + name + "." + std::to_string(b));
        in.inputs.emplace(name, std::move(bits));
    }
    const double t_build = since(t1);

    const auto t2 = clk::now();
    auto folded = fold_observed(in, ctx);
    const double t_fold = since(t2);
    if (folded.is_error()) { std::cerr << "fold error: " << folded.get_error().get() << "\n"; return 1; }
    const auto& g = folded.get();

    const auto t3 = clk::now();
    nlohmann::json out{{"available", g.available}, {"period", g.period}, {"origin", g.origin}, {"excluded", g.excluded},
                       {"histories", g.histories.size()}, {"nodes", nlohmann::json::array()}};
    std::map<u32, u64> hist;
    for (const auto& [name, p] : g.periods) ++hist[p];
    out["period_histogram"] = hist;
    const char* kinds[] = {"signal", "input", "ram"};
    for (const auto& n : g.nodes)
    {
        nlohmann::json src = nlohmann::json::object();
        for (const auto& [sym, s] : n.sources)
            src[sym] = {kinds[static_cast<int>(s.kind)], s.name, s.phase, s.delay};
        out["nodes"].push_back({{"name", n.name}, {"phase", n.phase}, {"equation", n.equation.to_string()}, {"sources", src}});
    }
    const double t_dump_build = since(t3);
    { std::ofstream f(argv[2]); f << out.dump(); }
    const double t_dump = since(t3);
    nlohmann::json stats{{"words", in.words.size()}, {"inputs", in.inputs.size()}, {"window", in.length},
                         {"distinct_equations", eqs.size()}, {"period", g.period}, {"origin", g.origin},
                         {"available", g.available}, {"nodes", g.nodes.size()}, {"excluded", g.excluded.size()},
                         {"seconds_json", t_json}, {"seconds_build", t_build}, {"seconds_fold", t_fold},
                         {"seconds_dump", t_dump}, {"seconds_dump_build", t_dump_build}, {"seconds_total", since(t0)}};
    std::cout << stats.dump() << std::endl;
    return 0;
}
