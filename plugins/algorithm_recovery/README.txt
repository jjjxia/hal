Native algorithm recovery
=========================

This plugin recovers the algorithm of a time-multiplexed iCE40 netlist in two C++ tools:

  hal-recover      netlist -> certified event graph (HAL plugin; links HAL and its z3 4.8.12)
    import         recovered flat primitive JSON -> checked HAL netlist           (src/frontend.cpp)
    groups         DANA register groups, MI order seeds, bit-order propagation    (src/grouping.cpp)
    trace          original-gate trace with Icarus                                 (src/observations.cpp)
    graph          schedule certificate (src/taint.cpp, src/certify.cpp), certified SSE word
                   equations (src/guided.cpp, src/memory.cpp), RAM model check (src/memory_replay.cpp,
                   src/evaluation.cpp) and the event graph (src/folding.cpp); writes certificate/,
                   graph_<profile>.json and fold_report.json
  recover-program  event graph -> untimed programs and folded RTL (program_recovery/, see its README.txt;
                   does not link HAL; links z3 5.3.3 from the pip package z3-solver)
    generate       flat, rolled and lifted Python programs, interface.json, report.json
    rtl            folded Verilog, validated with Icarus against the original-gate trace

The generated programs are checked by CPython scripts in algorithm_recovery/evaluation/program_checks:
validate_programs.py (held-out transactions of the trace) and close_chain.py (end-to-end proof against the
certified equations). Scoring and experiment runners are in algorithm_recovery/evaluation.

Inputs are already recovered flat primitive JSON plus its matching original
gate Verilog and an iCE40 gate library/model. This is not a bitstream decoder.
Grouping/order/clock/reset/RAM bindings come from physical structure, HAL and
the extension, without manual semantic word groups or observed RAM-read inputs.

Build from the HAL repository root (recover-program needs the z3 of z3-solver 5.3.3):

  cmake -S . -B build -DPL_ALGORITHM_RECOVERY=ON \
    -DPL_SEQUENTIAL_SYMBOLIC_EXECUTION=ON -DPL_Z3_UTILS=ON \
    -DPL_DATAFLOW=ON -DPL_MODULE_IDENTIFICATION=ON \
    -DPL_BITORDER_PROPAGATION=ON -DPL_BOOLEAN_INFLUENCE=ON \
    -DPROGRAM_RECOVERY_Z3_ROOT=<venv>/lib/python3.X/site-packages/z3 \
    -DALGORITHM_RECOVERY_TESTS=ON
  cmake --build build --target hal-recover recover-program -j 4

Both tools are written to build/bin/hal_plugins/.

The native importer uses libsodium's SHA256 (development headers/library required)
to record exact input identities. It calls HAL's hgl_parser/verilog_parser plugins,
which must be available in the HAL plugin directory.

Import a raw recovered primitive JSON and recover groups/orders:

  export HAL_BASE_PATH="$PWD/build" LD_LIBRARY_PATH="$PWD/build/lib"
  build/bin/hal_plugins/hal-recover import recovered.json \
    plugins/gate_libraries/definitions/ice40ultra_hal.hgl build/case_import
  build/bin/hal_plugins/hal-recover groups build/case_import/import.hal build/case_groups

Output directories for import/groups/trace/graph must be absent or empty. The importer
accepts one flat module with scalar pads. It preserves original cell insertion
order and primitive parameters, lowers scalar positive-edge IO $dff and the
specific tri-state $mux form, and checks all imported pins/parameters/global IO
aliases plus exhaustive two-state LUT/carry truth tables. Unknown/unsupported
cases fail explicitly. Generated IO simulation models leave FF initialization
unconstrained. Import checks do not prove sequential or silicon equivalence.

