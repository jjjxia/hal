// Python code printer for z3 bit-vector expressions (port of decompile.py's Printer).
#pragma once
#include "python_format.h"
#include <z3++.h>
#include <functional>
#include <map>
#include <string>
#include <vector>

namespace program_recovery
{
    class Printer
    {
    public:
        using Env = std::function<std::string(const std::string&)>;
        explicit Printer(Env env) : m_env(std::move(env)) {}

        std::string emit(const z3::expr& e);
        std::vector<std::string> lines;

    private:
        std::string build(const z3::expr& e);
        Env m_env;
        std::map<unsigned, std::string> m_memo;
        unsigned m_count = 0;
    };

    std::string numeral_hex(const z3::expr& e);
    BigInt numeral_value(const z3::expr& e);
}
