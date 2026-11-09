// Untimed transaction programs from a certified event graph: flat program and roll-up
// (lifting is added by lifted_program.cpp).
#pragma once
#include "event_graph.h"
#include "python_printer.h"

namespace program_recovery
{
    struct Counter
    {
        BigInt a, b;
        unsigned width = 0;
        std::vector<std::pair<std::string, unsigned>> bits; // position q holds bit k of symbol sym
    };

    struct Loop
    {
        size_t index = 0;
        std::vector<long long> phases;
        std::vector<std::string> names;
        std::map<std::string, z3::expr> templates;
        std::vector<std::pair<std::string, unsigned>> iter_vars, partial;
        std::vector<std::map<std::string, BigInt>> table;
        std::vector<std::map<std::string, std::pair<BigInt, BigInt>>> ptable;
        std::optional<Counter> counter;
        bool regular = true;
        long long stride = 0;
    };

    class ProgramBuilder
    {
    public:
        explicit ProgramBuilder(EventGraph& model);

        EventGraph& M;
        std::vector<std::string> header, tail;
        std::string program_flat, program_rolled;
        std::map<long long, std::vector<std::string>> steps; // phase -> live names (in order)
        std::vector<long long> step_phases;
        std::vector<Loop> loops;
        std::map<std::string, size_t> roll_proofs;
        std::vector<StateKey> state_keys;

        std::vector<std::string> emit_flat_node(const Key& key) const;
        std::string source_code(const Node& node, const std::string& sym, bool tolerant = false) const;
        std::vector<std::pair<std::string, z3::expr>> raw_symbols(const std::string& name, long long ph) const;
        bool data_symbol(const std::string& sym, long long ph) const;
        std::vector<std::string> emit_loop(const Loop& L, bool lifted = false);

        // Supplied by the lifting stage; when unset, emit_loop(lifted=true) behaves as plain.
        std::function<std::optional<std::vector<std::string>>(const z3::expr&, const Printer::Env&, unsigned, const std::function<bool(const std::string&)>&,
                                                              const std::string&, const std::map<std::string, std::pair<std::string, unsigned>>&,
                                                              const Json&, const std::string&, const std::string&)> lift_hook;

    private:
        void build_flat();
        void build_rollup();
        bool compatible(long long prev, long long cur, long long first) const;
        bool first_step_ok(long long first) const;
        std::optional<Counter> find_counter(const std::vector<std::pair<std::string, unsigned>>& iter_vars,
                                            const std::vector<std::map<std::string, BigInt>>& table) const;
        std::vector<std::vector<long long>> split_run(const std::vector<long long>& run) const;
    };

    std::string pyname(const std::string& sym);
    Py key_py(const Key& k);
    Py state_key_py(const StateKey& k);
}
