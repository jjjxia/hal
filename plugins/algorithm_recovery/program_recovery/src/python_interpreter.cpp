#include "python_interpreter.h"
#include <cctype>
#include <sstream>

namespace program_recovery::interp
{
    // ---------------------------------------------------------------- Python integer semantics
    BigInt py_rshift(const BigInt& x, const BigInt& s)
    {
        require(s >= 0, "negative shift count");
        const unsigned sh = static_cast<unsigned>(s);
        if (x >= 0) return x >> sh;
        return -((-x - 1) >> sh) - 1; // floor division
    }

    BigInt py_lshift(const BigInt& x, const BigInt& s)
    {
        require(s >= 0, "negative shift count");
        const unsigned sh = static_cast<unsigned>(s);
        if (x >= 0) return x << sh;
        return -((-x) << sh);
    }

    BigInt py_bitop(char op, const BigInt& a, const BigInt& b)
    {
        if (a >= 0 && b >= 0)
            return op == '&' ? BigInt(a & b) : op == '|' ? BigInt(a | b) : BigInt(a ^ b);
        auto bitlen = [](const BigInt& v) { BigInt x = v < 0 ? BigInt(-v) : v; unsigned n = 0; while (x > 0) { x >>= 1; ++n; } return n; };
        const unsigned n = std::max(bitlen(a), bitlen(b)) + 2;
        const BigInt mod = BigInt(1) << n;
        auto tc = [&](const BigInt& v) { BigInt r = v % mod; if (r < 0) r += mod; return r; };
        const BigInt A = tc(a), B = tc(b);
        BigInt R = op == '&' ? BigInt(A & B) : op == '|' ? BigInt(A | B) : BigInt(A ^ B);
        if (((R >> (n - 1)) & 1) != 0) R -= mod;
        return R;
    }

    std::string Value::key() const
    {
        switch (k)
        {
            case VK::none: return "None";
            case VK::integer: return i.str();
            case VK::str: return py_str_repr(s);
            case VK::tuple: case VK::list:
            {
                std::string out = k == VK::tuple ? "(" : "[";
                for (size_t q = 0; q < items->size(); ++q) out += (q ? ", " : "") + (*items)[q].key();
                return out + (k == VK::tuple ? ")" : "]");
            }
            default: throw std::runtime_error("TypeError: unhashable value");
        }
    }

    // ---------------------------------------------------------------- tokenizer and parser
    namespace
    {
        struct Tok { enum T { name, num, str, op, end } t; std::string s; BigInt v; };

        std::vector<Tok> tokenize(const std::string& line)
        {
            std::vector<Tok> out;
            size_t i = 0;
            const size_t n = line.size();
            while (i < n)
            {
                const char c = line[i];
                if (c == ' ' || c == '\t') { ++i; continue; }
                if (c == '#') break;
                if (std::isalpha(static_cast<unsigned char>(c)) || c == '_')
                {
                    size_t j = i;
                    while (j < n && (std::isalnum(static_cast<unsigned char>(line[j])) || line[j] == '_')) ++j;
                    out.push_back({Tok::name, line.substr(i, j - i), 0});
                    i = j;
                    continue;
                }
                if (std::isdigit(static_cast<unsigned char>(c)))
                {
                    size_t j = i;
                    BigInt v = 0;
                    if (c == '0' && i + 1 < n && (line[i + 1] == 'x' || line[i + 1] == 'X'))
                    {
                        j = i + 2;
                        while (j < n && std::isxdigit(static_cast<unsigned char>(line[j])))
                        {
                            const char d = static_cast<char>(std::tolower(static_cast<unsigned char>(line[j])));
                            v = v * 16 + (d >= 'a' ? d - 'a' + 10 : d - '0');
                            ++j;
                        }
                    }
                    else
                        while (j < n && std::isdigit(static_cast<unsigned char>(line[j]))) { v = v * 10 + (line[j] - '0'); ++j; }
                    out.push_back({Tok::num, line.substr(i, j - i), v});
                    i = j;
                    continue;
                }
                if (c == '\'' || c == '"')
                {
                    const bool triple = i + 2 < n && line[i + 1] == c && line[i + 2] == c;
                    std::string s;
                    size_t j = i + (triple ? 3 : 1);
                    while (j < n)
                    {
                        if (triple && j + 2 < n + 0 && line.compare(j, 3, std::string(3, c)) == 0) { j += 3; break; }
                        if (!triple && line[j] == c) { ++j; break; }
                        if (line[j] == '\\' && j + 1 < n)
                        {
                            const char e = line[j + 1];
                            if (e == 'n') s += '\n'; else if (e == 't') s += '\t'; else if (e == 'r') s += '\r';
                            else if (e == 'x' && j + 3 < n) { s += static_cast<char>(std::stoi(line.substr(j + 2, 2), nullptr, 16)); j += 2; }
                            else s += e;
                            j += 2;
                            continue;
                        }
                        s += line[j++];
                    }
                    out.push_back({Tok::str, s, 0});
                    i = j;
                    continue;
                }
                static const char* ops3[] = {"**=", "//=", ">>=", "<<="};
                static const char* ops2[] = {"**", "//", "<<", ">>", "<=", ">=", "==", "!=", "|=", "&=", "^=", "+=", "-=", "*="};
                bool matched = false;
                for (const char* o : ops3) if (line.compare(i, 3, o) == 0) { out.push_back({Tok::op, o, 0}); i += 3; matched = true; break; }
                if (matched) continue;
                for (const char* o : ops2) if (line.compare(i, 2, o) == 0) { out.push_back({Tok::op, o, 0}); i += 2; matched = true; break; }
                if (matched) continue;
                out.push_back({Tok::op, std::string(1, c), 0});
                ++i;
            }
            out.push_back({Tok::end, "", 0});
            return out;
        }

