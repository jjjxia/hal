#include "trace_validation.h"
#include <fstream>
#include <sstream>

namespace program_recovery
{
    TraceValidator::TraceValidator(const EventGraph& model, const ProgramBuilder& dec, const std::string& trace_dir) : M(model), D(dec)
    {
        std::ifstream mf(trace_dir + "/native_trace.json");
        require(bool(mf), "cannot read native_trace.json");
        const Json meta = Json::parse(mf);
        size_t i = 0;
        for (const auto& b : meta.at("observed")) pos[b.get<long long>()] = i++;
        std::ifstream tf(trace_dir + "/native_trace.txt");
        require(bool(tf), "cannot read native_trace.txt");
        std::string line;
        while (std::getline(tf, line))
        {
            std::istringstream ss(line);
            std::string pre, post;
            ss >> pre >> post;
            rows.emplace_back(pre, post);
        }
    }

    int TraceValidator::bitval(long long b, long long t, bool post) const
    {
        const std::string& r = post ? rows.at(static_cast<size_t>(t)).second : rows.at(static_cast<size_t>(t)).first;
        const char c = r.at(r.size() - 1 - pos.at(b));
        return c == '0' ? 0 : c == '1' ? 1 : -1;
    }

    std::optional<BigInt> TraceValidator::wordval(const std::string& name, long long t, bool post) const
    {
        BigInt v = 0;
        unsigned i = 0;
        for (const auto& b : M.words().at(name).at("bits"))
        {
            const int x = bitval(std::stoll(b.get<std::string>().substr(1)), t, post);
            if (x < 0) return std::nullopt;
            v |= BigInt(x) << i++;
        }
        return v;
    }

    Validation TraceValidator::validate(const std::string& text) const
    {
        using namespace interp;
        const Module mod = parse_module(text);
        Interpreter I(mod);
        Validation res;
        const long long P = M.P;
        const long long training_end = M.contract.at("training_end_exclusive").get<long long>();
        const long long theta = M.origin + M.shift;
        const long long nrows = static_cast<long long>(rows.size());
        for (long long start = theta; start < nrows - 2 * P; start += P)
        {
            if (start < training_end) continue;
            Value pads; pads.k = VK::dict; pads.dict = std::make_shared<DictObj>();
            bool unknown = false;
            for (const auto& [base, ph] : M.pad_reads)
            {
                const long long b = std::stoll(M.inputs().at(base).at("bits").at(0).get<std::string>().substr(1));
                const int x = bitval(b, start + ph, false);
                if (x < 0) unknown = true;
                pads.dict->set(Value::tuple({Value::string(base), Value::integer(ph)}), x < 0 ? Value::none() : Value::integer(x));
            }
            Value state; state.k = VK::dict; state.dict = std::make_shared<DictObj>();
            bool ok_state = true;
            for (const auto& [name, ph, d] : D.state_keys)
            {
                const auto v = wordval(name, start + ph - d * P, true);
                if (!v) ok_state = false;
                state.dict->set(Value::tuple({Value::string(name), Value::integer(ph), Value::integer(d)}), v ? Value::integer(*v) : Value::none());
            }
            if (!ok_state || unknown) { res.inc("skipped_unknown"); continue; }
            const auto [outs, _ns] = I.transaction(pads, state);
            for (const auto& [key, value] : outs.dict->entries)
            {
                const std::string port = (*key.items)[0].s;
                const long long ph = static_cast<long long>((*key.items)[1].i);
                std::optional<BigInt> expected = BigInt(0);
                unsigned i = 0;
                for (const auto& p : M.ports.at(port))
                {
                    const int x = bitval(p.at("physical_bit").get<long long>(), start + ph, false);
                    if (x < 0) { expected.reset(); break; }
                    *expected |= BigInt(x) << i++;
                }
                if (!expected) { res.inc("unknown_observation"); continue; }
                res.inc("compared");
                require(value.k == VK::integer, "program output is not an integer");
                if (value.i != *expected)
                {
                    res.inc("mismatch");
                    if (res.first_failure.kind == PJ::Kind::null)
                    {
                        PJ f = PJ::dict();
                        f.set("start", start);
                        f.set("port", port);
                        f.set("phase", ph);
                        f.set("program", value.is_bool ? PJ(value.i != 0) : PJ(value.i));
                        f.set("trace", *expected);
                        res.first_failure = f;
                    }
                }
            }
            res.inc("transactions");
        }
        return res;
    }

    PJ program_report(const EventGraph& M, const ProgramBuilder& D, const ProgramLifter& L, const Validation& flat, const Validation& rolled, const Validation& lifted)
    {
        auto ok = [](const Validation& r) { return r.get("compared") != 0 && r.get("mismatch") == 0; };
        auto lines = [](const std::string& s) { return static_cast<long long>(std::count(s.begin(), s.end(), '\n')); };
        PJ rep = PJ::dict();
        rep.set("case", M.case_name);
        rep.set("status", ok(lifted) && ok(flat) && ok(rolled) ? "PASS" : "FAIL");
        rep.set("rolled_validation", rolled.to_pj());
        rep.set("rolled_first_failure", rolled.first_failure);
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
        rep.set("validation", lifted.to_pj());
        rep.set("first_failure", lifted.first_failure);
        rep.set("flat_validation", flat.to_pj());
        rep.set("flat_first_failure", flat.first_failure);
        rep.set("flat_program_lines", lines(D.program_flat));
        PJ loops = PJ::list();
        for (const auto& lp : D.loops)
        {
            PJ l = PJ::dict();
            l.set("iterations", static_cast<unsigned long long>(lp.phases.size()));
            l.set("first_phase", lp.phases[0]);
            l.set("stride", lp.regular ? PJ(lp.stride) : PJ());
            PJ wn = PJ::list();
            for (const auto& n : lp.names) wn.push(n);
            l.set("words", wn);
            PJ ic = PJ::list();
            for (const auto& [s, _] : lp.iter_vars) ic.push(s);
            l.set("iteration_constants", ic);
            PJ pk = PJ::list();
            for (const auto& [s, _] : lp.partial) pk.push(s);
            l.set("partially_known_varying", pk);
            if (lp.counter)
            {
                PJ c = PJ::dict();
                c.set("a", lp.counter->a);
                c.set("b", lp.counter->b);
                c.set("width", lp.counter->width);
                l.set("counter", c);
            }
            else l.set("counter", PJ());
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
        rep.set("validated_on", "held-out transactions (start >= training_end_exclusive), state from the trace");
        return rep;
    }
}
