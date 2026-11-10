// Lifted program: proven arithmetic, routes, XOR layers and tables.
#pragma once
#include "transaction_program.h"
#include "lift_arithmetic.h"
#include "python_json.h"
#include <filesystem>

namespace program_recovery
{
    class Registry
    {
    public:
        std::string constant(const std::string& prefix, const Py& value);
        std::tuple<std::string, std::vector<unsigned>, std::vector<unsigned>> classify(const std::vector<BigInt>& T, unsigned k, unsigned m);
        std::vector<std::string> lines() const;

        std::vector<std::pair<std::string, Py>> consts;                       // name -> value, insertion order
        std::map<std::pair<std::string, std::string>, std::string> names;     // (prefix, repr) -> name
        std::vector<std::pair<std::pair<unsigned, unsigned>, std::vector<std::pair<std::string, std::vector<BigInt>>>>> classes;
        std::map<std::string, size_t> uses;
    };

    class ProgramLifter
    {
    public:
        ProgramLifter(ProgramBuilder& dec, const std::string& out_arg);

        std::string program;
        PJ interface_json;
        std::vector<std::pair<std::string, BigInt>> lift_stats;
        PJ arith_log = PJ::list();
        std::map<std::string, std::vector<unsigned>> word_order;
        std::map<std::string, std::string> order_sources;
        Registry registry;
        unsigned long long last_kept_bits = 0; // bits the last emit_lifted call left bit-level

        // the program's node statements: program_bits (all), program_lifted_bits (bits of chosen lifted statements, minus kept bits)
        void note_choice(bool lifted_chosen, unsigned width);

        std::optional<std::vector<std::string>> emit_lifted(const z3::expr& expr, const Printer::Env& env, unsigned indent, const IsParam& is_param,
                                                            const std::string& target, const std::map<std::string, std::pair<std::string, unsigned>>& alias,
                                                            const Json& node_id, const std::string& phase_code, const std::string& writes);

    private:
        ProgramBuilder& D;
        std::string m_out;
        void stat(const std::string& key, const BigInt& add);
        void learn_order(const std::string& word, const std::vector<unsigned>& order, const std::string& how);
        std::pair<std::set<unsigned>, std::vector<std::string>> arith_part(const z3::expr& expr, const std::map<std::string, z3::expr>& variables,
            const Json& node_id, const std::string& phase_code, const std::function<std::pair<std::string, unsigned>(const std::string&, unsigned)>& src,
            std::vector<std::string>& out, const std::string& ind, const std::string& writes, const IsParam& is_param);
    };

    std::optional<std::string> base_word(const std::string& sym);
    PJ to_pj(const Json& j);
}
