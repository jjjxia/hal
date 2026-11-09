// Formatting helpers for the generated Python programs: integer literals, hex() and repr() of tuples,
// lists and strings as Python prints them.
#pragma once
#include <boost/multiprecision/cpp_int.hpp>
#include <stdexcept>
#include <string>
#include <variant>
#include <vector>

namespace program_recovery
{
    using BigInt = boost::multiprecision::cpp_int;

    inline void require(bool condition, const std::string& why)
    {
        if (!condition) throw std::runtime_error(why);
    }

    // Python hex(): lowercase, "0x0" for zero, "-0x.." for negative values.
    inline std::string py_hex(const BigInt& value)
    {
        if (value < 0) return "-" + py_hex(-value);
        if (value == 0) return "0x0";
        static const char* digits = "0123456789abcdef";
        std::string out;
        BigInt v = value;
        while (v > 0)
        {
            const unsigned d = static_cast<unsigned>(v & 15);
            out.push_back(digits[d]);
            v >>= 4;
        }
        return "0x" + std::string(out.rbegin(), out.rend());
    }

    inline std::string py_int(const BigInt& value) { return value.str(); }

    inline BigInt mask_of(unsigned width) { return (BigInt(1) << width) - 1; }

    // int(bits, 2) of a binary string, most significant character first.
    inline BigInt from_binary(const std::string& bits)
    {
        BigInt v = 0;
        for (char c : bits)
        {
            require(c == '0' || c == '1', "not a binary digit");
            v = (v << 1) | (c == '1' ? 1 : 0);
        }
        return v;
    }

    // Python repr() of a str.
    inline std::string py_str_repr(const std::string& s)
    {
        const bool has_single = s.find('\'') != std::string::npos, has_double = s.find('"') != std::string::npos;
        const char quote = (has_single && !has_double) ? '"' : '\'';
        std::string out(1, quote);
        for (unsigned char c : s)
        {
            if (c == '\\') out += "\\\\";
            else if (c == quote) { out += '\\'; out += static_cast<char>(c); }
            else if (c == '\n') out += "\\n";
            else if (c == '\r') out += "\\r";
            else if (c == '\t') out += "\\t";
            else if (c < 0x20 || c == 0x7f)
            {
                static const char* hx = "0123456789abcdef";
                out += "\\x"; out += hx[c >> 4]; out += hx[c & 15];
            }
            else out += static_cast<char>(c);
        }
        out += quote;
        return out;
    }

    // A tiny Python value model, used only to reproduce repr() of constants that the
    // generated programs and reports print (ints, strings, None, tuples, lists).
    struct Py
    {
        enum class Kind { none, boolean, integer, string, tuple, list } kind = Kind::none;
        BigInt i = 0;
        bool b = false;
        std::string s;
        std::vector<Py> items;

        static Py none() { return Py{}; }
        static Py boolean(bool v) { Py p; p.kind = Kind::boolean; p.b = v; return p; }
        static Py integer(const BigInt& v) { Py p; p.kind = Kind::integer; p.i = v; return p; }
        static Py str(const std::string& v) { Py p; p.kind = Kind::string; p.s = v; return p; }
        static Py tuple(std::vector<Py> v) { Py p; p.kind = Kind::tuple; p.items = std::move(v); return p; }
        static Py list(std::vector<Py> v) { Py p; p.kind = Kind::list; p.items = std::move(v); return p; }

        std::string repr() const
        {
            switch (kind)
            {
                case Kind::none: return "None";
                case Kind::boolean: return b ? "True" : "False";
                case Kind::integer: return py_int(i);
                case Kind::string: return py_str_repr(s);
                case Kind::tuple:
                {
                    if (items.empty()) return "()";
                    std::string out = "(";
                    for (size_t k = 0; k < items.size(); ++k) out += (k ? ", " : "") + items[k].repr();
                    return out + (items.size() == 1 ? ",)" : ")");
                }
                case Kind::list:
                {
                    std::string out = "[";
                    for (size_t k = 0; k < items.size(); ++k) out += (k ? ", " : "") + items[k].repr();
                    return out + "]";
                }
            }
            return "";
        }

        bool operator==(const Py& o) const
        {
            return kind == o.kind && i == o.i && b == o.b && s == o.s && items == o.items;
        }
    };

    inline Py py(const std::string& s) { return Py::str(s); }
    inline Py py(const char* s) { return Py::str(s); }
    inline Py py(long long v) { return Py::integer(v); }
    inline Py py(int v) { return Py::integer(v); }
    inline Py py(unsigned v) { return Py::integer(v); }
    inline Py py(size_t v) { return Py::integer(static_cast<unsigned long long>(v)); }
    inline Py py(const BigInt& v) { return Py::integer(v); }

    template<class... T> Py tup(const T&... v) { return Py::tuple({py(v)...}); }

    inline std::string join(const std::vector<std::string>& parts, const std::string& sep)
    {
        std::string out;
        for (size_t k = 0; k < parts.size(); ++k) out += (k ? sep : "") + parts[k];
        return out;
    }

    // str.partition(":") -> (head, tail); tail is empty when there is no separator.
    inline std::pair<std::string, std::string> partition_colon(const std::string& s)
    {
        const auto p = s.find(':');
        if (p == std::string::npos) return {s, ""};
        return {s.substr(0, p), s.substr(p + 1)};
    }
}