Group recovery calls native HAL DANA with minimum group size 8 and only physical
RAM pin-group structures. Its inferred groups feed actual MI (all HAL-selectable
candidate types, max_control_signals=3 by default), followed by strict bitorder propagation and relaxed propagation
only on unresolved targets. Strict results are checked unchanged. Only verified
MI operands/results and library RAM bus indices seed orders. Equality and
value-check-only candidates remain in the identification report but do not seed
absolute numeric significance: their operand permutations do not fix bit weights.
Unresolved order remains a storage coordinate. HAL checks subtraction via the
addition selector; subtraction is not a separate selectable generator. The old
warning from explicitly selecting it did not establish missing subtraction support.

Configure identification explicitly (no manual semantic seeds):

  hal-recover groups input.hal build/expanded_groups \
    --mi-types all --mi-controls 3 --mi-threads 1 --dana-threads 4

The equivalent legacy selection is --mi-types counter,addition --mi-controls 0.
Explicit type lists use HAL's selectable names, case-insensitively; unsupported
selectors are errors.
Actual settings and numeric-seed eligibility are saved in the group/MI reports.
HAL retains its own per-candidate heuristics (for example, its comparison path
proposes at most one control); the configured maximum is not a completeness claim.


Configure observation lengths and select named profiles:

  hal-recover trace recovered.json recovered.v cells_sim.v build/long_pattern \
    --profiles pattern --profile-length 24576 --seed 20261031
  hal-recover graph ordered.hal groups.json recovered.json build/long_pattern \
    build/long_graph --profiles pattern --window 17472

For a benchmark with an external reset/start/sample protocol, supply physical
pad events without internal-state or word hints:

  hal-recover trace recovered.json recovered.v cells_sim.v build/protocol_trace \
    --stimulus-json protocol.json

The PHYSICAL_PAD_STIMULUS_V1 JSON has source_sha256, input_bits (each physical
input including clocks exactly once), events (rows of integer 0/1 values),
windows (named contiguous non-overlapping extraction-event arrays), and a
description. Its source identity, input inventory and window bounds are checked.
No automatic reset or clock stimulus overrides these rows. The native testbench
settles data pads before applying clock pads for each event. This option is
exclusive with --profiles/--profile-length/--seed. The stimulus file and explicit
protocol prior are recorded in trace and downstream reports. Protocol legality
is not proved by accepting the file. Extraction windows never exclude other
events from full-state replay; all known observations are compared from zero.

Defaults remain all four trace profiles, 768 events each, and a 512-event
extraction window. Profile selection on trace creates a new simulation starting
from INIT/reset, in the specified profile order; it does not retain the state
of omitted preceding profiles. Analysis selection only chooses among the actual
saved profile windows and does not alter observations. Reports record selected
profiles, lengths and extraction indices. All original FF/RAM/proof checks
still apply. NO_GRAPH diagnostics report the available window and the required
four complete joint periods; a larger window may expose different local periods
and does not itself guarantee recovery. Long windows construct control snapshots
only when a new HAL SSE variant is needed, avoiding redundant per-event maps.

Schedule certificate (graph --protocol pad_protocol.json): the protocol lists the non-data pads, which keep
their recorded values in every admitted run; every other pad is free data. R_t fixes every untainted FF to
its recorded value. Initiation is checked against primitive power-up values, B_t on the prefix and C_j over
one period; every step also discharges the recorded value of each untainted FF control net, which is the
condition of the conditional SSE word proofs. Failed consequents are tainted and the analysis repeats
(Houdini-style refinement). Extraction is then restricted to the certified periodic region.

Compare identification configurations by physical FF coordinates:

  build/bin/hal_plugins/check-recovery-grouping-comparison \
    build/old_groups/groups.json build/new_groups/groups.json build/order_comparison.json

This requires identical DANA membership and Q/D/gate wiring, then reports added,
lost and changed known orders independently of module names or unresolved list
enumeration. A lost or conflicting old order fails the comparison. This checks
preservation and coverage; it is not an independent proof of HAL-inferred weights.

DANA now has Configuration::max_thread_count (also exposed to its Python binding),
covering processing and evaluation workers; zero keeps automatic selection.
The recovery path uses 4 DANA workers and 1 MI worker without LD_PRELOAD or a
compiled thread-cap shim. No manual known-register list is provided to DANA.

