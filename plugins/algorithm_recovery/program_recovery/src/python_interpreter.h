// Interpreter for the restricted Python emitted by the C3 backend. Concrete mode follows Python's
// integer semantics (trace validation, as decompile.validate); symbolic mode follows
// close_chain.py exactly (fixed width W, wrap/wrapb simplification, signed z3py comparisons and
// right shifts, recording writes v[...] and replacing the written object by its cut symbol).
#pragma once
#include "python_format.h"
#include <z3++.h>
#include <functional>
#include <map>
#include <memory>
#include <optional>
#include <string>
#include <vector>

namespace program_recovery::interp
{
    struct Value;
    struct DictObj;
    struct SymBox { z3::expr t; };

    enum class VK { none, integer, str, tuple, list, dict, sym, symbool, func };

    struct Value
    {
        VK k = VK::none;
        BigInt i = 0;
        bool is_bool = false;
        std::string s;                                 // str, or function name
        std::shared_ptr<std::vector<Value>> items;     // tuple / list
        bool module_seq = false;                       // module-level constant (symbolic indexing allowed)
        std::shared_ptr<DictObj> dict;
        std::shared_ptr<SymBox> sym;                   // Sym (shared: writes mutate it)
        std::shared_ptr<z3::expr> sb;                  // SymBool

        static Value none() { return {}; }
        static Value integer(const BigInt& v) { Value x; x.k = VK::integer; x.i = v; return x; }
        static Value boolean(bool b) { Value x; x.k = VK::integer; x.i = b ? 1 : 0; x.is_bool = true; return x; }
        static Value string(const std::string& s) { Value x; x.k = VK::str; x.s = s; return x; }
        static Value tuple(std::vector<Value> v) { Value x; x.k = VK::tuple; x.items = std::make_shared<std::vector<Value>>(std::move(v)); return x; }
        static Value list(std::vector<Value> v) { Value x; x.k = VK::list; x.items = std::make_shared<std::vector<Value>>(std::move(v)); return x; }
        static Value func(const std::string& name) { Value x; x.k = VK::func; x.s = name; return x; }
        bool symbolic() const { return k == VK::sym || k == VK::symbool; }
        std::string key() const; // canonical dict key (repr)
    };

    enum class DictKind { plain, pads, state, values };

    struct DictObj
    {
        DictKind kind = DictKind::plain;
        std::vector<std::pair<Value, Value>> entries;
        std::map<std::string, size_t> index;
        std::optional<Value> find(const Value& key) const
        {
            auto it = index.find(key.key());
            if (it == index.end()) return std::nullopt;
            return entries[it->second].second;
        }
        void set(const Value& key, const Value& v)
        {
            const std::string k = key.key();
            auto it = index.find(k);
            if (it == index.end()) { index[k] = entries.size(); entries.emplace_back(key, v); }
            else entries[it->second].second = v;
        }
    };

    // ---- AST ----
    struct Expr;
    using ExprP = std::shared_ptr<Expr>;
    enum class EK { num, name, str, none, boolean, tuple, list, dict, subscript, call, attribute, binop, unary, boolop, compare, ifexp, starred };
    struct Expr
    {
        EK k;
        BigInt num;
        bool b = false;
        std::string s;         // name / str / operator / attribute
        std::vector<ExprP> args;
    };

    struct Stmt;
    using StmtP = std::shared_ptr<Stmt>;
    enum class SK { assign, augassign, forloop, ret, expr, funcdef };
    struct Stmt
    {
        SK k;
        std::vector<ExprP> targets; // assign: one target (may be a tuple)
        ExprP value;
        std::string op;             // augassign operator, loop variable, function name
        std::vector<std::string> params;
        std::vector<StmtP> body;
        size_t line = 0;
        std::string text;           // source line (funcdef headers, for the helper check)
    };

    struct Module
    {
        std::vector<StmtP> body;
        std::vector<std::string> lines;
        BigInt max_literal_bits = 0;
    };

    Module parse_module(const std::string& source);

    // ---- execution ----
    struct SymbolicContext
    {
        z3::context* ctx = nullptr;
        unsigned W = 0;
        std::function<Value(const Value& key)> pad_missing;                     // Pads.__missing__
        std::function<Value(const Value& key)> state_missing;                   // State.__missing__
        std::function<void(const Value& key, Value& val)> record_write;         // Values.__setitem__
    };

    class Interpreter
    {
    public:
        explicit Interpreter(const Module& m, SymbolicContext* sym = nullptr);
        // Run transaction(pads, state[, v]); returns (outputs, new_state).
        std::pair<Value, Value> transaction(const Value& pads, const Value& state, const std::optional<Value>& v = std::nullopt);
        std::vector<std::string> helper_problems; // helper preamble deviations

    private:
        const Module& M;
        SymbolicContext* S;
        std::map<std::string, Value> globals;
        const Stmt* tx = nullptr;

        using Env = std::map<std::string, Value>;
        Value eval(const ExprP& e, Env& env);
        void exec(const std::vector<StmtP>& body, Env& env, std::optional<std::pair<Value, Value>>& ret);
        void assign(const ExprP& target, const Value& v, Env& env);
        Value call(const std::string& fn, std::vector<Value> args);

    public:
        // primitive operations (shared by helpers)
        Value binop(const std::string& op, const Value& a, const Value& b);
        Value unary(const std::string& op, const Value& a);
        Value compare(const std::string& op, const Value& a, const Value& b);
        Value ite(const Value& c, const Value& a, const Value& b);
        Value logical_not(const Value& a);
        Value logical(const std::string& op, const std::vector<Value>& xs);
        Value index(const Value& seq, const Value& idx);
        Value vmin(const std::vector<Value>& xs);
        bool truthy(const Value& v) const;
        z3::expr bv(const Value& v);
        z3::expr bl(const Value& v);
        Value wrap(const z3::expr& t);
        Value wrapb(const z3::expr& t);
    };

    // Python integer semantics on arbitrary-precision values
    BigInt py_rshift(const BigInt& x, const BigInt& s);
    BigInt py_lshift(const BigInt& x, const BigInt& s);
    BigInt py_bitop(char op, const BigInt& a, const BigInt& b);
}
