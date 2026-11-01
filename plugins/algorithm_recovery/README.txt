Native algorithm recovery (migration in progress)
================================================

This plugin is being ported from algorithm_recovery/invfold and the separately
preserved firmware adapter. Its commands cover import, native grouping/ordering,
original-gate trace generation, guided SSE, folding, checked routing orders and arithmetic lifting
and RAM extraction. The C++ API
also implements coupled frame execution. The full
netlist-to-algorithm pipeline is not yet migrated. See
algorithm_recovery/CPP_MIGRATION_STATE.txt.

Build from the HAL repository root:

  cmake -S . -B build -DPL_ALGORITHM_RECOVERY=ON \
    -DPL_SEQUENTIAL_SYMBOLIC_EXECUTION=ON -DPL_Z3_UTILS=ON \
    -DPL_DATAFLOW=ON -DPL_MODULE_IDENTIFICATION=ON \
    -DPL_BITORDER_PROPAGATION=ON -DPL_BOOLEAN_INFLUENCE=ON \
    -DALGORITHM_RECOVERY_TESTS=ON
  cmake --build build --target hal-recover check-recovery-memory \
    check-recovery-ram-extraction check-recovery-folding \
    check-recovery-folding-fixture check-recovery-frontend \
    check-recovery-frontend-fixture check-recovery-observations \
    check-recovery-arithmetic check-recovery-arithmetic-fixture \
    check-recovery-routing check-recovery-routing-fixture -j 4

The native importer uses libsodium's SHA256 (development headers/library required)
to record exact input identities. It calls HAL's hgl_parser/verilog_parser plugins,
which must be available in the HAL plugin directory.

Import a raw recovered primitive JSON and recover groups/orders without Python:

  export HAL_BASE_PATH="$PWD/build" LD_LIBRARY_PATH="$PWD/build/lib"
  build/bin/hal_plugins/hal-recover import recovered.json \
    plugins/gate_libraries/definitions/ice40ultra_hal.hgl build/case_import
  build/bin/hal_plugins/hal-recover groups build/case_import/import.hal build/case_groups
  build/bin/hal_plugins/hal-recover extract-ram build/case_groups/ordered.hal build/case_ram.json

Output directories for import/groups/trace/analyze must be absent or empty. The importer
accepts one flat module with scalar pads. It preserves original cell insertion
order and primitive parameters, lowers scalar positive-edge IO $dff and the
specific tri-state $mux form, and checks all imported pins/parameters/global IO
aliases plus exhaustive two-state LUT/carry truth tables. Unknown/unsupported
cases fail explicitly. Generated IO simulation models leave FF initialization
unconstrained. Import checks do not prove sequential or silicon equivalence.

Group recovery calls native HAL DANA with minimum group size 8 and only physical
RAM pin-group structures. Its inferred groups feed actual MI (counter/add/sub,
max_controls=0), followed by strict bitorder propagation and relaxed propagation
only on unresolved targets. Strict results are checked unchanged. Only verified
MI operands/results and library RAM bus indices seed orders. Unresolved order
remains a storage coordinate. The current MI engine reports no subtraction
candidate generator; this existing limitation remains visible in its log.

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

Extract RAM metadata and symbolic port equations from an imported HAL netlist:

  HAL_BASE_PATH="$PWD/build" LD_LIBRARY_PATH="$PWD/build/lib" \
    build/bin/hal_plugins/hal-recover extract-ram input.hal output.json

The native plugin calls HAL SSE directly. No Python interpreter, script, original
RTL names, grouping table, or observed address schedule is invoked by this path.
HAL's shared plugin infrastructure itself links Python libraries; this is not a
claim that the entire HAL distribution has no Python build/link dependencies.

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

Real-case migration regression (local artifacts are intentionally not tracked):

  HAL_BASE_PATH="$PWD/build" LD_LIBRARY_PATH="$PWD/build/lib" \
    build/bin/hal_plugins/check-recovery-ram-extraction \
    ordered.hal native_ram.json recovered.json