Real-case frontend/grouping regression:

  build/bin/hal_plugins/check-recovery-frontend-fixture \
    build/case_import build/case_groups PREVIOUS_REFERENCE_CASE_DIRECTORY

This checks exact imported primitive graphs, source mapping and identity hashes,
DANA membership and full FF coverage, all MI/library order seeds, exact recovered
numeric orders, unresolved storage membership and Q/D wiring. Module IDs are not
assumed semantically significant. Grouping must leave primitive gates unchanged.


Supported memory: SB_RAM40_4K, READ_MODE=WRITE_MODE=0, primary-input rising-edge
clocks, concrete hexadecimal bit_vector INIT parameters, LUT/carry cones with
FF/RAM output cutpoints. Unsupported modes, missing pins/parameters, unknown
INIT bits, and unsupported cone cells return explicit errors. Other architectures
are not covered by this primitive adapter. All eight port functions remain
symbolic. Pin lists and expression net_ID_0 symbols refer to native HAL net IDs.
No claim of logical RAM bank recovery is made.

step_ram implements synchronous held read data, independent clock enables,
inverted per-bit write mask and read-before-write simultaneous edges. RamState's
initial read=0 is one explicit two-state choice for the uninitialized output
register, not inferred hardware initialization. The cell's storage INIT is
extracted exactly; no trace contents substitute for it.

Independent primitive regression (requires iverilog and Yosys cells_sim.v):

  ctest --test-dir build/plugins/algorithm_recovery --output-on-failure

This test runs 512 events with arbitrary nonzero INIT, independent read/write
edges, held reads, collisions, full/partial masks and high address bits. It
compares native outputs and final storage with the actual Yosys primitive, and
requires three deliberately wrong transition rules to disagree.


Native folding API (include/algorithm_recovery/folding.h):

  fold_observed(FoldInput, context) -> FoldGraph
  initial_memories(FoldGraph, initial_read) -> extracted power-up RAM state
  run_frame(FoldGraph, history, boundary_inputs, warmed_RAM_state, context)

FoldInput contains HAL-derived groups in physical storage coordinates, effective
D equations including reset, observed physical write classifications and a
contiguous extraction window. Primitive bit symbols are packed into words in
C++ without introducing numeric bit orders. Unknown, mixed, inactive and
nonperiodic words remain explicit boundary inputs. Period detection checks the
entire window. Construction requires one real prefix and three repetitions;
last writes are resolved strictly before the reading event and checked in all
three repetitions. Too short a common-period window returns available=false.
This is an observed-period contract, not an unbounded schedule certificate.

RAM read outputs are internal state endpoints. RAM ports retain symbolic data
and control dependencies resolved through the same graph. Every RAM port is
evaluated before any memory is updated, and FF nodes sample pre-event reads.
The raw native executor uses Z3's fixed-width evaluator and accepts words wider
than machine integers; missing or wrong-width boundary state fails explicitly.
It does not invoke Python. initial_memories gives the extracted power-up RAM state; run_frame and
initial_memories are used by the folding test to execute folded graphs.

check-recovery-folding exercises wide-word recurrence against independent
integer arithmetic, actual last writes, held/mixed/nonperiodic words, insufficient
windows and two coupled RAMs. These tests are included in the CTest command above.

Real-case folding migration regression:

  build/bin/hal_plugins/check-recovery-folding-fixture CASE_DIRECTORY recovered.json

Only this test adapter reads the old native_sse.json/native_ram.json artifacts;
they are converted to the typed production API. The folder does not read an old
graph or invoke the old InvFold. The test then compares all seven existing graphs
across the two cases: periods, origins, every FF node's source bindings and
universally equivalent expression, history entries and every RAM port binding.
It also checks the HMD pattern's existing NO_GRAPH outcome. This fixture-only
invocation does not test trace/SSE generation or real-case frame replay.


