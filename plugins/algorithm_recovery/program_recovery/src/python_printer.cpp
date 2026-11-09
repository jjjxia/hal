#include "python_printer.h"
#include "event_graph.h"

namespace program_recovery
{
    BigInt numeral_value(const z3::expr& e) { return BigInt(Z3_get_numeral_string(e.ctx(), e)); }
    std::string numeral_hex(const z3::expr& e) { return py_hex(numeral_value(e)); }

    std::string Printer::emit(const z3::expr& e)
    {
        const unsigned key = e.id();
        if (auto it = m_memo.find(key); it != m_memo.end()) return it->second;
        std::string code = build(e);
        if (e.is_app() && e.num_args() > 0 && code.size() > 40)
        {
            ++m_count;
            const std::string name = "t" + std::to_string(m_count);
            lines.push_back("    " + name + " = " + code);
            code = name;
        }
        m_memo[key] = code;
        return code;
    }

    std::string Printer::build(const z3::expr& e)
    {
        require(e.is_app(), "printer: not an application");
        const Z3_decl_kind k = e.decl().decl_kind();
        std::vector<std::string> a;
        for (unsigned i = 0; i < e.num_args(); ++i) a.push_back(emit(e.arg(i)));
        const unsigned w = e.is_bv() ? e.get_sort().bv_size() : 1;
        const std::string m = py_hex(mask_of(w));
        if (k == Z3_OP_BNUM) return numeral_hex(e);
        if (k == Z3_OP_TRUE) return "True";
        if (k == Z3_OP_FALSE) return "False";
        if (e.num_args() == 0 && k == Z3_OP_UNINTERPRETED) return m_env(sym_name(e));
        auto param = [&](unsigned i) { return static_cast<unsigned>(Z3_get_decl_int_parameter(e.ctx(), e.decl(), i)); };
        switch (k)
        {
            case Z3_OP_EXTRACT:
            {
                const unsigned hi = param(0), lo = param(1);
                return "((" + a[0] + " >> " + std::to_string(lo) + ") & " + py_hex(mask_of(hi - lo + 1)) + ")";
            }
            case Z3_OP_CONCAT:
            {
                std::vector<std::string> parts;
                unsigned shift = 0;
                for (int i = static_cast<int>(e.num_args()) - 1; i >= 0; --i)
                {
                    parts.push_back(shift ? "(" + a[i] + " << " + std::to_string(shift) + ")" : a[i]);
                    shift += e.arg(i).get_sort().bv_size();
                }
                return "(" + join(parts, " | ") + ")";
            }
            case Z3_OP_BNOT: return "(" + a[0] + " ^ " + m + ")";
            case Z3_OP_BAND: return "(" + join(a, " & ") + ")";
            case Z3_OP_BOR: return "(" + join(a, " | ") + ")";
            case Z3_OP_BXOR: return "(" + join(a, " ^ ") + ")";
            case Z3_OP_BADD: return "((" + join(a, " + ") + ") & " + m + ")";
            case Z3_OP_BSUB: return "((" + a[0] + " - " + a[1] + ") & " + m + ")";
            case Z3_OP_BMUL: return "((" + join(a, " * ") + ") & " + m + ")";
            case Z3_OP_BNEG: return "((-" + a[0] + ") & " + m + ")";
            case Z3_OP_BSHL:
                return "((" + a[0] + " << " + a[1] + ") & " + m + " if " + a[1] + " < " + std::to_string(w) + " else 0)";
            case Z3_OP_BLSHR:
                return "((" + a[0] + " >> " + a[1] + ") if " + a[1] + " < " + std::to_string(w) + " else 0)";
            case Z3_OP_BASHR: return "(sra(" + a[0] + ", " + a[1] + ", " + std::to_string(w) + "))";
            case Z3_OP_ITE: return "(" + a[1] + " if " + a[0] + " else " + a[2] + ")";
            case Z3_OP_EQ: return "(" + a[0] + " == " + a[1] + ")";
            case Z3_OP_NOT: return "(not " + a[0] + ")";
            case Z3_OP_AND: return "(" + join(a, " and ") + ")";
            case Z3_OP_OR: return "(" + join(a, " or ") + ")";
            case Z3_OP_ULEQ: return "(" + a[0] + " <= " + a[1] + ")";
            case Z3_OP_ULT: return "(" + a[0] + " < " + a[1] + ")";
            case Z3_OP_SLEQ:
            case Z3_OP_SLT:
            {
                const std::string sw = std::to_string(e.arg(0).get_sort().bv_size());
                const std::string op = k == Z3_OP_SLEQ ? "<=" : "<";
                return "(sgn(" + a[0] + ", " + sw + ") " + op + " sgn(" + a[1] + ", " + sw + "))";
            }
            case Z3_OP_SIGN_EXT:
            {
                const unsigned ext = param(0), sw = e.arg(0).get_sort().bv_size();
                return "(sgn(" + a[0] + ", " + std::to_string(sw) + ") & " + py_hex(mask_of(sw + ext)) + ")";
            }
            case Z3_OP_ZERO_EXT: return a[0];
            default: throw std::runtime_error("operator " + e.decl().name().str());
        }
    }
}
