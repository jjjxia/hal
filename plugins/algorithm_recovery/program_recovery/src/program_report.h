// Structure report of the generated programs: loops, lifting, tables, arithmetic, word orders, sizes.
#pragma once
#include "lifted_program.h"

namespace program_recovery
{
    PJ program_report(const EventGraph& M, const ProgramBuilder& D, const ProgramLifter& L);
}
