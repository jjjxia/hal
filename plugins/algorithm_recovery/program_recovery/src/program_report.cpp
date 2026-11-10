// Structure report of the generated programs (report.json). Trace validation is a separate step
// (algorithm_recovery/evaluation/program_checks/validate_programs.py), run by CPython on the saved programs.
#include "program_report.h"
#include <algorithm>

namespace program_recovery
{
    PJ program_report(const EventGraph& M, const ProgramBuilder& D, const ProgramLifter& L)
    {
        auto lines = [](const std::string& s) { return static_cast<long long>(std::count(s.begin(), s.end(), '\n')); };
        PJ rep = PJ::dict();
        rep.set("case", M.case_name);
        rep.set("rolled_program_lines", lines(D.program_rolled));
        PJ ls = PJ::dict();
        for (const auto& [k, v] : L.lift_stats) ls.set(k, v);
        rep.set("lifting", ls);
        rep.set("arithmetic", L.arith_log);
        PJ wo = PJ::dict();
        for (const auto& [w, src] : L.order_sources) wo.set(w, src);
        rep.set("word_orders", wo);
        PJ tables = PJ::dict();
        for (const auto& [km, v] : L.registry.classes)
            for (const auto& [name, T] : v)
            {
                PJ t = PJ::dict();
                t.set("inputs", km.first);
                t.set("outputs", km.second);
                t.set("instances", static_cast<unsigned long long>(L.registry.uses.at(name)));
                PJ tl = PJ::list();
                for (const auto& x : T) tl.push(x);
                t.set("table", tl);
                tables.set(name, t);
            }
        rep.set("tables", tables);
        rep.set("flat_program_lines", lines(D.program_flat));
        // one loop step: its words, iteration constants, partially known varying symbols and counter
        auto step_fields = [](PJ& l, const std::vector<std::string>& names, const LoopStep& s) {
            PJ wn = PJ::list();
            for (const auto& n : names) wn.push(n);
            l.set("words", wn);
            PJ ic = PJ::list();
            for (const auto& [sym, _] : s.iter_vars) ic.push(sym);
            l.set("iteration_constants", ic);
            PJ pk = PJ::list();
            for (const auto& [sym, _] : s.partial) pk.push(sym);
            l.set("partially_known_varying", pk);
            if (s.counter)
            {
                PJ c = PJ::dict();
                c.set("a", s.counter->a);
                c.set("b", s.counter->b);
                c.set("width", s.counter->width);
                l.set("counter", c);
            }
            else l.set("counter", PJ());
        };
        PJ loops = PJ::list();
        for (const auto& lp : D.loops)
        {
            PJ l = PJ::dict();
            l.set("iterations", static_cast<unsigned long long>(lp.phases.size()));
            l.set("first_phase", lp.phases[0]);
            l.set("stride", lp.regular ? PJ(lp.stride) : PJ());
            if (lp.body.size() == 1) step_fields(l, lp.body[0].names, lp.body[0]);
            else
            {
                // an iteration spanning several steps: the union at the top, each step in "body"
                PJ wn = PJ::list(), ic = PJ::list(), pk = PJ::list(), body = PJ::list();
                for (const auto& n : lp.names()) wn.push(n);
                for (size_t q = 0; q < lp.body.size(); ++q)
                {
                    for (const auto& [sym, _] : lp.body[q].iter_vars) ic.push(std::to_string(q) + ":" + sym);
                    for (const auto& [sym, _] : lp.body[q].partial) pk.push(std::to_string(q) + ":" + sym);
                    PJ s = PJ::dict();
                    s.set("offset", lp.body[q].offset);
                    step_fields(s, lp.body[q].names, lp.body[q]);
                    body.push(s);
                }
                l.set("words", wn);
                l.set("iteration_constants", ic);
                l.set("partially_known_varying", pk);
                l.set("counter", PJ());
                l.set("steps_per_iteration", static_cast<unsigned long long>(lp.body.size()));
                l.set("body", body);
            }
            loops.push(l);
        }
        rep.set("loops", loops);
        PJ rp = PJ::dict();
        for (const auto& [k, v] : D.roll_proofs) rp.set(k, static_cast<unsigned long long>(v));
        rep.set("rollup_iteration_checks", rp);
        rep.set("graph_nodes", static_cast<unsigned long long>(M.nodes.size()));
        rep.set("live_nodes", static_cast<unsigned long long>(M.order.size()));
        rep.set("state_values", static_cast<unsigned long long>(D.state_keys.size()));
        rep.set("data_pad_reads", static_cast<unsigned long long>(M.pad_reads.size()));
        PJ obs = PJ::list();
        for (const auto& o : M.observations) obs.push(PJ::list({PJ(o.port), PJ(o.phase)}));
        rep.set("observations", obs);
        rep.set("program_lines", lines(L.program));
        return rep;
    }
}