        struct Parser
        {
            std::vector<Tok> t;
            size_t p = 0;
            BigInt* maxbits;

            const Tok& peek() const { return t[p]; }
            bool is_op(const std::string& s) const { return t[p].t == Tok::op && t[p].s == s; }
            bool is_kw(const std::string& s) const { return t[p].t == Tok::name && t[p].s == s; }
            void expect_op(const std::string& s) { require(is_op(s), "syntax: expected '" + s + "' got '" + t[p].s + "'"); ++p; }
            static ExprP mk(EK k) { auto e = std::make_shared<Expr>(); e->k = k; return e; }

            ExprP testlist()
            {
                ExprP first = test();
                if (!is_op(",")) return first;
                auto tup = mk(EK::tuple);
                tup->args.push_back(first);
                while (is_op(","))
                {
                    ++p;
                    if (peek().t == Tok::end || is_op("=") || is_op(")") || is_op("]")) break;
                    tup->args.push_back(test());
                }
                return tup;
            }
            ExprP test()
            {
                ExprP body = or_test();
                if (is_kw("if"))
                {
                    ++p;
                    ExprP cond = or_test();
                    require(is_kw("else"), "syntax: expected else");
                    ++p;
                    ExprP orelse = test();
                    auto e = mk(EK::ifexp);
                    e->args = {cond, body, orelse};
                    return e;
                }
                return body;
            }
            ExprP boolop(const std::string& kw, ExprP (Parser::*sub)())
            {
                ExprP first = (this->*sub)();
                if (!is_kw(kw)) return first;
                auto e = mk(EK::boolop);
                e->s = kw;
                e->args.push_back(first);
                while (is_kw(kw)) { ++p; e->args.push_back((this->*sub)()); }
                return e;
            }
            ExprP or_test() { return boolop("or", &Parser::and_test); }
            ExprP and_test() { return boolop("and", &Parser::not_test); }
            ExprP not_test()
            {
                if (is_kw("not")) { ++p; auto e = mk(EK::unary); e->s = "not"; e->args = {not_test()}; return e; }
                return comparison();
            }
            ExprP comparison()
            {
                ExprP left = bitor_();
                std::string op;
                if (is_op("<") || is_op(">") || is_op("==") || is_op(">=") || is_op("<=") || is_op("!=")) op = t[p++].s;
                else if (is_kw("is")) { ++p; op = "is"; if (is_kw("not")) { ++p; op = "is not"; } }
                else if (is_kw("in")) { ++p; op = "in"; }
                else return left;
                ExprP right = bitor_();
                require(!(is_op("<") || is_op(">") || is_op("==") || is_op(">=") || is_op("<=") || is_op("!=")), "chained comparisons are not supported");
                auto e = mk(EK::compare);
                e->s = op;
                e->args = {left, right};
                return e;
            }
            ExprP binary(const std::vector<std::string>& ops, ExprP (Parser::*sub)())
            {
                ExprP left = (this->*sub)();
                while (true)
                {
                    bool hit = false;
                    for (const auto& o : ops)
                        if (is_op(o))
                        {
                            ++p;
                            auto e = mk(EK::binop);
                            e->s = o;
                            e->args = {left, (this->*sub)()};
                            left = e;
                            hit = true;
                            break;
                        }
                    if (!hit) return left;
                }
            }
            ExprP bitor_() { return binary({"|"}, &Parser::bitxor_); }
            ExprP bitxor_() { return binary({"^"}, &Parser::bitand_); }
            ExprP bitand_() { return binary({"&"}, &Parser::shift_); }
            ExprP shift_() { return binary({"<<", ">>"}, &Parser::arith_); }
            ExprP arith_() { return binary({"+", "-"}, &Parser::term_); }
            ExprP term_() { return binary({"*", "//", "%"}, &Parser::factor_); }
            ExprP factor_()
            {
                if (is_op("-") || is_op("+") || is_op("~"))
                {
                    auto e = mk(EK::unary);
                    e->s = t[p++].s;
                    e->args = {factor_()};
                    return e;
                }
                return atom_expr();
            }
            ExprP atom_expr()
            {
                ExprP e = atom();
                while (true)
                {
                    if (is_op("("))
                    {
                        ++p;
                        auto c = mk(EK::call);
                        c->args.push_back(e);
                        while (!is_op(")"))
                        {
                            if (is_op("*")) { ++p; auto s = mk(EK::starred); s->args = {test()}; c->args.push_back(s); }
                            else c->args.push_back(test());
                            if (is_op(",")) ++p;
                            else break;
                        }
                        expect_op(")");
                        e = c;
                    }
                    else if (is_op("["))
                    {
                        ++p;
                        auto s = mk(EK::subscript);
                        s->args = {e, testlist()};
                        expect_op("]");
                        e = s;
                    }
                    else if (is_op("."))
                    {
                        ++p;
                        require(peek().t == Tok::name, "syntax: attribute");
                        auto a = mk(EK::attribute);
                        a->s = t[p++].s;
                        a->args = {e};
                        e = a;
                    }
                    else return e;
                }
            }
            ExprP atom()
            {
                const Tok& tk = t[p];
                if (tk.t == Tok::num)
                {
                    ++p;
                    auto e = mk(EK::num);
                    e->num = tk.v;
                    unsigned bl = 0;
                    for (BigInt x = tk.v; x > 0; x >>= 1) ++bl;
                    if (maxbits && BigInt(bl) > *maxbits) *maxbits = bl;
                    return e;
                }
                if (tk.t == Tok::str) { ++p; auto e = mk(EK::str); e->s = tk.s; while (peek().t == Tok::str) e->s += t[p++].s; return e; }
                if (tk.t == Tok::name)
                {
                    ++p;
                    if (tk.s == "None") return mk(EK::none);
                    if (tk.s == "True" || tk.s == "False")
                    {
                        auto e = mk(EK::boolean);
                        e->b = tk.s == "True";
                        if (maxbits && *maxbits < 1) *maxbits = 1;
                        return e;
                    }
                    auto e = mk(EK::name);
                    e->s = tk.s;
                    return e;
                }
                if (is_op("("))
                {
                    ++p;
                    if (is_op(")")) { ++p; return mk(EK::tuple); }
                    ExprP first = test();
                    if (is_op(")")) { ++p; return first; }
                    auto tup = mk(EK::tuple);
                    tup->args.push_back(first);
                    while (is_op(","))
                    {
                        ++p;
                        if (is_op(")")) break;
                        tup->args.push_back(test());
                    }
                    expect_op(")");
                    return tup;
                }
                if (is_op("["))
                {
                    ++p;
                    auto l = mk(EK::list);
                    while (!is_op("]"))
                    {
                        l->args.push_back(test());
                        if (is_op(",")) ++p;
                        else break;
                    }
                    expect_op("]");
                    return l;
                }
                if (is_op("{")) { ++p; expect_op("}"); return mk(EK::dict); }
                throw std::runtime_error("syntax: unexpected token '" + tk.s + "'");
            }
        };

