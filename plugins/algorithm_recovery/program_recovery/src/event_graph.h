// Event-graph model of the C3 backend: graph loading, certified specialisation, retiming
// to transaction coordinates, observations and liveness (port of decompile.py steps 1-3).
#pragma once
#include "python_format.h"
#include <nlohmann/json.hpp>
#include <z3++.h>
#include <functional>
#include <map>
#include <optional>
#include <set>
#include <string>
#include <tuple>
#include <vector>

namespace program_recovery
{
    using Json = nlohmann::ordered_json;

    struct Source
    {
        std::string symbol, kind, name;
        long long phase = 0, delay = 0;
    };

    // Node sources keep the insertion order of the graph JSON (Python dict order).
    struct Sources
    {
        std::vector<Source> items;
        const Source* find(const std::string& symbol) const
        {
            for (const auto& s : items) if (s.symbol == symbol) return &s;
            return nullptr;
        }
        const Source& at(const std::string& symbol) const
        {
            const auto* s = find(symbol);
            require(s != nullptr, "no source for " + symbol);
            return *s;
        }
        bool empty() const { return items.empty(); }
    };

    struct Node
    {
        z3::expr expr, raw;
        Sources sources;
        unsigned width = 0;
        size_t raw_size = 0;
        std::string raw_sha256;
        long long old_phase = 0;
    };

    using Key = std::pair<std::string, long long>;              // (word, phase)
    using StateKey = std::tuple<std::string, long long, long long>; // (word, phase, delay)

    struct Observation
    {
        std::string port;
        long long phase;
        z3::expr expr;
    };

    std::vector<z3::expr> z3_vars(const z3::expr& e);
    std::string sym_name(const z3::expr& e);
    z3::expr const_bv(z3::context& ctx, const std::string& bits_lsb, unsigned width);
    z3::expr substitute(const z3::expr& e, const std::vector<std::pair<z3::expr, z3::expr>>& subs);
    z3::expr concat_all(const std::vector<z3::expr>& parts); // z3.Concat(*parts): left fold
    z3::expr masked_known(const z3::expr& v, const std::string& known_lsb); // (v & ~mask) | val, Python creation order
    std::string sha256_hex(const std::string& text);

    class EventGraph
    {
    public:
        EventGraph(z3::context& ctx, const Json& graph, const Json& contract, const Json& observe, const std::string& case_name);

        z3::context& ctx;
        Json G, contract, observe;
        std::string case_name;
        long long P = 0, origin = 0, shift = 0;
        std::map<std::string, unsigned> width;
        std::map<std::string, std::tuple<std::string, std::string, unsigned>> bit_owner;
        std::map<Key, Node> nodes;
        std::map<std::string, std::vector<long long>> writes;
        std::map<std::string, std::vector<Json>> ports;   // output port -> port bits sorted by offset
        std::vector<Observation> observations;
        std::set<Key> live;
        std::set<StateKey> state_reads;
        std::set<Key> pad_reads;
        std::vector<Key> order;                               // live nodes sorted by (phase, name)

        z3::expr parse(const std::string& smt2) const;
        z3::expr specialise(const z3::expr& expr, long long phase) const;
        std::pair<long long, long long> last_write(const std::string& name, long long phase) const;
        z3::expr output_expr(const std::string& port) const;
        z3::expr leaf_substitute(const z3::expr& expr, long long phase) const;
        const std::string& known_pattern(const std::string& sym, long long ph) const;
        const Json& words() const { return G.at("words"); }
        const Json& inputs() const { return G.at("inputs"); }

    private:
        void visit_symbols(const z3::expr& expr, long long phase, const Sources* sources, std::vector<Key>& stack);
    };
}
