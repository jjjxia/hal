#include "algorithm_recovery/certify.h"
#include "artifacts.h"
#include <chrono>
#include <functional>
#include <numeric>
#include <algorithm>
#include <iostream>

namespace hal::algorithm_recovery
{
    using Json = nlohmann::json;
    using artifacts::require;

    namespace
    {
        struct FFInfo { PhysicalBit q, c, d, e, r; bool async, set, falling, io; };
        struct Failure { std::string kind; PhysicalBit bit; size_t event; std::string status; };
    }

    Result<ScheduleCertificate> certify_schedule(OriginalCircuit& original, const Observations& trace, const PadProtocol& protocol,
                                                 z3::context& ctx, const std::filesystem::path& output, size_t horizon, unsigned max_rounds)
    {
        try
        {
            const auto begin = std::chrono::steady_clock::now();
            const auto out = std::filesystem::absolute(output);
            require(!std::filesystem::exists(out) || std::filesystem::is_empty(out), "certificate output directory must be empty");
            std::filesystem::create_directories(out);
            const auto events = trace.pre.size();
            const size_t H = std::min(horizon, events - 1);
            require(H > 8, "certificate horizon too short");
            std::vector<FFInfo> ffs;
            for (const auto& [q, ff] : original.flip_flops)
                ffs.push_back({q, ff.pins.at("C"), ff.pins.at("D"), ff.pins.count("E") ? ff.pins.at("E") : -2,
                               ff.pins.count("R") ? ff.pins.at("R") : ff.pins.count("S") ? ff.pins.at("S") : -1,
                               !ff.synchronous_reset && (ff.pins.count("R") || ff.pins.count("S")), ff.pins.count("S") != 0, ff.falling, ff.type == "$dff"});
            std::set<PhysicalBit> controls;
            for (const auto bit : original.controls) if (bit >= 0) controls.insert(bit);
            size_t derived_clocks = 0;
            for (const auto bit : original.clocks) if (bit >= 0 && original.combinational(bit)) { controls.insert(bit); ++derived_clocks; }
            std::vector<PhysicalBit> pads(protocol.non_data.begin(), protocol.non_data.end());
            std::vector<PhysicalBit> ram_reads;
            for (const auto bit : original.leaves()) if (!original.inputs.count(bit) && !original.flip_flops.count(bit)) ram_reads.push_back(bit);
            const std::set<PhysicalBit> ram_set(ram_reads.begin(), ram_reads.end());

            const auto boolean = [&](PhysicalBit bit) { return original.function(bit); };
            std::map<std::pair<PhysicalBit, bool>, z3::expr> next_cache;
            const auto next = [&](const FFInfo& f, bool edge) -> z3::expr {
                const auto key = std::make_pair(f.q, edge);
                if (auto it = next_cache.find(key); it != next_cache.end()) return it->second;
                const auto q = boolean(f.q), d = boolean(f.d), e = boolean(f.e), r = boolean(f.r), set = ctx.bool_val(f.set);
                z3::expr clocked = edge ? z3::ite(e, f.async ? d : z3::ite(r, set, d), q) : q;
                if (f.async) clocked = z3::ite(r, set, clocked);
                clocked = clocked.simplify(); next_cache.emplace(key, clocked); return clocked;
            };

            ForcedTaint forced; Json rounds = Json::array(); ScheduleCertificate result;
            for (unsigned round = 0;; ++round)
            {
                require(round < max_rounds, "schedule refinement did not converge");
                auto taint = compute_taint(original, trace, protocol, forced);
                if (taint.is_error()) return ERR(taint.get_error().get());
                result.taint = std::move(taint.get());
                const auto& T = result.taint;
                // Candidate relation: keep stored signals whose recorded (taint, value)
                // sequence repeats on the tail of the horizon; drop the rest from R by
                // tainting them everywhere. Dropping only weakens R (free-running timers).
                const size_t tail = H / 2;
                const auto period_of = [&](const std::function<int(size_t)>& key) -> size_t {
                    for (size_t p = 1; 3 * p <= H - tail; ++p)
                    {
                        bool ok = true;
                        for (size_t t = tail; t + p <= H && ok; ++t) ok = key(t) == key(t + p);
                        if (ok) return p;
                    }
                    return 0;
                };
                const size_t P0 = period_of([&](size_t t) {
                    int k = 0; for (const auto pad : pads) k = k * 9 + (trace.value(pad, t) + 1) * 3 + trace.value(pad, t, true) + 1; return k; });
                require(P0 > 0, "protocol pads are not periodic on the horizon tail");
                std::vector<std::pair<size_t, PhysicalBit>> kept; std::vector<PhysicalBit> drops;
                std::vector<PhysicalBit> stored; for (const auto& f : ffs) stored.push_back(f.q);
                stored.insert(stored.end(), ram_reads.begin(), ram_reads.end());
                for (const auto bit : stored)
                {
                    if (forced.periodic.count({bit, 0, 1, 0})) continue;
                    bool any = false; for (size_t t = tail; t <= H && !any; ++t) any = T.known(bit, t) >= 0;
                    if (!any) continue;
                    const auto p = period_of([&](size_t t) { return T.known(bit, t); });
                    if (!p) drops.push_back(bit); else kept.push_back({p, bit});
                }
                std::sort(kept.begin(), kept.end());
                size_t P = P0;
                for (size_t i = 0; i < kept.size(); ++i)
                {
                    const auto next = std::lcm(P, kept[i].first);
                    if (next > H / 4) { for (size_t j = i; j < kept.size(); ++j) drops.push_back(kept[j].second); break; }
                    P = next;
                }
                if (!drops.empty())
                {
                    for (const auto bit : drops) forced.periodic.insert({bit, 0, 1, 0});
                    rounds.push_back({{"round", round}, {"dropped_from_relation", drops.size()}, {"protocol_period", P0}});
                    std::cout << "CERT round " << round << " dropped " << drops.size() << " non-periodic stored signals from R" << std::endl;
                    continue;
                }
                std::vector<std::vector<uint8_t>> sig(H + 1); std::vector<size_t> hash(H + 1);
                for (size_t t = 0; t <= H; ++t)
                {
                    auto& s = sig[t];
                    for (const auto pad : pads) { s.push_back(trace.value(pad, t) + 1); s.push_back(trace.value(pad, t, true) + 1); }
                    for (const auto& f : ffs) s.push_back(T.known(f.q, t) + 1);
                    for (const auto bit : controls) s.push_back(T.known(bit, t) + 1);
                    for (const auto bit : ram_reads) s.push_back(T.known(bit, t) + 1);
                    size_t h = s.size(); for (const auto v : s) h = h * 1000003u ^ v; hash[t] = h;
                }
                size_t t0 = 0;
                {
                    size_t last = 0; bool mismatch = false;
                    for (size_t t = H - P + 1; t-- > 0;)
                        if (hash[t] != hash[t + P] || sig[t] != sig[t + P]) { last = t; mismatch = true; break; }
                    t0 = mismatch ? last + 1 : 0;
                    if (H - t0 < 3 * P) P = 0;
                }
                Json summary{{"round", round}, {"forced_taints", forced.size()}, {"protocol_period", P0}, {"relation_signals_kept", kept.size()}};
                if (!P)
                {
                    summary["status"] = "NO_PERIODIC_SCHEDULE"; rounds.push_back(summary);
                    result.report = {{"status", "NO_PERIODIC_SCHEDULE"}, {"rounds", rounds}};
                    artifacts::write(out / "certificate.json", result.report.dump(2) + '\n');
                    return OK(std::move(result));
                }
                summary["origin"] = t0; summary["period"] = P;
                std::vector<Failure> failures;
                const auto round_dir = out / ("round_" + std::to_string(round)); std::filesystem::create_directories(round_dir);
                // Initiation: untainted power-up values of iCE40 FF primitives are 0
                // unless an untainted asynchronous control is asserted in frame 0.
                for (const auto& f : ffs)
                {
                    const auto v = T.known(f.q, 0); if (v < 0) continue;
                    const bool expected = f.async && T.known(f.r, 0) == 1 ? f.set : false;
                    if (f.io || v != int(expected)) failures.push_back({"initiation", f.q, 0, "MISMATCH"});
                }
                uint64_t goals_proved = 0, queries = 0; Json query_files = Json::array(); size_t untainted_targets = 0;
                for (size_t t = 0; t < t0 + P; ++t)
                {
                    z3::solver solver(ctx); solver.set("timeout", 120000u);
                    for (const auto bit : original.leaves())
                    {
                        if (ram_set.count(bit)) continue; // read latches stay free
                        const auto v = T.known(bit, t); if (v >= 0) solver.add(boolean(bit) == ctx.bool_val(v == 1));
                    }
                    std::vector<std::pair<Failure, z3::expr>> goals;
                    for (const auto& f : ffs)
                    {
                        const auto target = T.known(f.q, t + 1); if (target < 0) continue;
                        ++untainted_targets;
                        if (f.async && T.known(f.r, t + 1) == 1)
                        { if (target != int(f.set)) failures.push_back({"async_reset_value", f.q, t + 1, "MISMATCH"}); continue; }
                        const int before = trace.value(f.c, t), after = trace.value(f.c, t, true);
                        if (T.known(f.c, t) < 0 || before < 0 || after < 0) { failures.push_back({"clock", f.q, t + 1, "UNKNOWN_EDGE"}); continue; }
                        const bool edge = f.falling ? (before == 1 && after == 0) : (before == 0 && after == 1);
                        goals.push_back({{"update", f.q, t + 1, ""}, next(f, edge) == ctx.bool_val(target == 1)});
                    }
                    for (const auto bit : controls)
                    {
                        const auto v = T.known(bit, t); if (v < 0) continue;
                        goals.push_back({{"control", bit, t, ""}, boolean(bit) == ctx.bool_val(v == 1)});
                    }
                    while (!goals.empty())
                    {
                        z3::expr_vector all(ctx); for (const auto& g : goals) all.push_back(g.second);
                        solver.push(); solver.add(!z3::mk_and(all)); ++queries; const auto status = solver.check();
                        if (status == z3::unsat)
                        {
                            if (t < 4 || t + 4 >= t0 + P || t % 64 == 0)
                            {
                                const auto file = round_dir / ("step_" + std::to_string(t) + ".smt2"); artifacts::write(file, solver.to_smt2());
                                query_files.push_back({{"event", t}, {"query", file.string()}, {"sha256", artifacts::file_hash(file)}});
                            }
                            solver.pop(); goals_proved += goals.size(); break;
                        }
                        if (status == z3::unknown)
                        { for (auto& g : goals) { g.first.status = "UNKNOWN"; failures.push_back(g.first); } solver.pop(); goals.clear(); break; }
                        const auto model = solver.get_model(); std::vector<std::pair<Failure, z3::expr>> kept;
                        for (auto& g : goals)
                        {
                            if (model.eval(g.second, true).is_false()) { g.first.status = "SAT"; failures.push_back(g.first); }
                            else kept.push_back(g);
                        }
                        solver.pop(); require(kept.size() < goals.size(), "model did not falsify any goal");
                        goals = std::move(kept);
                    }
                }
                summary["steps"] = t0 + P; summary["queries"] = queries; summary["goals_proved"] = goals_proved;
                summary["failures"] = failures.size(); summary["untainted_ff_targets"] = untainted_targets;
                Json failure_rows = Json::array();
                for (const auto& f : failures) failure_rows.push_back({f.kind, f.bit, f.event, f.status});
                summary["failure_rows"] = failure_rows.size() > 200 ? Json(failure_rows.size()) : failure_rows;
                rounds.push_back(summary);
                std::cout << "CERT round " << round << " origin=" << t0 << " period=" << P << " goals=" << goals_proved << " failures=" << failures.size() << std::endl;
                if (failures.empty())
                {
                    result.certified = true; result.origin = t0; result.period = P; result.forced = forced;
                    uint64_t untainted_ff = 0, phase_controls = 0;
                    for (size_t t = t0; t < t0 + P; ++t)
                    {
                        for (const auto& f : ffs) untainted_ff += T.known(f.q, t) >= 0;
                        for (const auto bit : controls) phase_controls += T.known(bit, t) >= 0;
                    }
                    result.report = {{"status", "PROTOCOL_SCHEDULE_CERTIFIED"}, {"origin", t0}, {"period", P}, {"horizon", H},
                        {"flip_flops", ffs.size()}, {"mean_untainted_ff_per_phase", double(untainted_ff) / P},
                        {"mean_concretized_controls_per_phase", double(phase_controls) / P}, {"control_nets", controls.size()},
                        {"derived_clock_nets", derived_clocks}, {"ram_read_latches_free", ram_reads.size()},
                        {"obligations", {{"initiation", "primitive power-up values"}, {"prefix_steps", t0}, {"periodic_steps", P}, {"goals_proved", goals_proved}, {"queries", queries}}},
                        {"rounds", rounds}, {"saved_queries", query_files}, {"taint", T.report},
                        {"seconds", std::chrono::duration<double>(std::chrono::steady_clock::now() - begin).count()},
                        {"scope", "Under the supplied pad protocol, every admitted run satisfies R_t for all t (prefix steps then period by induction). "
                                  "Each step discharges every untainted FF control value, so conditional SSE word proofs specialised on those values hold for all data. "
                                  "Trusted: original-JSON LUT/carry/FF semantics, single-clock-edge-per-event model, edges of derived clocks taken from untainted observations, Z3."}};
                    // Per-event untainted values of every observed net, for independent
                    // cross-run checks of Lemma 1 against other admitted traces.
                    {
                        std::vector<PhysicalBit> observed(trace.positions.size());
                        for (const auto& [bit, pos] : trace.positions) observed[pos] = bit;
                        std::ofstream dump(out / "taint_observed.txt"); require(bool(dump), "cannot write taint dump");
                        for (size_t t = 0; t < events; ++t)
                        {
                            std::string row(observed.size(), '?');
                            for (size_t i = 0; i < observed.size(); ++i)
                                if (T.column.count(observed[i])) { const auto v = T.known(observed[i], t); row[observed.size() - 1 - i] = v < 0 ? 't' : char('0' + v); }
                            dump << row << '\n';
                        }
                        dump.close(); require(bool(dump), "taint dump write failed");
                        result.report["taint_observed_sha256"] = artifacts::file_hash(out / "taint_observed.txt");
                    }
                    artifacts::write(out / "certificate.json", result.report.dump(2) + '\n');
                    return OK(std::move(result));
                }
                for (const auto& f : failures)
                {
                    if (f.event >= t0) forced.periodic.insert({f.bit, t0, P, (f.event - t0) % P});
                    else forced.events[f.bit].insert(f.event);
                }
            }
        }
        catch (const std::exception& error) { return ERR(error.what()); }
    }
}