        size_t indent_of(const std::string& line)
        {
            size_t i = 0;
            while (i < line.size() && line[i] == ' ') ++i;
            return i;
        }

        bool blank_or_comment(const std::string& line)
        {
            const size_t i = indent_of(line);
            return i == line.size() || line[i] == '#';
        }

        // position of a top-level assignment operator, or npos
        std::pair<size_t, std::string> find_assign(const std::string& line)
        {
            int depth = 0;
            char quote = 0;
            for (size_t i = 0; i < line.size(); ++i)
            {
                const char c = line[i];
                if (quote) { if (c == '\\') ++i; else if (c == quote) quote = 0; continue; }
                if (c == '\'' || c == '"') { quote = c; continue; }
                if (c == '#') break;
                if (c == '(' || c == '[' || c == '{') ++depth;
                else if (c == ')' || c == ']' || c == '}') --depth;
                else if (depth == 0 && c == '=')
                {
                    const char prev = i ? line[i - 1] : ' ', next = i + 1 < line.size() ? line[i + 1] : ' ';
                    if (next == '=') { ++i; continue; }
                    if (prev == '=' || prev == '!' || prev == '<' || prev == '>') continue;
                    if (prev == '|' || prev == '&' || prev == '^' || prev == '+' || prev == '-' || prev == '*')
                        return {i - 1, std::string(1, prev)};
                    return {i, ""};
                }
            }
            return {std::string::npos, ""};
        }

        ExprP parse_expr(const std::string& text, BigInt* maxbits)
        {
            Parser ps{tokenize(text), 0, maxbits};
            ExprP e = ps.testlist();
            require(ps.peek().t == Tok::end, "syntax: trailing tokens in '" + text.substr(0, 80) + "'");
            return e;
        }

        std::vector<StmtP> parse_block(const std::vector<std::string>& lines, size_t& i, size_t indent, BigInt* maxbits)
        {
            std::vector<StmtP> out;
            while (i < lines.size())
            {
                const std::string& line = lines[i];
                if (blank_or_comment(line)) { ++i; continue; }
                const size_t ind = indent_of(line);
                if (ind < indent) break;
                require(ind == indent, "indentation error at line " + std::to_string(i + 1));
                const std::string body = line.substr(ind);
                auto st = std::make_shared<Stmt>();
                st->line = i + 1;
                st->text = line;
                if (body.rfind("for ", 0) == 0)
                {
                    // for VAR in range(EXPR):  [# comment]
                    const auto in_pos = body.find(" in ");
                    const auto colon = body.rfind("):");
                    require(in_pos != std::string::npos && colon != std::string::npos, "unsupported for statement at line " + std::to_string(i + 1));
                    st->k = SK::forloop;
                    st->op = body.substr(4, in_pos - 4);
                    const std::string iter = body.substr(in_pos + 4, colon + 1 - (in_pos + 4));
                    require(iter.rfind("range(", 0) == 0, "only range() loops are supported");
                    st->value = parse_expr(iter.substr(6, iter.size() - 7), maxbits);
                    ++i;
                    st->body = parse_block(lines, i, indent + 4, maxbits);
                    out.push_back(st);
                    continue;
                }
                if (body.rfind("def ", 0) == 0)
                {
                    st->k = SK::funcdef;
                    const auto lp = body.find('('), rp = body.find(')');
                    st->op = body.substr(4, lp - 4);
                    std::stringstream ss(body.substr(lp + 1, rp - lp - 1));
                    std::string prm;
                    while (std::getline(ss, prm, ','))
                    {
                        const auto b = prm.find_first_not_of(' '), e = prm.find_last_not_of(' ');
                        st->params.push_back(prm.substr(b, e - b + 1));
                    }
                    ++i;
                    if (st->op == "transaction") st->body = parse_block(lines, i, indent + 4, maxbits);
                    else
                    {
                        // helper: keep the raw lines (checked verbatim, executed natively); count literals
                        while (i < lines.size() && (blank_or_comment(lines[i]) || indent_of(lines[i]) > indent))
                        {
                            if (!blank_or_comment(lines[i]))
                            {
                                st->text += "\n" + lines[i];
                                for (const auto& tk : tokenize(lines[i]))
                                    if (tk.t == Tok::num)
                                    {
                                        unsigned bl = 0;
                                        for (BigInt x = tk.v; x > 0; x >>= 1) ++bl;
                                        if (BigInt(bl) > *maxbits) *maxbits = bl;
                                    }
                            }
                            ++i;
                        }
                    }
                    out.push_back(st);
                    continue;
                }
                if (body.rfind("return ", 0) == 0)
                {
                    st->k = SK::ret;
                    st->value = parse_expr(body.substr(7), maxbits);
                    out.push_back(st);
                    ++i;
                    continue;
                }
                const auto [pos, aug] = find_assign(body);
                if (pos == std::string::npos)
                {
                    st->k = SK::expr; // docstring
                    st->value = parse_expr(body, nullptr);
                    out.push_back(st);
                    ++i;
                    continue;
                }
                if (!aug.empty())
                {
                    st->k = SK::augassign;
                    st->op = aug;
                    st->targets = {parse_expr(body.substr(0, pos), maxbits)};
                    st->value = parse_expr(body.substr(pos + 2), maxbits);
                }
                else
                {
                    st->k = SK::assign;
                    st->targets = {parse_expr(body.substr(0, pos), maxbits)};
                    st->value = parse_expr(body.substr(pos + 1), maxbits);
                }
                out.push_back(st);
                ++i;
            }
            return out;
        }
    }

