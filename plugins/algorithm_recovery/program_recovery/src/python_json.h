// JSON values with arbitrary-precision integers, written exactly like Python's json.dump(obj, indent=1).
#pragma once
#include "python_format.h"
#include <charconv>
#include <cmath>
#include <string>
#include <utility>
#include <vector>

namespace program_recovery
{
    struct PJ
    {
        enum class Kind { null, boolean, integer, real, string, list, dict } kind = Kind::null;
        bool b = false;
        BigInt i = 0;
        double d = 0;
        std::string s;
        std::vector<PJ> items;
        std::vector<std::pair<std::string, PJ>> fields;

        PJ() = default;
        PJ(std::nullptr_t) {}
        PJ(bool v) : kind(Kind::boolean), b(v) {}
        PJ(int v) : kind(Kind::integer), i(v) {}
        PJ(long v) : kind(Kind::integer), i(v) {}
        PJ(long long v) : kind(Kind::integer), i(v) {}
        PJ(unsigned v) : kind(Kind::integer), i(v) {}
        PJ(unsigned long v) : kind(Kind::integer), i(v) {}
        PJ(unsigned long long v) : kind(Kind::integer), i(v) {}
        PJ(const BigInt& v) : kind(Kind::integer), i(v) {}
        PJ(double v) : kind(Kind::real), d(v) {}
        PJ(const char* v) : kind(Kind::string), s(v) {}
        PJ(const std::string& v) : kind(Kind::string), s(v) {}

        static PJ list(std::vector<PJ> v = {}) { PJ p; p.kind = Kind::list; p.items = std::move(v); return p; }
        static PJ dict() { PJ p; p.kind = Kind::dict; return p; }

        PJ& set(const std::string& key, PJ value)
        {
            for (auto& [k, v] : fields) if (k == key) { v = std::move(value); return *this; }
            fields.emplace_back(key, std::move(value));
            return *this;
        }
        PJ* get(const std::string& key)
        {
            for (auto& [k, v] : fields) if (k == key) return &v;
            return nullptr;
        }
        void push(PJ v) { items.push_back(std::move(v)); }

        std::string dump(int indent = 1) const { std::string out; write(out, indent, 0); return out; }

    private:
        static std::string quote(const std::string& s)
        {
            std::string out = "\"";
            // ensure_ascii=True: non-ASCII as \uXXXX (UTF-8 decoded, surrogate pairs above the BMP)
            for (size_t k = 0; k < s.size();)
            {
                unsigned char c = static_cast<unsigned char>(s[k]);
                uint32_t cp = c;
                size_t len = 1;
                if (c >= 0x80)
                {
                    if ((c & 0xe0) == 0xc0) { cp = c & 0x1f; len = 2; }
                    else if ((c & 0xf0) == 0xe0) { cp = c & 0x0f; len = 3; }
                    else { cp = c & 0x07; len = 4; }
                    for (size_t q = 1; q < len && k + q < s.size(); ++q) cp = (cp << 6) | (static_cast<unsigned char>(s[k + q]) & 0x3f);
                }
                k += len;
                auto hex4 = [&](uint32_t v) {
                    static const char* hx = "0123456789abcdef";
                    out += "\\u"; out += hx[(v >> 12) & 15]; out += hx[(v >> 8) & 15]; out += hx[(v >> 4) & 15]; out += hx[v & 15];
                };
                if (cp == '"') out += "\\\"";
                else if (cp == '\\') out += "\\\\";
                else if (cp == '\n') out += "\\n";
                else if (cp == '\r') out += "\\r";
                else if (cp == '\t') out += "\\t";
                else if (cp == '\b') out += "\\b";
                else if (cp == '\f') out += "\\f";
                else if (cp < 0x20 || (cp >= 0x7f && cp < 0x10000)) hex4(cp);
                else if (cp >= 0x10000) { const uint32_t v = cp - 0x10000; hex4(0xd800 | (v >> 10)); hex4(0xdc00 | (v & 0x3ff)); }
                else out += static_cast<char>(cp);
            }
            return out + "\"";
        }

        static std::string real(double v)
        {
            if (std::isnan(v)) return "NaN";
            if (std::isinf(v)) return v > 0 ? "Infinity" : "-Infinity";
            // Python float repr: shortest round-trip digits, scientific when exp < -4 or >= 16
            char buf[64];
            auto res = std::to_chars(buf, buf + sizeof(buf), v, std::chars_format::scientific);
            std::string sci(buf, res.ptr);
            const auto e = sci.find('e');
            std::string mant = sci.substr(0, e);
            const int exp = std::stoi(sci.substr(e + 1));
            std::string sign;
            if (!mant.empty() && mant[0] == '-') { sign = "-"; mant = mant.substr(1); }
            std::string digits;
            for (char c : mant) if (c != '.') digits += c;
            while (digits.size() > 1 && digits.back() == '0') digits.pop_back();
            if (exp < -4 || exp >= 16)
            {
                std::string m = digits.substr(0, 1);
                if (digits.size() > 1) m += "." + digits.substr(1);
                char eb[16];
                std::snprintf(eb, sizeof(eb), "e%c%02d", exp < 0 ? '-' : '+', exp < 0 ? -exp : exp);
                return sign + m + eb;
            }
            if (exp >= 0)
            {
                std::string ip = digits.size() > static_cast<size_t>(exp + 1) ? digits.substr(0, exp + 1) : digits + std::string(exp + 1 - digits.size(), '0');
                std::string fp = digits.size() > static_cast<size_t>(exp + 1) ? digits.substr(exp + 1) : "0";
                return sign + ip + "." + fp;
            }
            return sign + "0." + std::string(-exp - 1, '0') + digits;
        }

        void write(std::string& out, int indent, int level) const
        {
            switch (kind)
            {
                case Kind::null: out += "null"; return;
                case Kind::boolean: out += b ? "true" : "false"; return;
                case Kind::integer: out += i.str(); return;
                case Kind::real: out += real(d); return;
                case Kind::string: out += quote(s); return;
                case Kind::list:
                {
                    if (items.empty()) { out += "[]"; return; }
                    out += "[";
                    for (size_t k = 0; k < items.size(); ++k)
                    {
                        out += k ? ",\n" : "\n";
                        out += std::string(static_cast<size_t>((level + 1) * indent), ' ');
                        items[k].write(out, indent, level + 1);
                    }
                    out += "\n" + std::string(static_cast<size_t>(level * indent), ' ') + "]";
                    return;
                }
                case Kind::dict:
                {
                    if (fields.empty()) { out += "{}"; return; }
                    out += "{";
                    for (size_t k = 0; k < fields.size(); ++k)
                    {
                        out += k ? ",\n" : "\n";
                        out += std::string(static_cast<size_t>((level + 1) * indent), ' ');
                        out += quote(fields[k].first) + ": ";
                        fields[k].second.write(out, indent, level + 1);
                    }
                    out += "\n" + std::string(static_cast<size_t>(level * indent), ' ') + "}";
                    return;
                }
            }
        }
    };
}