The last two inputs are test evidence only. This regression checks exact pins,
clocks and INIT, then proves equality of each native port expression against
the previously validated Python adapter's expression with Z3. SAT and UNKNOWN
both fail the equality check. It also checks rejection of unsupported modes
and unknown INIT. This is migration equivalence of RAM extraction, not a new
independent whole-device proof. The .hal inputs were produced by the previous
Python frontend. The same regression can instead take a freshly generated
native ordered.hal; the import/groups commands above now provide that path.

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
It does not invoke Python. Power-up RAM state must be warmed to the graph origin
before real-case replay; initial_memories does not perform that warmup.

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

Native trace and guided recovery (requires iverilog and vvp on PATH):

  build/bin/hal_plugins/hal-recover trace recovered.json recovered.v \
    /usr/share/yosys/ice40/cells_sim.v build/case_trace
  build/bin/hal_plugins/hal-recover analyze build/case_groups/ordered.hal \
    build/case_groups/groups.json recovered.json build/case_trace build/case_analysis

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

analyze uses actual HAL SSE module pin groups and the FF's physical D/Q wiring.
Only combinational LUT/carry gates are included, so snapshots use normalized
SSE time zero; FF outputs and RAM reads remain state cutpoints. Observed primitive
control values specialize queries. Every specialization requires a nonvacuous
universal equality proof against an independent original-JSON LUT/carry
interpreter. All eight RAM port equations per instance are checked unconditionally.
Original-cone unknown constants, unsupported endpoints and feedback fail explicitly.
The last 512 events of each profile feed the typed C++ folder directly. The
analysis directory contains proof queries/hashes, sse_report.json and a compact
fold_report.json, plus lift_PROFILE/arithmetic.json and arithmetic proof queries.
This is not yet a standalone recovered-program export.

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
Neither production trace/analyze nor the production folder loads those artifacts.
MemoryWindow.model clock IDs remain HAL net IDs; GuidedAnalysis.physical_nets
maps them to physical trace IDs for a future real-case frame replay adapter.

Native routing-order recovery (routing.h):

  recover_routing(graph, HAL_window, empty_directory, options) -> RoutingOrders

analyze runs this stage after folding and before arithmetic lifting. It ports
InvFold's zero/one-hot probes and requires the same proposed permutation across
all writes of a word. These probes only propose an order. Acceptance requires
every write to have one full-width signal source with an established numeric
order, then a universal proof that the permuted result equals that ordered
source. Only UNSAT is accepted. Accepted orders propagate to dependent words
until a fixed point; SAT/UNKNOWN retain the unresolved order. The default proof
timeout is 30 seconds. Relative-only/partial-width proposals remain diagnostic.
Words absent from the graph explicitly report NO_WRITES_IN_GRAPH; unavailable
graphs report NO_GRAPH. No user-provided semantic anchors are added.

Each permutation maps raw bit j to canonical bit permutation[j]. Routing and
arithmetic never mutate the executable graph's physical coordinates. Arithmetic
first substitutes inverse permutations on affected source operands, then
permutes the output; both transformations and raw/canonical hashes are recorded.
This also applies when only a source's order changed and the result's order was
already known. Routing queries/results/hashes are saved in routing_PROFILE;
only the accepted permutations enter lift_PROFILE/arithmetic.json.

The routing CTest checks a two-level chain requiring repeated propagation,
every-write obligations and saved-query replay, actual downstream arithmetic,
unanchored cycles, differing phase maps, missing/duplicated bits, partial-width
and non-signal sources, 80-bit proposals, a rare nonlinear one-hot impostor,
SAT/UNKNOWN, invalid metadata and unchanged raw execution coordinates.

Routing migration regression:

  build/bin/hal_plugins/check-recovery-routing-fixture \
    PREVIOUS_REFERENCE_CASE_DIRECTORY build/case_routing_regression
  build/bin/hal_plugins/check-recovery-routing-fixture \
    PREVIOUS_REFERENCE_CASE_DIRECTORY build/case_routing_integration \
    build/case_analysis

The first runs native inference on the preserved graph and compares exact
relative proposals and all active word decisions. The second checks the actual
fresh analyze output: accepted orders, decisions, proposal inventory and call
counts. Relative permutation coordinates may differ with unresolved HAL storage
enumeration, so the second form does not equate those arbitrary permutations.
The reference controller/HMD graphs accepted zero additional routing orders;
synthetic positive tests therefore remain necessary for validating this stage.