    Module parse_module(const std::string& source)
    {
        Module m;
        std::stringstream ss(source);
        std::string line;
        while (std::getline(ss, line)) m.lines.push_back(line);
        size_t i = 0;
        m.body = parse_block(m.lines, i, 0, &m.max_literal_bits);
        require(i == m.lines.size(), "unparsed lines from line " + std::to_string(i + 1));
        return m;
    }

    // ---------------------------------------------------------------- interpreter
    static const std::map<std::string, std::string> HELPERS = {
        {"sgn", "def sgn(x, w):\n    return x - (1 << w) if (x >> (w - 1)) & 1 else x"},
        {"sra", "def sra(x, s, w):\n    return (sgn(x, w) >> min(s, w)) & ((1 << w) - 1)"},
        {"route", "def route(x, pairs):  # bit k of x -> bit o, for (o, k) in pairs\n    return sum(((x >> k) & 1) << o for o, k in pairs)"},
        {"pins", "def pins(*pairs):  # 1-bit values placed at bit o, for (o, v) in pairs\n    return sum((v & 1) << o for o, v in pairs)"},
        {"bits", "def bits(x, *idx):  # gather: bit i of the result = bit idx[i] of x (None: constant 0)\n    return sum(((x >> k) & 1) << i for i, k in enumerate(idx) if k is not None)"},
        {"pick", "def pick(*vk):  # gather across values: bit i = bit k_i of v_i, for v_i, k_i in pairs\n    return sum(((v >> k) & 1) << i for i, (v, k) in enumerate(zip(vk[::2], vk[1::2])) if k is not None)"},
        {"place", "def place(y, *idx):  # scatter: bit i of y -> bit idx[i]\n    return sum(((y >> i) & 1) << k for i, k in enumerate(idx))"},
        {"padword", "def padword(pads, ph, names):  # word whose bit i is data pad names[i] at phase ph\n    return sum((pads.get((n, ph), 0) & 1) << i for i, n in enumerate(names))"},
    };

    Interpreter::Interpreter(const Module& m, SymbolicContext* sym) : M(m), S(sym)
    {
        Env dummy;
        for (const auto& st : M.body)
        {
            if (st->k == SK::funcdef)
            {
                if (st->op == "transaction") { tx = st.get(); continue; }
                auto it = HELPERS.find(st->op);
                if (it == HELPERS.end()) helper_problems.push_back("unknown function " + st->op);
                else if (st->text != it->second) helper_problems.push_back("helper " + st->op + " differs from the reference definition");
                globals[st->op] = Value::func(st->op);
                continue;
            }
            require(st->k == SK::assign && st->targets[0]->k == EK::name, "unsupported module-level statement at line " + std::to_string(st->line));
            Value v = eval(st->value, dummy);
            if (S && (v.k == VK::list || v.k == VK::tuple) && st->targets[0]->s.rfind("_", 0) != 0) v.module_seq = true;
            globals[st->targets[0]->s] = v;
        }
        require(tx != nullptr, "no transaction function");
        for (const char* b : {"min", "range"}) if (!globals.count(b)) globals[b] = Value::func(b);
    }

    bool Interpreter::truthy(const Value& v) const
    {
        switch (v.k)
        {
            case VK::none: return false;
            case VK::integer: return v.i != 0;
            case VK::str: return !v.s.empty();
            case VK::tuple: case VK::list: return !v.items->empty();
            case VK::dict: return !v.dict->entries.empty();
            case VK::func: return true;
            default: throw std::runtime_error("TypeError: symbolic condition used as a Python bool");
        }
    }

    z3::expr Interpreter::bv(const Value& x)
    {
        z3::context& ctx = *S->ctx;
        if (x.k == VK::sym) return x.sym->t;
        if (x.k == VK::symbool)
        {
            z3::expr one = ctx.bv_val(1, S->W), zero = ctx.bv_val(0, S->W);
            return z3::ite(*x.sb, one, zero);
        }
        if (x.k == VK::integer)
        {
            const BigInt mod = BigInt(1) << S->W;
            BigInt r = x.i % mod;
            if (r < 0) r += mod;
            return ctx.bv_val(r.str().c_str(), S->W);
        }
        throw std::runtime_error("TypeError: bv of non-integer");
    }

