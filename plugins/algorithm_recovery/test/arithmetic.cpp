#include "algorithm_recovery/arithmetic.h"
#include <iostream>
#include <random>

using namespace hal::algorithm_recovery;
static void check(bool ok, const std::string& why) { if (!ok) throw std::runtime_error(why); }
template<class F> static void rejects(F action)
{ bool failed = false; try { action(); } catch (const std::exception&) { failed = true; } check(failed, "invalid model accepted"); }
static z3::expr parse(z3::context& ctx, const std::string& term)
{
    return ctx.parse_string(("(declare-const x (_ BitVec 4))(declare-const y (_ BitVec 4)) (assert (= " + term + " " + term + "))").c_str())[0].arg(0);
}
int main()
{
    try
    {
        z3::context ctx;
        const std::vector<std::string> terms{
            "(bvadd x y)", "(bvsub x y)", "(bvmul x y)", "(bvneg x)", "(bvnot x)",
            "(bvand x y)", "(bvor x y)", "(bvxor x y)", "(bvnand x y)", "(bvnor x y)", "(bvxnor x y)",
            "(bvshl x y)", "(bvlshr x y)", "(bvashr x y)", "(concat x y)", "((_ extract 2 1) x)",
            "((_ zero_extend 3) x)", "((_ sign_extend 3) x)", "((_ repeat 3) x)",
            "((_ rotate_left 7) x)", "((_ rotate_right 7) x)", "(ext_rotate_left x y)", "(ext_rotate_right x y)",
            "(bvudiv x y)", "(bvurem x y)", "(bvsdiv x y)", "(bvsrem x y)", "(bvsmod x y)",
            "(bvult x y)", "(bvule x y)", "(bvugt x y)", "(bvuge x y)",
            "(bvslt x y)", "(bvsle x y)", "(bvsgt x y)", "(bvsge x y)",
            "(ite (= x #x0) y x)", "(bvcomp x y)", "(distinct x y #x0)", "(= x y)",
            "(and (= x #x0) (= y #x0))", "(or (= x #x0) (= y #x0))", "(xor (= x #x0) (= y #x0))",
            "(not (= x #x0))", "(=> (= x #x0) (= y #x0))", "true", "false", "#x9"};
        size_t assignments = 0;
        for (const auto& text : terms)
        {
            const auto term = parse(ctx, text); BitVectorProgram program(term);
            for (unsigned x = 0; x < 16; ++x) for (unsigned y = 0; y < 16; ++y)
            {
                z3::expr_vector from(ctx), to(ctx); std::map<std::string, Integer> values;
                for (const auto& [name, width] : program.inputs())
                { unsigned value = name == "x" ? x : y; from.push_back(ctx.bv_const(name.c_str(), width)); to.push_back(ctx.bv_val(value, width)); values[name] = value; }
                auto reference = term; reference = reference.substitute(from, to).simplify();
                const auto expected = reference.is_bool() ? Integer(reference.is_true()) : Integer(Z3_get_numeral_string(ctx, reference));
                check(program.evaluate(values) == expected, "native evaluator differs from Z3: " + text + " x=" + std::to_string(x) + " y=" + std::to_string(y)); ++assignments;
            }
        }
        auto wide = ctx.bv_const("wide", 80); auto wide_term = z3::concat(wide, wide) + z3::sext(wide, 80);
        BitVectorProgram wide_program(wide_term); const Integer large = (Integer(1) << 79) + 12345;
        check(wide_program.evaluate(std::map<std::string, Integer>{{"wide", large}}) == wrap((large << 80) + large + signed_value(large, 80), 160), "wide evaluator loses precision");
        rejects([&] { wide_program.evaluate(std::map<std::string, Integer>{}); });
        rejects([&] { BitVectorProgram bad(ctx.int_const("integer") == 1); });
        rejects([&] { BitVectorProgram bad(z3::concat(ctx.bv_const("alias", 4), ctx.bv_const("alias", 8))); });

        auto x = ctx.bv_const("x", 6), y = ctx.bv_const("y", 6);
        const auto sx = z3::sext(x, 10), sy = z3::sext(y, 10);
        std::vector<z3::expr> recoverable{
            (3 * sx - 2 * sy + 5).extract(9, 0),
            z3::ashr(3 * sx + 5 * sy + 3, 3).extract(7, 0),
            z3::ashr(-3 * sx + 1, 1).extract(7, 0),
            ctx.bv_val(239, 8),
            z3::ashr(z3::sext(ctx.bv_const("a", 4), 6) + z3::sext(ctx.bv_const("b", 1), 9), 1).extract(5, 0)
        };
        for (const auto& raw : recoverable)
        {
            const auto fit = recover_arithmetic(raw, true);
            check(fit.adopted(), "recoverable arithmetic not proved: " + fit.status + " " + fit.reason + " term=" + raw.to_string());
            check(fit.proposal && !fit.proof_query.empty(), "missing model/proof evidence");
        }
        // Exercise the exhaustive/bisection and exhaustive refinement branches,
        // plus source/constant precision beyond machine integers.
        for (const unsigned width : {9u, 13u, 80u})
        {
            auto source = ctx.bv_const("probe", width); const auto extended = z3::sext(source, 8);
            auto raw = z3::ashr(85 * extended + 37, 7).extract(width - 1, 0);
            auto fit = recover_arithmetic(raw, true); check(fit.adopted(), "wide/refined coefficient class: " + std::to_string(width) + " " + fit.status);
        }
        auto quantized = (z3::ashr(sx, 1) + z3::ashr(sy, 1)).extract(7, 0);
        const auto quantized_fit = recover_arithmetic(quantized, true);
        check(quantized_fit.status == "RAW_QUANT_CANDIDATE" && quantized_fit.proposal->quant_bound == 1, "multiple-rounding diagnostic was lost/adopted");
        check(!recover_arithmetic((sx * sy).extract(9, 0), true).adopted(), "nonlinear function adopted");
        // Physical storage permutations can move the sign bit into the word's
        // interior. Projection recovery must not depend on that arbitrary order.
        for (const auto bit : {0u, 4u, 8u, 14u, 19u, 63u, 79u})
            check(recover_arithmetic(wide.extract(bit, bit), true).adopted(), "interior bit projection missed");
        ArithmeticRecovery cache; const auto first = cache.recover(recoverable[0], true);
        z3::expr_vector from(ctx), to(ctx); from.push_back(x); from.push_back(y);
        to.push_back(ctx.bv_const("renamed_a", 6)); to.push_back(ctx.bv_const("renamed_b", 6));
        auto renamed = recoverable[0]; renamed = renamed.substitute(from, to);
        const auto second = cache.recover(renamed, true);
        check(first.adopted() && second.adopted() && second.reused && cache.calls() == 1 && cache.cache_hits() == 1, "proof cache did not reuse equivalent shape");
        check(second.proof_symbols.size() == 2 && verify_arithmetic(renamed, *second.proposal).adopted(), "cached model was not renamed consistently");
        ArithmeticModel rounded; rounded.width = 8; rounded.mode = "round";
        rounded.coefficients["x"] = Rational(3, 4); rounded.constant = Rational(-1, 4);
        const auto rounding = z3::ashr(3 * sx + 1, 2).extract(7, 0);
        check(verify_arithmetic(rounding, rounded).adopted(), "round-to-floor semantics");
        auto unsigned_model = rounded; unsigned_model.mode = "floor"; unsigned_model.constant = Rational(1, 4); unsigned_model.unsigned_inputs.insert("x");
        check(verify_arithmetic(z3::ashr(3 * z3::zext(x, 10) + 1, 2).extract(7, 0), unsigned_model).adopted(), "unsigned extension semantics");
        ArithmeticOptions unsigned_options; unsigned_options.unsigned_inputs.insert("x");
        check(recover_arithmetic(z3::ashr(3 * z3::zext(x, 10) + 1, 2).extract(7, 0), true, unsigned_options).adopted(), "unsigned candidate recovery");

        // Rare guard evades ordinary probes, but the acceptance gate must find it.
        const auto rare = ctx.bv_val("1512366075204170929041", 80);
        const auto guarded = z3::ite(wide == rare, wide + 2, wide + 1);
        ArithmeticModel guess; guess.width = 80; guess.mode = "exact"; guess.coefficients["wide"] = Rational(1); guess.constant = Rational(1);
        check(verify_arithmetic(guarded, guess).status == "FIT_REFUTED", "rare guarded error was adopted");
        ArithmeticOptions limited; limited.proof_resource_limit = 1;
        check(verify_arithmetic(guarded, guess, limited).status == "FIT_UNKNOWN", "resource exhaustion must remain UNKNOWN");
        check(recover_arithmetic(recoverable[0], false).status == "RAW_UNORDERED", "unknown order silently upgraded");
        ArithmeticOptions expired; expired.fit_timeout_ms = 0;
        check(recover_arithmetic(recoverable[0], true, expired).status == "RAW_FIT_TIMEOUT", "fit timeout lost");
        guess.mode = "quant"; guess.quant_bound = 1;
        check(!verify_arithmetic(guarded, guess).adopted(), "quantization samples are not a proof");
        rejects([&] { guess.expression({{"wide", wide}}, ctx); });
        auto malformed = rounded.to_json(); malformed["constant"] = {"1", "0"};
        rejects([&] { ArithmeticModel::from_json(malformed); });
        rounded.coefficients["x"] = Rational(1, 3);
        rejects([&] { rounded.expression({{"x", x}}, ctx); });
        std::cout << "PASS " << assignments << " native evaluator comparisons; exact/floor/round/unsigned fitting and acceptance gates\n";
    }
    catch (const std::exception& error) { std::cerr << error.what() << '\n'; return 1; }
}