Native arithmetic lifting (arithmetic.h, evaluation.h, lifting.h):

  recover_arithmetic(raw_BV, numeric_order_known, options) -> ArithmeticResult
  verify_arithmetic(raw_BV, ArithmeticModel, options) -> ArithmeticResult
  ArithmeticRecovery::recover(...) -> same result, with reusable canonical proofs
  lift_arithmetic(graph, recovery, empty_directory, routing_orders) -> report and proofs

The native candidate generator ports InvFold's coefficient-class method: small
linearity screen, exhaustive narrow probes, rational coefficient intervals,
dyadic selection, bisection for larger exhaustive sets, wrap-aware refinement,
joint fractional constant constraints, and a joint search for narrow sources.
Literal bit-vector extracts also propose the exact dyadic projection directly,
then pass through the same proof gate. This handles a physical bit moving from
the sign position to an interior position when an unresolved storage list is
re-enumerated; it neither needs nor infers a numeric bit order.
Source widths and constants use arbitrary-precision integers/rationals. Default
limits remain 30 seconds per candidate and 30 seconds per proof; timeout is not
a counterexample. The deterministic native sampler uses std::mt19937_64; its
probe stream is not claimed byte-identical to Python's RNG. Candidate labels
can differ; accepted semantic coverage is checked against the old outputs.

The serialized model is reconstructed before proof. exact/floor/round use an
adequately wide signed accumulator, explicit signed/unsigned source extension,
arithmetic right shift (round means floor(x + 1/2)), and final modular truncation.
Only UNSAT of raw != reconstructed model produces FIT_PROVED. SAT is FIT_REFUTED,
UNKNOWN is FIT_UNKNOWN, and fitting expiry is RAW_FIT_TIMEOUT. A quant candidate
is recorded separately as RAW_QUANT_CANDIDATE: its sampled error bound is not
adopted as an executable equation or a universal guarantee. RAW_UNORDERED remains
unless routing has proved its order, and all original folded equations stay available.

ArithmeticRecovery caches by exact expression shape under a bijective variable
renaming, source/output widths and unsigned interpretations. Each reused proof
records canonical-name -> actual-source-name bindings; model coefficients are
renamed consistently. It does not infer a numeric order. The analyze command
shares this cache across profiles and records every node's result, accepted fit
or unadopted candidate, raw-expression hash and proof hash/binding.

BitVectorProgram compiles the Z3 expression DAG into native arbitrary-width
instructions. It supports the Boolean/BV operations used by the old SMT2
compiler, including signed division/remainder, zero-divisor cases, rotations,
extension and shifts past the word width. Unsupported sorts/operations and
ambiguous same-name source sorts fail explicitly. No Python is invoked for
sampling, fitting, model encoding or proof. The primitive/evaluator CTests check
12288 assignments against Z3, wide arithmetic, exact/floor/round/unsigned fits,
coefficient refinement at 9/13/80 bits, cached renaming, multiple rounding,
rare guarded errors, UNKNOWN and expiry.

Arithmetic migration regression:

  build/bin/hal_plugins/check-recovery-arithmetic-fixture \
    PREVIOUS_REFERENCE_CASE_DIRECTORY build/case_arithmetic_regression

Check coverage of the actual fresh analyze output by node ID, including query
identities and absence of adopted unproved models:

  build/bin/hal_plugins/check-recovery-arithmetic-fixture \
    PREVIOUS_REFERENCE_CASE_DIRECTORY build/case_arithmetic_integration \
    build/case_analysis

The first form reads the saved graphs as regression input and runs native
proposals and proofs for every numerically ordered node. The second checks the
recorded outcomes of the fresh production run (it is not a separate whole-device
verifier). Both require every formerly proved node to remain proved, check
unknown-order preservation, and save complete comparison results.
Production analyze obtains its equations from the new
native SSE/folder; the reference graphs are never a production dependency.

Standalone program export and real-case coupled replay remain required migration
steps; routing and arithmetic annotations do not replace them.