    z3::expr Interpreter::bl(const Value& x)
    {
        if (x.k == VK::symbool) return *x.sb;
        if (x.k == VK::integer && x.is_bool) return S->ctx->bool_val(x.i != 0);
        throw std::runtime_error("TypeError: bl of non-bool");
    }

    Value Interpreter::wrap(const z3::expr& t0)
    {
        z3::expr t = t0.simplify();
        if (t.is_app() && t.decl().decl_kind() == Z3_OP_BNUM)
        {
            BigInt v(Z3_get_numeral_string(t.ctx(), t));
            const unsigned w = t.get_sort().bv_size();
            if (((v >> (w - 1)) & 1) != 0) v -= BigInt(1) << w; // as_signed_long
            return Value::integer(v);
        }
        Value r;
        r.k = VK::sym;
        r.sym = std::make_shared<SymBox>(SymBox{t});
        return r;
    }

    Value Interpreter::wrapb(const z3::expr& t0)
    {
        z3::expr t = t0.simplify();
        if (t.is_true()) return Value::boolean(true);
        if (t.is_false()) return Value::boolean(false);
        Value r;
        r.k = VK::symbool;
        r.sb = std::make_shared<z3::expr>(t);
        return r;
    }

    Value Interpreter::binop(const std::string& op, const Value& a, const Value& b)
    {
        if (!a.symbolic() && !b.symbolic())
        {
            require(a.k == VK::integer && b.k == VK::integer, "TypeError: unsupported operand types for " + op);
            if (op == "+") return Value::integer(a.i + b.i);
            if (op == "-") return Value::integer(a.i - b.i);
            if (op == "*") return Value::integer(a.i * b.i);
            if (op == "<<") return Value::integer(py_lshift(a.i, b.i));
            if (op == ">>") return Value::integer(py_rshift(a.i, b.i));
            if (op == "&" || op == "|" || op == "^")
            {
                Value r = Value::integer(py_bitop(op[0], a.i, b.i));
                r.is_bool = a.is_bool && b.is_bool;
                return r;
            }
            throw std::runtime_error("unsupported operator " + op);
        }
        const bool boolish_a = a.k == VK::symbool || (a.k == VK::integer && a.is_bool);
        const bool boolish_b = b.k == VK::symbool || (b.k == VK::integer && b.is_bool);
        if ((op == "&" || op == "|" || op == "^") && boolish_a && boolish_b)
        {
            // SymBool op bool stays bool (reflected for bool op SymBool)
            const Value& x = a.k == VK::symbool ? a : b;
            const Value& y = a.k == VK::symbool ? b : a;
            z3::expr ex = bl(x), ey = bl(y);
            if (op == "&") return wrapb(ex && ey);
            if (op == "|") return wrapb(ex || ey);
            return wrapb(z3::expr(*S->ctx, Z3_mk_xor(*S->ctx, ex, ey)));
        }
        if (op == "<<")
        {
            const Value& x = a;
            const Value& s = b;
            if (s.k == VK::integer)
            {
                if (s.i < S->W)
                {
                    z3::expr ex = bv(x);
                    z3::expr es = S->ctx->bv_val(s.i.str().c_str(), S->W);
                    return wrap(z3::shl(ex, es));
                }
                return Value::integer(0);
            }
            z3::expr es = bv(s);
            z3::expr lim = S->ctx->bv_val(S->W, S->W);
            z3::expr c = z3::ult(es, lim);
            z3::expr ex = bv(x);
            z3::expr sh = z3::shl(ex, es);
            z3::expr zero = S->ctx->bv_val(0, S->W);
            return wrap(z3::ite(c, sh, zero));
        }
        if (op == ">>")
        {
            if (b.k == VK::integer)
            {
                z3::expr ex = bv(a);
                const BigInt sh = b.i < S->W - 1 ? b.i : BigInt(S->W - 1);
                z3::expr es = S->ctx->bv_val(sh.str().c_str(), S->W);
                return wrap(z3::ashr(ex, es));
            }
            z3::expr ex = bv(a);
            z3::expr es = bv(b);
            return wrap(z3::ashr(ex, es));
        }
        z3::expr ea = bv(a);
        z3::expr eb = bv(b);
        if (op == "&") return wrap(ea & eb);
        if (op == "|") return wrap(ea | eb);
        if (op == "^") return wrap(ea ^ eb);
        if (op == "+") return wrap(ea + eb);
        if (op == "-") return wrap(ea - eb);
        if (op == "*") return wrap(ea * eb);
        throw std::runtime_error("unsupported symbolic operator " + op);
    }

    Value Interpreter::unary(const std::string& op, const Value& a)
    {
        if (op == "not") return logical_not(a);
        if (!a.symbolic())
        {
            require(a.k == VK::integer, "TypeError: bad operand for unary " + op);
            if (op == "-") return Value::integer(-a.i);
            if (op == "+") return Value::integer(a.i);
            if (op == "~") return Value::integer(-a.i - 1);
        }
        else
        {
            z3::expr ea = bv(a);
            if (op == "~") return wrap(~ea);
            if (op == "-") return wrap(-ea);
        }
        throw std::runtime_error("unsupported unary operator " + op);
    }