Native trace and certified SSE (requires iverilog and vvp on PATH):

  build/bin/hal_plugins/hal-recover trace recovered.json recovered.v \
    /usr/share/yosys/ice40/cells_sim.v build/case_trace
  build/bin/hal_plugins/hal-recover graph build/case_groups/ordered.hal \
    build/case_groups/groups.json recovered.json build/case_trace build/case_graph \
    --window 512 --profiles training --protocol pad_protocol.json

Trace generation discovers primary clocks by FF fanout and determines inactive
pad-controlled resets with SAT. Four generic pad profiles are used; X/Z are
retained, and tristate pads release the external driver when the original
internal output enable is active. It emits the testbench in C++ and invokes
Icarus directly with argv, without a shell or Python. Receipt hashes bind original
JSON/Verilog, Yosys primitive model, stimulus, testbench, trace and final RAM dump.
Loading rejects changed artifacts, malformed rows and truncated observations.
For exact comparison with the old stimulus stream, a native integer-seed
initialization feeds std::mt19937 (32-bit seed). Algorithm reference:
https://github.com/python/cpython/blob/v3.12.0/Modules/_randommodule.c
(init_by_array, MT19937 Nishimura/Matsumoto initialization); no Python RNG call.

graph uses actual HAL SSE module pin groups and the FF's physical D/Q wiring.
Only combinational LUT/carry gates are included, so snapshots use normalized
SSE time zero; FF outputs and RAM reads remain state cutpoints. Observed primitive
control values specialize queries. Every specialization requires a nonvacuous
universal equality proof against an independent original-JSON LUT/carry
interpreter. All eight RAM port equations per instance are checked unconditionally.
Original-cone unknown constants, unsupported endpoints and feedback fail explicitly.
The extraction window of each profile feeds the typed C++ folder directly. The
graph directory contains proof queries/hashes, sse_report.json, fold_report.json and graph_PROFILE.json.

FF event classification follows the actual primitive: SR/SS are synchronous,
R/S asynchronous, E has the priority specified by Yosys, and N uses the falling
edge. Unknown relevant controls/clocks remain unknown. Classification is for
the sampled, settled pre/post-clock events; it is not a general asynchronous
event scheduler. The primitive CTest compares all 20 iCE40 FF variants with
Yosys on 512 events (10240 bits), including holds and enabled/disabled reset/set.
Wrong sync/async, clock polarity and enable semantics must each be detected.
LUT input ordering, unknown parameters/cones and receipt corruption are tested.

Real-case regression using the fresh native path:

  build/bin/hal_plugins/check-recovery-folding-fixture \
    PREVIOUS_REFERENCE_CASE_DIRECTORY recovered.json \
    build/case_groups/ordered.hal build/case_groups/groups.json \
    build/case_trace build/case_analysis_regression

This invokes native guided analysis on the new trace, checks every extracted
window's word membership/order/write mask and universal equation equality, then
folds the NEW equations and compares every node and RAM binding with the saved
reference. Known numeric orders must match exactly. Unresolved storage lists may
be enumerated differently after import: only the test permutes them by physical
Q-bit identity on both the operands and results before comparing semantics.
This does not infer a numeric order or change the production graph.
Old Python artifacts are read only by this test as regression evidence.
Neither production trace/graph nor the production folder loads those artifacts.
MemoryWindow.model clock IDs remain HAL net IDs; GuidedAnalysis.physical_nets
maps them to physical trace IDs.


RAM model check (memory_replay.h): graph replays the extracted RAM port functions over the trace before
folding and writes memory_replay/memory_replay.json.
RAM warmup begins with extracted INIT and an explicit initial held-read choice
of zero. All symbolic port functions evaluate against old internal RAM read
registers, followed by independent read/write clock edges and masked updates.
Other physical FF/pad cuts come from the original trace; unknown operands use
zero with counts. Observed RAM reads are comparisons only, never inputs. Warmup
checks each port, pre/post read values and the final original-simulator storage
dump, preserving known/unknown counts. Original RAM instances are matched by
their exact physical RDATA buses rather than guessed semantic names.

Regression tests (iverilog and Yosys cells_sim.v needed for the primitive tests):

  ctest --test-dir build/plugins/algorithm_recovery --output-on-failure

