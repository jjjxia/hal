// Trace validation of generated programs and the program report (port of decompile.py's tail).
#pragma once
#include "lifted_program.h"
#include "python_interpreter.h"

namespace program_recovery
{
    struct Validation
    {
        std::vector<std::pair<std::string, long long>> counts; // Python Counter, insertion order
        PJ first_failure;                                      // null or dict
        void inc(const std::string& k, long long by = 1)
        {
            for (auto& [kk, v] : counts) if (kk == k) { v += by; return; }
            counts.emplace_back(k, by);
        }
        long long get(const std::string& k) const
        {
            for (const auto& [kk, v] : counts) if (kk == k) return v;
            return 0;
        }
        PJ to_pj() const
        {
            PJ d = PJ::dict();
            for (const auto& [k, v] : counts) d.set(k, v);
            return d;
        }
    };

    class TraceValidator
    {
    public:
        TraceValidator(const EventGraph& M, const ProgramBuilder& D, const std::string& trace_dir);
        Validation validate(const std::string& program_text) const;

    private:
        const EventGraph& M;
        const ProgramBuilder& D;
        std::map<long long, size_t> pos;
        std::vector<std::pair<std::string, std::string>> rows; // (pre, post)
        int bitval(long long b, long long t, bool post) const;
        std::optional<BigInt> wordval(const std::string& name, long long t, bool post) const;
    };

    PJ program_report(const EventGraph& M, const ProgramBuilder& D, const ProgramLifter& L, const Validation& flat, const Validation& rolled, const Validation& lifted);
}