    Value Interpreter::compare(const std::string& op, const Value& a, const Value& b)
    {
        if (!a.symbolic() && !b.symbolic())
        {
            if (op == "is" || op == "is not")
            {
                const bool same = a.k == VK::none && b.k == VK::none;
                return Value::boolean(op == "is" ? same : !same);
            }
            if (op == "==" || op == "!=")
            {
                bool eq;
                if (a.k == VK::integer && b.k == VK::integer) eq = a.i == b.i;
                else eq = a.k == b.k && a.key() == b.key();
                return Value::boolean(op == "==" ? eq : !eq);
            }
            require(a.k == VK::integer && b.k == VK::integer, "TypeError: ordering of non-integers");
            if (op == "<") return Value::boolean(a.i < b.i);
            if (op == "<=") return Value::boolean(a.i <= b.i);
            if (op == ">") return Value::boolean(a.i > b.i);
            if (op == ">=") return Value::boolean(a.i >= b.i);
            throw std::runtime_error("unsupported comparison " + op);
        }
        auto booleq = [&](const Value& x, const Value& y, bool neg) {
            const bool bx = x.k == VK::symbool || (x.k == VK::integer && x.is_bool);
            const bool by = y.k == VK::symbool || (y.k == VK::integer && y.is_bool);
            z3::expr r = (bx && by) ? z3::expr(bl(x) == bl(y)) : z3::expr(bv(x) == bv(y));
            return wrapb(neg ? !r : r);
        };
        auto symcmp = [&](const std::string& o, const Value& x, const Value& y) -> Value {
            // x is Sym: z3py semantics (signed ordering)
            z3::expr ex = bv(x);
            z3::expr ey = bv(y);
            if (o == "<") return wrapb(ex < ey);
            if (o == "<=") return wrapb(ex <= ey);
            if (o == ">") return wrapb(ex > ey);
            if (o == ">=") return wrapb(ex >= ey);
            if (o == "==") return wrapb(ex == ey);
            if (o == "!=") return wrapb(ex != ey);
            throw std::runtime_error("unsupported comparison " + o);
        };
        static const std::map<std::string, std::string> reflected = {{"<", ">"}, {"<=", ">="}, {">", "<"}, {">=", "<="}, {"==", "=="}, {"!=", "!="}};
        if (a.k == VK::sym) return symcmp(op, a, b);
        if (a.k == VK::symbool)
        {
            if (op == "==" || op == "!=") return booleq(a, b, op == "!=");
            if (b.k == VK::sym) return symcmp(reflected.at(op), b, a);
            throw std::runtime_error("TypeError: ordering of a symbolic bool");
        }
        // a concrete, b symbolic: reflected method of b
        if (b.k == VK::sym) return symcmp(reflected.at(op), b, a);
        if (op == "==" || op == "!=") return booleq(b, a, op == "!=");
        throw std::runtime_error("TypeError: ordering of a symbolic bool");
    }

    Value Interpreter::ite(const Value& c, const Value& a, const Value& b)
    {
        if (!c.symbolic()) return truthy(c) ? a : b;
        z3::expr cz = c.k == VK::symbool ? bl(c) : z3::expr(bv(c) != S->ctx->bv_val(0, S->W));
        const bool ab = a.k == VK::symbool || b.k == VK::symbool || (a.k == VK::integer && a.is_bool && b.k == VK::integer && b.is_bool);
        if (ab)
        {
            z3::expr x = bl(a);
            z3::expr y = bl(b);
            return wrapb(z3::ite(cz, x, y));
        }
        z3::expr x = bv(a);
        z3::expr y = bv(b);
        return wrap(z3::ite(cz, x, y));
    }

    Value Interpreter::logical_not(const Value& a)
    {
        if (a.k == VK::symbool) return wrapb(!*a.sb);
        if (a.k == VK::sym) return wrapb(a.sym->t == S->ctx->bv_val(0, S->W));
        return Value::boolean(!truthy(a));
    }

    Value Interpreter::logical(const std::string& op, const std::vector<Value>& xs)
    {
        bool any_sym = false;
        for (const auto& x : xs) any_sym |= x.symbolic();
        if (!any_sym)
        {
            Value r = Value::boolean(op == "and");
            for (const auto& x : xs) r = op == "and" ? (truthy(r) ? x : r) : (truthy(r) ? r : x);
            return r;
        }
        z3::expr_vector v(*S->ctx);
        for (const auto& x : xs)
        {
            if (x.k == VK::symbool || (x.k == VK::integer && x.is_bool)) v.push_back(bl(x));
            else v.push_back(bv(x) != S->ctx->bv_val(0, S->W));
        }
        return wrapb(op == "and" ? z3::mk_and(v) : z3::mk_or(v));
    }

    Value Interpreter::vmin(const std::vector<Value>& xs)
    {
        bool concrete = true;
        for (const auto& x : xs) concrete &= x.k == VK::integer;
        if (concrete)
        {
            Value r = xs[0];
            for (size_t i = 1; i < xs.size(); ++i) if (xs[i].i < r.i) r = xs[i];
            return r;
        }
        Value r = xs[0];
        for (size_t i = 1; i < xs.size(); ++i) r = ite(compare("<", xs[i], r), xs[i], r);
        return r;
    }

