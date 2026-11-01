#include "algorithm_recovery/arithmetic.h"
#include <algorithm>
#include <chrono>
#include <cmath>
#include <functional>
#include <random>
#include <stdexcept>

namespace hal::algorithm_recovery
{
    namespace
    {
        using Clock = std::chrono::steady_clock;
        void require(bool condition, const std::string& why) { if (!condition) throw std::runtime_error(why); }
        Integer magnitude(const Integer& x) { return x < 0 ? Integer(-x) : x; }
        Rational magnitude(const Rational& x) { return x < 0 ? -x : x; }
        // boost::rational's bounded-integer overflow guard rejects negative
        // denominators for cpp_int; normalize the sign before construction.
        Rational ratio(const Integer& n, const Integer& d) { require(d != 0, "zero denominator"); return d < 0 ? Rational(-n, -d) : Rational(n, d); }
        Integer floor(const Rational& x) { return floor_div(x.numerator(), x.denominator()); }
        Integer ceil(const Rational& x) { return -floor(-x); }
        Rational modulo(const Rational& x, const Integer& m) { return x - Rational(floor(x / m) * m); }
        unsigned bits(const Integer& x) { return x == 0 ? 0 : boost::multiprecision::msb(magnitude(x)) + 1; }
        Rational rational(const nlohmann::json& row)
        {
            require(row.is_array() && row.size() == 2, "invalid rational encoding");
            const auto integer = [](const auto& v) -> Integer { return Integer(v.is_string() ? v.template get<std::string>() : v.dump()); };
            Integer n = integer(row[0]), d = integer(row[1]); require(d > 0, "nonpositive rational denominator"); return Rational(n, d);
        }
        nlohmann::json encoded(const Rational& value) { return {value.numerator().str(), value.denominator().str()}; }
        std::optional<Rational> simplest(const CoefficientInterval& range)
        {
            if (range.low > range.high || (range.low == range.high && !(range.low_closed && range.high_closed))) return {};
            for (unsigned shift = 0; shift < 64; ++shift)
            {
                const Integer denominator = Integer(1) << shift;
                Integer a = ceil(range.low * denominator), b = floor(range.high * denominator);
                if (!range.low_closed && Rational(a, denominator) == range.low) ++a;
                if (!range.high_closed && Rational(b, denominator) == range.high) --b;
                if (a <= b) return Rational(a <= 0 && b >= 0 ? Integer(0) : magnitude(a) <= magnitude(b) ? a : b, denominator);
            }
            return {};
        }
        struct TimedOut : std::runtime_error { TimedOut() : std::runtime_error("native arithmetic fitting deadline") {} };
        struct Fitter
        {
            const BitVectorProgram& program;
            std::mt19937_64 rng;
            Clock::time_point deadline;
            std::set<std::string> unsigned_inputs;
            size_t evaluations = 0;
            std::vector<unsigned> widths;
            std::vector<std::string> names;
            Fitter(const BitVectorProgram& p, const ArithmeticOptions& options) : program(p), rng(options.seed),
                deadline(Clock::now() + std::chrono::milliseconds(options.fit_timeout_ms)), unsigned_inputs(options.unsigned_inputs)
            {
                for (const auto& [name, width] : p.inputs()) { require(width > 0, "Boolean fitting source unsupported"); names.push_back(name); widths.push_back(width); }
                for (const auto& name : unsigned_inputs) require(std::find(names.begin(), names.end(), name) != names.end(), "unknown unsigned source");
            }
            void check() const { if (Clock::now() >= deadline) throw TimedOut(); }
            Integer evaluate(const std::vector<Integer>& values)
            { if ((evaluations++ & 63) == 0) check(); return program.evaluate(values); }
            Integer random(unsigned width)
            {
                Integer value = 0; for (unsigned shift = 0; shift < width; shift += 64) value |= Integer(rng()) << shift;
                return wrap(value, width);
            }
            Integer interpret(unsigned index, const Integer& value) const
            { return unsigned_inputs.count(names[index]) ? wrap(value, widths[index]) : signed_value(value, widths[index]); }
            bool looks_linear()
            {
                const std::vector<Integer> zero(names.size(), 0); const auto f0 = signed_value(evaluate(zero), program.width());
                for (unsigned trial = 0; trial < 6; ++trial)
                {
                    std::vector<Integer> a, b, sum; bool valid = true;
                    for (auto width : widths)
                    {
                        const auto small = [&]() -> Integer {
                            if (width <= 2) return 0;
                            unsigned range = 1u << std::min(6u, width - 2); return Integer(rng() % (2 * range)) - range;
                        };
                        a.push_back(small()); b.push_back(small()); sum.push_back(a.back() + b.back());
                        if (magnitude(sum.back()) >= (Integer(1) << (width - 1))) valid = false;
                    }
                    if (valid && magnitude(signed_value(evaluate(sum), program.width()) - signed_value(evaluate(a), program.width()) -
                        signed_value(evaluate(b), program.width()) + f0) > names.size() + 2) return false;
                }
                return true;
            }
            CoefficientInterval coefficient_class(const std::vector<Integer>& xs, const std::vector<Integer>& ds, unsigned error = 0)
            {
                require(xs.size() == ds.size(), "probe inventory mismatch");
                const Rational large(Integer("1000000000000000000000000000000"));
                CoefficientInterval result{-large, large, true, true};
                std::vector<std::pair<Integer, Integer>> lower, upper;
                for (unsigned i = 0; i <= xs.size(); ++i)
                {
                    const Integer x = i == xs.size() ? Integer(0) : xs[i], d = i == ds.size() ? Integer(0) : ds[i];
                    lower.emplace_back(-x, d - error); upper.emplace_back(-x, d + 1 + error);
                }
                lower.emplace_back(0, 0); upper.emplace_back(0, 1);
                for (const auto& [a, b] : lower)
                {
                    check();
                    for (const auto& [c, d] : upper)
                    {
                        const Integer k = a - c, v = d - b;
                        if (k == 0) { if (v <= 0) return {Rational(1), Rational(0), true, true}; continue; }
                        const Rational bound = ratio(v, k);
                        if (k > 0 && bound <= result.high) { result.high = bound; result.high_closed = false; }
                        if (k < 0 && bound >= result.low) { result.low = bound; result.low_closed = false; }
                    }
                }
                return result;
            }
            std::optional<Rational> bisect_class(const std::vector<Integer>& xs, const std::vector<Integer>& ds, unsigned error = 0)
            {
                if (xs.empty()) return {};
                const auto feasible = [&](const Rational& c) {
                    Rational lo(0), hi(1);
                    for (unsigned i = 0; i < xs.size(); ++i)
                    {
                        lo = std::max(lo, Rational(ds[i] - error) - c * xs[i]);
                        hi = std::min(hi, Rational(ds[i] + 1 + error) - c * xs[i]);
                        if (lo >= hi) return false;
                    }
                    return true;
                };
                const auto maximum = std::max_element(xs.begin(), xs.end(), [](const auto& a, const auto& b) { return magnitude(a) < magnitude(b); });
                if (*maximum == 0) return {};
                const auto index = std::distance(xs.begin(), maximum);
                Rational left = ratio(ds[index] - 1 - error, *maximum), right = ratio(ds[index] + 1 + error, *maximum);
                if (left > right) std::swap(left, right);
                const auto as_float = [](const Rational& r) { return r.numerator().convert_to<long double>() / r.denominator().convert_to<long double>(); };
                long double a = as_float(left), b = as_float(right);
                std::vector<long double> fx, fd;
                for (unsigned i = 0; i < xs.size(); ++i) { fx.push_back(xs[i].convert_to<long double>()); fd.push_back(ds[i].convert_to<long double>()); }
                const auto gap = [&](long double c) {
                    long double lo = 0, hi = 1;
                    for (unsigned i = 0; i < fx.size(); ++i) { lo = std::max(lo, fd[i] - error - c * fx[i]); hi = std::min(hi, fd[i] + 1 + error - c * fx[i]); }
                    return hi - lo;
                };
                for (unsigned i = 0; i < 100; ++i)
                { check(); const auto m1 = a + (b - a) / 3, m2 = b - (b - a) / 3; if (gap(m1) < gap(m2)) a = m1; else b = m2; }
                std::optional<Rational> center;
                for (unsigned k = 20; k < 56; k += 4)
                {
                    const auto approx = std::round((a + b) / 2 * std::ldexp(1.0L, k));
                    if (!std::isfinite(approx)) return {};
                    Rational c(Integer(approx), Integer(1) << k); if (feasible(c)) { center = c; break; }
                }
                if (!center) return {};
                const auto edge = [&](int direction) {
                    Rational lo = *center, hi = *center + direction;
                    while (feasible(hi)) { check(); hi = *center + 2 * (hi - *center); }
                    for (unsigned i = 0; i < 48; ++i) { check(); const Rational mid = (lo + hi) / 2; if (feasible(mid)) lo = mid; else hi = mid; }
                    return lo;
                };
                const auto low = edge(-1), high = edge(1); auto simple = simplest({low, high, true, true});
                return simple && feasible(*simple) ? simple : center;
            }
            std::optional<ArithmeticModel> fit()
            {
                const unsigned width = program.width(); const Integer modulus = Integer(1) << width;
                std::vector<Integer> zero(names.size(), 0); const auto f0 = signed_value(evaluate(zero), width);
                auto at = [&](unsigned a, const Integer& x) { auto input = zero; input[a] = x; return Integer(signed_value(evaluate(input), width) - f0); };
                std::map<unsigned, Rational> coefficients; std::map<unsigned, CoefficientInterval> classes;
                for (unsigned a = 0; a < names.size(); ++a)
                {
                    check(); const auto wa = widths[a]; std::vector<Integer> xs, ds; std::optional<Rational> c;
                    if (8 < wa && wa <= 12)
                    {
                        std::set<Integer> all; for (unsigned x = 0; x < (1u << wa); ++x) all.insert(interpret(a, x)); all.erase(0);
                        xs.assign(all.begin(), all.end()); for (const auto& x : xs) ds.push_back(at(a, x));
                        for (unsigned error = 0; error <= 3 && !c; ++error) c = bisect_class(xs, ds, error);
                        if (c) { if (*c != 0) coefficients[a] = *c; classes[a] = {*c, *c, true, true}; continue; }
                    }
                    std::set<Integer> probes;
                    if (wa <= 8) for (unsigned x = 0; x < (1u << wa); ++x) probes.insert(interpret(a, x));
                    else
                    {
                        for (unsigned k = 0; k < wa - 1; ++k) { probes.insert(interpret(a, Integer(1) << k)); probes.insert(interpret(a, -(Integer(1) << k))); }
                        probes.insert(interpret(a, Integer(1) << (wa - 1))); probes.insert(interpret(a, (Integer(1) << (wa - 1)) - 1));
                        for (unsigned k = 0; k < 48; ++k) probes.insert(interpret(a, random(wa)));
                    }
                    probes.erase(0); xs.assign(probes.begin(), probes.end()); ds.clear(); for (const auto& x : xs) ds.push_back(at(a, x));
                    CoefficientInterval range;
                    for (unsigned error = 0; error <= 3 && !c; ++error) { range = coefficient_class(xs, ds, error); c = simplest(range); }
                    if (!c)
                    {
                        std::optional<Integer> limit; const Integer bound = Integer(1) << (width > 2 ? width - 2 : 0);
                        for (unsigned i = 0; i < xs.size(); ++i) if (magnitude(ds[i]) >= bound)
                            if (!limit || magnitude(xs[i]) < *limit) limit = magnitude(xs[i]);
                        std::vector<Integer> xs2, ds2;
                        for (unsigned i = 0; i < xs.size(); ++i) if (magnitude(xs[i]) < (Integer(1) << (wa - 1)) && magnitude(ds[i]) < bound && (!limit || magnitude(xs[i]) <= *limit))
                        { xs2.push_back(xs[i]); ds2.push_back(ds[i]); }
                        if (xs2.empty()) return {};
                        for (unsigned error = 0; error <= 3 && !c; ++error) { range = coefficient_class(xs2, ds2, error); c = simplest(range); }
                        if (!c) return {};
                    }
                    if (12 < wa && wa <= 16 && names.size() <= 4)
                    {
                        std::vector<Integer> allx, ally;
                        for (unsigned x = 0; x < (1u << wa); ++x) { allx.push_back(interpret(a, x)); ally.push_back(at(a, allx.back())); }
                        for (unsigned iteration = 0; iteration < 16; ++iteration)
                        {
                            Rational lo(0), hi(1); std::vector<std::pair<Integer, Integer>> bad;
                            for (unsigned i = 0; i < allx.size(); ++i)
                            {
                                if ((i & 255) == 0) check();
                                Rational u = modulo(Rational(ally[i]) - *c * allx[i], modulus); if (u > modulus - 1) u -= modulus;
                                lo = std::max(lo, u); hi = std::min(hi, u + 1);
                                if (lo >= hi)
                                {
                                    if (magnitude(ally[i]) < (Integer(1) << (width > 2 ? width - 2 : 0))) bad.emplace_back(allx[i], ally[i]);
                                    lo = std::max(Rational(0), u); hi = std::min(Rational(1), u + 1);
                                }
                                if (bad.size() >= 16) break;
                            }
                            if (bad.empty()) break;
                            for (const auto& [x, d] : bad) { xs.push_back(x); ds.push_back(d); }
                            std::vector<Integer> nw_x, nw_d;
                            for (unsigned i = 0; i < xs.size(); ++i) if (magnitude(ds[i]) < (Integer(1) << (width > 2 ? width - 2 : 0))) { nw_x.push_back(xs[i]); nw_d.push_back(ds[i]); }
                            c = bisect_class(nw_x, nw_d); if (!c) return {};
                        }
                    }
                    if (wa > 8)
                    {
                        for (unsigned iteration = 0; iteration < 12; ++iteration)
                        {
                            std::vector<Integer> xr, dr;
                            for (unsigned i = 0; i < 400; ++i) { auto x = interpret(a, random(wa)); if (x != 0) { xr.push_back(x); dr.push_back(at(a, x)); } }
                            Rational lo(0), hi(1);
                            const auto tighten = [&](const auto& xx, const auto& dd) {
                                for (unsigned i = 0; i < xx.size(); ++i) { lo = std::max(lo, Rational(dd[i]) - *c * xx[i]); hi = std::min(hi, Rational(dd[i] + 1) - *c * xx[i]); }
                            };
                            tighten(xs, ds); tighten(xr, dr); if (lo < hi) break;
                            std::vector<unsigned> bad;
                            for (unsigned i = 0; i < xr.size() && bad.size() < 24; ++i)
                                if (!(Rational(dr[i]) - *c * xr[i] < 1 && Rational(dr[i] + 1) - *c * xr[i] > 0)) bad.push_back(i);
                            if (bad.empty()) for (unsigned i = 0; i < std::min<size_t>(24, xr.size()); ++i) bad.push_back(i);
                            for (auto i : bad) { xs.push_back(xr[i]); ds.push_back(dr[i]); }
                            auto candidate_range = coefficient_class(xs, ds); auto next = simplest(candidate_range);
                            if (!next) break; range = candidate_range; c = next;
                        }
                    }
                    if (*c != 0) coefficients[a] = *c; classes[a] = range;
                }
                std::vector<std::vector<Integer>> samples; std::vector<Integer> outputs;
                for (unsigned trial = 0; trial < 3000; ++trial)
                {
                    std::vector<Integer> sample; for (unsigned i = 0; i < names.size(); ++i) sample.push_back(interpret(i, random(widths[i])));
                    outputs.push_back(evaluate(sample)); samples.push_back(std::move(sample));
                }
                const auto joint = [&](const std::map<unsigned, Rational>& coeffs) -> std::optional<Rational> {
                    Rational lo(0), hi(1);
                    for (unsigned i = 0; i < samples.size(); ++i)
                    {
                        if ((i & 255) == 0) check(); Rational linear(0);
                        for (const auto& [key, coefficient] : coeffs) linear += coefficient * samples[i][key];
                        Rational u = modulo(Rational(outputs[i] - f0) - linear, modulus); if (u > modulus - 1) u -= modulus;
                        lo = std::max(lo, u); hi = std::min(hi, u + 1); if (lo >= hi) return {};
                    }
                    return simplest({lo, hi, true, false});
                };
                auto r = joint(coefficients); std::vector<unsigned> narrow;
                for (unsigned i = 0; i < widths.size(); ++i) if (widths[i] <= 2) narrow.push_back(i);
                if (!r && !narrow.empty() && narrow.size() <= 6)
                {
                    Integer denominator = 2;
                    for (const auto& [key, c] : coefficients) if (widths[key] > 2) denominator = std::max(denominator, c.denominator());
                    std::map<unsigned, std::vector<Rational>> candidates;
                    for (auto key : narrow)
                    {
                        const auto& range = classes.at(key); const Rational lo = std::max(range.low, Rational(-4)), hi = std::min(range.high, Rational(4));
                        for (Integer k = ceil(lo * denominator), last = floor(hi * denominator); k <= last; ++k)
                        {
                            check(); Rational value(k, denominator);
                            if ((value > range.low || (range.low_closed && value == range.low)) && (value < range.high || (range.high_closed && value == range.high))) candidates[key].push_back(value);
                        }
                    }
                    auto trial = coefficients;
                    std::function<bool(unsigned)> search = [&](unsigned index) {
                        check(); if (index == narrow.size()) { auto found = joint(trial); if (!found) return false; coefficients = trial; r = found; return true; }
                        const auto key = narrow[index];
                        for (const auto& value : candidates[key]) { if (value == 0) trial.erase(key); else trial[key] = value; if (search(index + 1)) return true; }
                        return false;
                    };
                    search(0);
                }
                ArithmeticModel result; result.width = width; result.unsigned_inputs = unsigned_inputs;
                for (const auto& [key, coefficient] : coefficients) result.coefficients.emplace(names[key], coefficient);
                result.constant = Rational(f0) + (r ? *r : Rational(0));
                if (r)
                {
                    const bool integral = std::all_of(coefficients.begin(), coefficients.end(), [](const auto& pair) { return pair.second.denominator() == 1; });
                    result.mode = integral && *r == 0 ? "exact" : "floor"; return result;
                }
                std::vector<Rational> linear; std::vector<Integer> errors; Rational span(0); Integer error = 0;
                for (unsigned i = 0; i < samples.size(); ++i)
                {
                    Rational value(0); for (const auto& [key, c] : coefficients) value += c * samples[i][key];
                    linear.push_back(value); span = std::max(span, magnitude(value));
                    const Integer delta = signed_value(outputs[i] - floor(value + f0), width);
                    errors.push_back(magnitude(delta)); error = std::max(error, errors.back());
                }
                if (span == 0) span = Rational(1);
                std::optional<Integer> central;
                for (unsigned i = 0; i < samples.size(); ++i)
                {
                    bool band = magnitude(linear[i]) > span / 16 && magnitude(linear[i]) < span / 4;
                    for (unsigned j = 0; j < names.size(); ++j) if (magnitude(samples[i][j]) >= (Integer(1) << (widths[j] > 2 ? widths[j] - 2 : 0))) band = false;
                    if (band && (!central || errors[i] > *central)) central = errors[i];
                }
                if (error <= names.size() + 2 && central && *central == error)
                {
                    result.mode = "quant"; result.quant_bound = error;
                    for (const auto& [key, c] : coefficients) result.coefficient_classes.emplace(names[key], classes.at(key));
                    return result;
                }
                return {};
            }
        };
    }
    nlohmann::json ArithmeticModel::to_json() const
    {
        nlohmann::json row = {{"width", width}, {"mode", mode}, {"constant", encoded(constant)}, {"coefficients", nlohmann::json::object()}, {"unsigned", unsigned_inputs}};
        for (const auto& [name, value] : coefficients) row["coefficients"][name] = encoded(value);
        if (mode == "quant")
        {
            row["quant_bound"] = quant_bound.str(); row["coefficient_classes"] = nlohmann::json::object();
            for (const auto& [name, range] : coefficient_classes) row["coefficient_classes"][name] = {encoded(range.low), range.low_closed, encoded(range.high), range.high_closed};
        }
        return row;
    }
    ArithmeticModel ArithmeticModel::from_json(const nlohmann::json& row)
    {
        ArithmeticModel value; value.width = row.at("width"); require(value.width > 0, "invalid model width"); value.mode = row.at("mode");
        require(value.mode == "exact" || value.mode == "floor" || value.mode == "round" || value.mode == "quant", "invalid arithmetic model mode");
        value.constant = rational(row.at("constant")); value.unsigned_inputs = row.at("unsigned").get<std::set<std::string>>();
        for (auto it = row.at("coefficients").begin(); it != row.at("coefficients").end(); ++it) value.coefficients.emplace(it.key(), rational(it.value()));
        if (value.mode == "quant")
        {
            value.quant_bound = Integer(row.at("quant_bound").get<std::string>()); require(value.quant_bound >= 0, "negative quantization bound");
            for (auto it = row.at("coefficient_classes").begin(); it != row.at("coefficient_classes").end(); ++it)
                value.coefficient_classes.emplace(it.key(), CoefficientInterval{rational(it.value().at(0)), rational(it.value().at(2)), it.value().at(1), it.value().at(3)});
        }
        return value;
    }
    z3::expr ArithmeticModel::expression(const std::map<std::string, z3::expr>& values, z3::context& ctx) const
    {
        require(width > 0 && (mode == "exact" || mode == "floor" || mode == "round"), "approximate/invalid arithmetic model is not executable");
        const Rational offset = constant + (mode == "round" ? Rational(1, 2) : Rational(0)); Integer denominator = offset.denominator();
        for (const auto& [name, coefficient] : coefficients) denominator = std::max(denominator, coefficient.denominator());
        require((denominator & (denominator - 1)) == 0, "non-dyadic arithmetic model");
        for (const auto& [name, c] : coefficients) require(denominator % c.denominator() == 0, "non-dyadic coefficient");
        require(denominator % offset.denominator() == 0, "non-dyadic constant");
        const Integer numerator = offset.numerator() * (denominator / offset.denominator()); Integer bound = magnitude(numerator);
        unsigned accumulator_width = width;
        for (const auto& [name, value] : values) { require(value.is_bv() && &value.ctx() == &ctx, "invalid model source"); accumulator_width = std::max(accumulator_width, value.get_sort().bv_size() + 1); }
        for (const auto& name : unsigned_inputs) require(values.count(name), "missing unsigned source");
        for (const auto& [name, c] : coefficients) bound += magnitude(Integer(c.numerator() * (denominator / c.denominator()))) * (Integer(1) << values.at(name).get_sort().bv_size());
        accumulator_width = std::max(accumulator_width, bits(bound) + 2);
        auto literal = [&](const Integer& v) { return ctx.bv_val(wrap(v, accumulator_width).str().c_str(), accumulator_width); };
        z3::expr sum = literal(numerator);
        for (const auto& [name, c] : coefficients)
        {
            const auto& value = values.at(name); const auto extension = accumulator_width - value.get_sort().bv_size();
            sum = sum + literal(c.numerator() * (denominator / c.denominator())) * (unsigned_inputs.count(name) ? z3::zext(value, extension) : z3::sext(value, extension));
        }
        return z3::ashr(sum, bits(denominator) - 1).extract(width - 1, 0).simplify();
    }
    ArithmeticResult verify_arithmetic(const z3::expr& raw, const ArithmeticModel& proposal, const ArithmeticOptions& options)
    {
        ArithmeticResult result; result.proposal = ArithmeticModel::from_json(proposal.to_json());
        if (proposal.mode == "quant") { result.status = "RAW_QUANT_CANDIDATE"; return result; }
        require(raw.is_bv() && raw.get_sort().bv_size() == proposal.width, "fit output width mismatch");
        auto& ctx = raw.ctx(); BitVectorProgram program(raw); std::map<std::string, z3::expr> values;
        for (const auto& [name, width] : program.inputs()) { require(width > 0, "Boolean fit source"); values.emplace(name, ctx.bv_const(name.c_str(), width)); }
        z3::solver solver(ctx); solver.set("timeout", options.proof_timeout_ms);
        if (options.proof_resource_limit) solver.set("rlimit", options.proof_resource_limit);
        solver.add(raw != result.proposal->expression(values, ctx)); result.proof_query = solver.to_smt2();
        const auto status = solver.check();
        result.status = status == z3::unsat ? "FIT_PROVED" : status == z3::sat ? "FIT_REFUTED" : "FIT_UNKNOWN";
        if (status == z3::unknown) result.reason = solver.reason_unknown();
        return result;
    }
    ArithmeticResult recover_arithmetic(const z3::expr& raw, bool numeric_order_known, const ArithmeticOptions& options)
    {
        ArithmeticResult result; if (!numeric_order_known) { result.status = "RAW_UNORDERED"; return result; }
        require(raw.is_bv(), "non-BV arithmetic output"); BitVectorProgram program(raw); Fitter fitter(program, options);
        try
        {
            fitter.check();
            // Unknown word storage coordinates can permute a physical bit from
            // the sign position into an interior position. The probe heuristic
            // may miss the resulting wrapping staircase. A literal BV slice is
            // directly a dyadic projection in ANY storage coordinate; propose
            // it structurally, without inferring/changing a numeric bit order.
            if (raw.decl().decl_kind() == Z3_OP_EXTRACT && raw.arg(0).is_const() && raw.arg(0).decl().decl_kind() == Z3_OP_UNINTERPRETED)
            {
                const unsigned low = Z3_get_decl_int_parameter(raw.ctx(), raw.decl(), 1);
                ArithmeticModel projection; projection.width = program.width(); projection.mode = low ? "floor" : "exact";
                projection.coefficients.emplace(raw.arg(0).decl().name().str(), Rational(1, Integer(1) << low));
                projection.unsigned_inputs = options.unsigned_inputs;
                return verify_arithmetic(raw, projection, options);
            }
            // As in InvFold's unsigned-lane retry, signed small probes must not
            // screen out an explicitly unsigned source at its wrap boundary.
            if (!options.unsigned_inputs.empty() || fitter.looks_linear()) result.proposal = fitter.fit();
            if (result.proposal) result = verify_arithmetic(raw, *result.proposal, options);
        }
        catch (const TimedOut& error) { result.status = "RAW_FIT_TIMEOUT"; result.reason = error.what(); result.proposal.reset(); }
        result.evaluations = fitter.evaluations; return result;
    }
    ArithmeticResult ArithmeticRecovery::recover(const z3::expr& raw, bool numeric_order_known)
    {
        if (!numeric_order_known) { ArithmeticResult result; result.status = "RAW_UNORDERED"; return result; }
        auto& ctx = raw.ctx(); BitVectorProgram program(raw); z3::expr_vector from(ctx), to(ctx);
        auto options = m_options; options.unsigned_inputs.clear(); std::map<std::string, std::string> names;
        std::string key = std::to_string(program.width());
        for (const auto& [name, width] : program.inputs())
        {
            const auto canonical = "recovery_input_" + std::to_string(names.size());
            require(width > 0, "Boolean arithmetic source"); from.push_back(ctx.bv_const(name.c_str(), width)); to.push_back(ctx.bv_const(canonical.c_str(), width));
            names.emplace(canonical, name); key += ":" + std::to_string(width);
            if (m_options.unsigned_inputs.count(name)) { options.unsigned_inputs.insert(canonical); key += 'u'; }
        }
        require(options.unsigned_inputs.size() == m_options.unsigned_inputs.size(), "unknown unsigned source in cached fit");
        auto canonical = raw; canonical = canonical.substitute(from, to); key += "\n" + canonical.to_string();
        const bool reused = m_cache.count(key);
        if (!reused) { ++m_calls; m_cache.emplace(key, recover_arithmetic(canonical, true, options)); }
        else ++m_hits;
        auto result = m_cache.at(key); result.reused = reused; result.proof_symbols = names;
        if (reused) result.evaluations = 0;
        if (result.proposal)
        {
            auto& model = *result.proposal; std::map<std::string, Rational> coefficients;
            for (const auto& [name, value] : model.coefficients) coefficients.emplace(names.at(name), value);
            model.coefficients = std::move(coefficients); std::set<std::string> unsigned_sources;
            for (const auto& name : model.unsigned_inputs) unsigned_sources.insert(names.at(name)); model.unsigned_inputs = std::move(unsigned_sources);
            std::map<std::string, CoefficientInterval> classes;
            for (const auto& [name, range] : model.coefficient_classes) classes.emplace(names.at(name), range); model.coefficient_classes = std::move(classes);
        }
        return result;
    }
}
