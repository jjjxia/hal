#pragma once
#include "algorithm_recovery/evaluation.h"
#include <boost/rational.hpp>
#include <nlohmann/json.hpp>
#include <optional>
#include <set>

namespace hal::algorithm_recovery
{
    using Rational = boost::rational<Integer>;
    struct CoefficientInterval
    {
        Rational low, high;
        bool low_closed = true, high_closed = true;
    };
    struct ArithmeticModel
    {
        unsigned width = 0;
        std::string mode; // exact/floor/round; quant is diagnostic only
        Rational constant;
        std::map<std::string, Rational> coefficients;
        std::set<std::string> unsigned_inputs;
        Integer quant_bound = 0;
        std::map<std::string, CoefficientInterval> coefficient_classes;
        nlohmann::json to_json() const;
        static ArithmeticModel from_json(const nlohmann::json& value);
        z3::expr expression(const std::map<std::string, z3::expr>& values, z3::context& ctx) const;
    };
    struct ArithmeticOptions
    {
        uint64_t seed = 20261031;
        unsigned fit_timeout_ms = 30000, proof_timeout_ms = 30000;
        unsigned proof_resource_limit = 0; // 0 = ordinary Z3 default
        std::set<std::string> unsigned_inputs;
    };
    struct ArithmeticResult
    {
        std::string status = "RAW_OPAQUE", reason, proof_query;
        std::optional<ArithmeticModel> proposal;
        size_t evaluations = 0;
        bool reused = false;
        std::map<std::string, std::string> proof_symbols; // canonical proof name -> caller name
        bool adopted() const { return status == "FIT_PROVED"; }
    };
    // Candidate generation preserves InvFold's probe/refinement strategy. Only
    // universal UNSAT of raw != SERIALIZED model permits adoption. Quantitative
    // candidates, SAT, UNKNOWN and timeout retain the original equation.
    ArithmeticResult recover_arithmetic(const z3::expr& raw, bool numeric_order_known,
                                        const ArithmeticOptions& options = {});
    ArithmeticResult verify_arithmetic(const z3::expr& raw, const ArithmeticModel& proposal,
                                       const ArithmeticOptions& options = {});
    class ArithmeticRecovery
    {
    public:
        explicit ArithmeticRecovery(ArithmeticOptions options = {}) : m_options(std::move(options)) {}
        ArithmeticResult recover(const z3::expr& raw, bool numeric_order_known);
        size_t calls() const { return m_calls; }
        size_t cache_hits() const { return m_hits; }
    private:
        ArithmeticOptions m_options;
        size_t m_calls = 0, m_hits = 0;
        std::map<std::string, ArithmeticResult> m_cache;
    };
}