    Value Interpreter::index(const Value& seq, const Value& idx)
    {
        if (seq.k == VK::list || seq.k == VK::tuple)
        {
            const auto& items = *seq.items;
            if (idx.symbolic())
            {
                require(seq.module_seq, "TypeError: symbolic value used as a Python index");
                Value r = items.back();
                for (long long k = static_cast<long long>(items.size()) - 2; k >= 0; --k)
                    r = ite(compare("==", idx, Value::integer(k)), items[static_cast<size_t>(k)], r);
                return r;
            }
            require(idx.k == VK::integer, "TypeError: index");
            long long i = static_cast<long long>(idx.i);
            if (i < 0) i += static_cast<long long>(items.size());
            require(i >= 0 && i < static_cast<long long>(items.size()), "IndexError");
            return items[static_cast<size_t>(i)];
        }
        if (seq.k == VK::dict)
        {
            DictObj& d = *seq.dict;
            if (auto v = d.find(idx)) return *v;
            if (d.kind == DictKind::pads) { Value v = S->pad_missing(idx); d.set(idx, v); return v; }
            if (d.kind == DictKind::state) return S->state_missing(idx);
            throw std::runtime_error("KeyError " + idx.key());
        }
        throw std::runtime_error("TypeError: not subscriptable");
    }

    Value Interpreter::call(const std::string& fn, std::vector<Value> a)
    {
        auto I = [](long long v) { return Value::integer(v); };
        auto sum_terms = [&](const std::vector<Value>& terms) {
            Value acc = I(0);
            for (const auto& t : terms) acc = binop("+", acc, t);
            return acc;
        };
        auto seq_items = [](const Value& v) -> const std::vector<Value>& {
            require(v.k == VK::tuple || v.k == VK::list, "TypeError: not iterable");
            return *v.items;
        };
        if (fn == "min") return vmin(a);
        if (fn == "sgn")
        {
            const Value& x = a[0];
            const Value& w = a[1];
            Value cond = binop("&", binop(">>", x, binop("-", w, I(1))), I(1));
            Value neg = binop("-", x, binop("<<", I(1), w));
            return ite(cond, neg, x);
        }
        if (fn == "sra")
        {
            Value s = call("sgn", {a[0], a[2]});
            Value m = vmin({a[1], a[2]});
            Value sh = binop(">>", s, m);
            return binop("&", sh, binop("-", binop("<<", I(1), a[2]), I(1)));
        }
        if (fn == "route")
        {
            std::vector<Value> terms;
            for (const auto& pr : seq_items(a[1]))
            {
                const auto& ok = seq_items(pr);
                terms.push_back(binop("<<", binop("&", binop(">>", a[0], ok[1]), I(1)), ok[0]));
            }
            return sum_terms(terms);
        }
        if (fn == "pins")
        {
            std::vector<Value> terms;
            for (const auto& pr : a)
            {
                const auto& ov = seq_items(pr);
                terms.push_back(binop("<<", binop("&", ov[1], I(1)), ov[0]));
            }
            return sum_terms(terms);
        }
        if (fn == "bits")
        {
            std::vector<Value> terms;
            for (size_t i = 1; i < a.size(); ++i)
            {
                if (a[i].k == VK::none) continue;
                terms.push_back(binop("<<", binop("&", binop(">>", a[0], a[i]), I(1)), I(static_cast<long long>(i - 1))));
            }
            return sum_terms(terms);
        }
        if (fn == "pick")
        {
            // zip(vk[::2], vk[1::2]) has floor(len/2) pairs
            std::vector<Value> terms;
            for (size_t i = 0; i < a.size() / 2; ++i)
            {
                const Value& v = a[2 * i];
                const Value& k = a[2 * i + 1];
                if (k.k == VK::none) continue;
                terms.push_back(binop("<<", binop("&", binop(">>", v, k), I(1)), I(static_cast<long long>(i))));
            }
            return sum_terms(terms);
        }
        if (fn == "place")
        {
            std::vector<Value> terms;
            for (size_t i = 1; i < a.size(); ++i)
                terms.push_back(binop("<<", binop("&", binop(">>", a[0], I(static_cast<long long>(i - 1))), I(1)), a[i]));
            return sum_terms(terms);
        }
        if (fn == "padword")
        {
            std::vector<Value> terms;
            const auto& names = seq_items(a[2]);
            for (size_t i = 0; i < names.size(); ++i)
            {
                Value key = Value::tuple({names[i], a[1]});
                Value got;
                if (a[0].dict->kind == DictKind::plain) { auto f = a[0].dict->find(key); got = f ? *f : I(0); }
                else got = index(a[0], key);
                terms.push_back(binop("<<", binop("&", got, I(1)), I(static_cast<long long>(i))));
            }
            return sum_terms(terms);
        }
        throw std::runtime_error("unknown function " + fn);
    }

    Value Interpreter::eval(const ExprP& e, Env& env)
    {
        switch (e->k)
        {
            case EK::num: return Value::integer(e->num);
            case EK::boolean: return Value::boolean(e->b);
            case EK::none: return Value::none();
            case EK::str: return Value::string(e->s);
            case EK::name:
            {
                if (auto it = env.find(e->s); it != env.end()) return it->second;
                if (auto it = globals.find(e->s); it != globals.end()) return it->second;
                throw std::runtime_error("NameError " + e->s);
            }
            case EK::tuple: case EK::list:
            {
                std::vector<Value> items;
                for (const auto& x : e->args) items.push_back(eval(x, env));
                return e->k == EK::tuple ? Value::tuple(items) : Value::list(items);
            }
            case EK::dict:
            {
                Value d;
                d.k = VK::dict;
                d.dict = std::make_shared<DictObj>();
                return d;
            }
            case EK::subscript:
            {
                Value seq = eval(e->args[0], env);
                Value idx = eval(e->args[1], env);
                return index(seq, idx);
            }
            case EK::call:
            {
                const ExprP& f = e->args[0];
                std::vector<Value> args;
                if (f->k == EK::attribute)
                {
                    require(f->s == "get", "unsupported method " + f->s);
                    Value obj = eval(f->args[0], env);
                    for (size_t i = 1; i < e->args.size(); ++i) args.push_back(eval(e->args[i], env));
                    require(obj.k == VK::dict, "get on non-dict");
                    DictObj& d = *obj.dict;
                    if (d.kind == DictKind::pads || d.kind == DictKind::state) return index(obj, args[0]);
                    if (auto v = d.find(args[0])) return *v;
                    return args.size() > 1 ? args[1] : Value::none();
                }
                Value fv = eval(f, env);
                require(fv.k == VK::func, "call of non-function");
                for (size_t i = 1; i < e->args.size(); ++i)
                {
                    if (e->args[i]->k == EK::starred)
                    {
                        Value sv = eval(e->args[i]->args[0], env);
                        require(sv.k == VK::tuple || sv.k == VK::list, "starred non-sequence");
                        for (const auto& x : *sv.items) args.push_back(x);
                    }
                    else args.push_back(eval(e->args[i], env));
                }
                return call(fv.s, args);
            }
            case EK::binop:
            {
                Value a = eval(e->args[0], env);
                Value b = eval(e->args[1], env);
                return binop(e->s, a, b);
            }
            case EK::unary:
            {
                Value a = eval(e->args[0], env);
                return unary(e->s, a);
            }
            case EK::boolop:
            {
                if (S)
                {
                    std::vector<Value> xs;
                    for (const auto& x : e->args) xs.push_back(eval(x, env));
                    return logical(e->s, xs);
                }
                // concrete: Python short-circuit semantics
                Value r = eval(e->args[0], env);
                for (size_t i = 1; i < e->args.size(); ++i)
                {
                    if (e->s == "and" ? !truthy(r) : truthy(r)) return r;
                    r = eval(e->args[i], env);
                }
                return r;
            }
            case EK::compare:
            {
                Value a = eval(e->args[0], env);
                Value b = eval(e->args[1], env);
                return compare(e->s, a, b);
            }
            case EK::ifexp:
            {
                if (S)
                {
                    Value c = eval(e->args[0], env);
                    Value a = eval(e->args[1], env);
                    Value b = eval(e->args[2], env);
                    return ite(c, a, b);
                }
                Value c = eval(e->args[0], env);
                return truthy(c) ? eval(e->args[1], env) : eval(e->args[2], env);
            }
            default: throw std::runtime_error("unsupported expression");
        }
    }

    void Interpreter::assign(const ExprP& target, const Value& v, Env& env)
    {
        if (target->k == EK::name) { env[target->s] = v; return; }
        if (target->k == EK::tuple)
        {
            require(v.k == VK::tuple || v.k == VK::list, "unpack of non-sequence");
            require(v.items->size() == target->args.size(), "unpack size mismatch");
            const auto items = *v.items;
            for (size_t i = 0; i < items.size(); ++i) assign(target->args[i], items[i], env);
            return;
        }
        if (target->k == EK::subscript)
        {
            Value obj = eval(target->args[0], env);
            Value key = eval(target->args[1], env);
            require(obj.k == VK::dict, "item assignment on non-dict");
            if (obj.dict->kind == DictKind::values && S)
            {
                Value val = v;
                S->record_write(key, val);
                return;
            }
            obj.dict->set(key, v);
            return;
        }
        throw std::runtime_error("unsupported assignment target");
    }

    void Interpreter::exec(const std::vector<StmtP>& body, Env& env, std::optional<std::pair<Value, Value>>& ret)
    {
        for (const auto& st : body)
        {
            if (ret) return;
            switch (st->k)
            {
                case SK::expr: break;
                case SK::assign:
                {
                    Value v = eval(st->value, env);
                    assign(st->targets[0], v, env);
                    break;
                }
                case SK::augassign:
                {
                    require(st->targets[0]->k == EK::name, "augmented assignment to non-name");
                    Value cur = eval(st->targets[0], env);
                    Value v = eval(st->value, env);
                    env[st->targets[0]->s] = binop(st->op, cur, v);
                    break;
                }
                case SK::forloop:
                {
                    Value n = eval(st->value, env);
                    require(n.k == VK::integer, "range of non-integer");
                    for (BigInt i = 0; i < n.i && !ret; ++i)
                    {
                        env[st->op] = Value::integer(i);
                        exec(st->body, env, ret);
                    }
                    break;
                }
                case SK::ret:
                {
                    Value v = eval(st->value, env);
                    require(v.k == VK::tuple && v.items->size() == 2, "transaction must return (outputs, new_state)");
                    ret = std::make_pair((*v.items)[0], (*v.items)[1]);
                    break;
                }
                default: throw std::runtime_error("unsupported statement in transaction");
            }
        }
    }

    std::pair<Value, Value> Interpreter::transaction(const Value& pads, const Value& state, const std::optional<Value>& v)
    {
        Env env;
        env[tx->params.at(0)] = pads;
        env[tx->params.at(1)] = state;
        std::vector<StmtP> body;
        for (const auto& st : tx->body)
        {
            // the checker passes its recording dict instead of the program's own `v = {}`
            if (v && st->k == SK::assign && st->targets[0]->k == EK::name && st->targets[0]->s == "v" && st->value->k == EK::dict) continue;
            body.push_back(st);
        }
        if (v) env["v"] = *v;
        std::optional<std::pair<Value, Value>> ret;
        exec(body, env, ret);
        require(ret.has_value(), "transaction did not return");
        return *ret;
    }
}
