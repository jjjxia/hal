Program recovery (C++): event graph -> untimed programs, end-to-end check, folded RTL
===================================================================================
C++ port (2026-11-08) of the Python reference in algorithm_recovery/evaluation/program_recovery_python.

Goal (user, 2026-11-08): everything except evaluation scoring in C++. The C3 backend
(algorithm_recovery/evaluation/program_recovery_python: decompile.py, lift.py, lift_arith.py, close_chain.py,
emit_rtl.py) is ported here. Scoring stays in Python (check_reference.py, check_stream.py,
present_truth.py, arf_truth.py, library_designs/*, kernel_foldings/*, synthetic_foldings/*).

Layout
  src/python_format.h  Python-compatible repr/hex helpers, big integers
  src/event_graph.*    graph loading, certified specialisation, retiming, observations, liveness
  src/transaction_program.*  flat program and roll-up (decompile.py)
  src/lift_tables.*, lift_arithmetic.*, lifted_program.*  lifting (lift.py, lift_arith.py)
  src/python_interpreter.*  restricted Python interpreter (concrete and symbolic)
  src/trace_validation.*  trace validation and report.json
  src/equation_check.cpp  end-to-end check (close_chain.py)
  src/folded_rtl.cpp  folded RTL and Icarus validation (emit_rtl.py)
  src/main.cpp  CLI recover-program generate|check|rtl
  tools/compare_programs.py  comparison up to commutative operand order (verification only)

Z3 version
  The Python backend uses z3-solver 5.3.3 (build/c3_venv). HAL links system z3 4.8.12.
  simplify() differs between versions, so byte-identical comparison with the frozen Python
  outputs is only possible against 5.3.3. This executable therefore builds standalone
  against the venv's libz3 (headers in site-packages/z3/include). Do not link it into the
  HAL process, which already loads libz3 4.8.12.

Acceptance (first version, before porting; superseded below)
  1. program_flat.py and program_rolled.py byte-identical to the frozen Python outputs.
  2. program.py (lifted) byte-identical, except where a proof outcome depends on a solver
     timeout; every such case is listed.
  3. report.json and interface.json: same content (timings excluded).
  4. close_chain: same verdicts and counts per program.
  5. emit_rtl: identical Verilog and identical trace comparison results.

Finding (2026-11-08): byte identity is not attainable in general. z3's simplify orders the
arguments of commutative operators by AST id, and ids depend on the context's allocation
history, including id recycling when Python wrappers are freed. Even the term structure can
change (e.g. (= a b) and (= b a) shared or not), which changes the number of printer
temporaries. Flat/rolled on 53 designs (build/bench2_e5 + build/e8/bambu): 36 identical or equal up to
commutative order and temporaries (tools/compare_programs.py), 17 differ by 1-21 lines.

Acceptance (revised)
  A. Byte-identical, or equal under tools/compare_programs.py, where possible.
  B. Every C++ program passes the end-to-end check against the certified equations; cross-
     checked with the unchanged Python checker (close_chain_deep.py) until the C++ checker exists.
  C. Structural results identical: loops (phases, words, counters, iteration constants), lifting
     statistics, tables, arithmetic candidates (kind, width, coefficients, proof status), state
     values, data pad reads, observations, trace validation counts.
  D. Line-count differences are listed per design.
  Corpus: build/bench2_e5 (sweep), build/bench2_v1 (initial 8, final program versions),
  build/e8/bambu (E8.3 grid). The Python reference is frozen since 2026-11-02 23:26 (a08cb3f).

Python semantics to replicate
  - str(z3 const) is the raw decl name (no |bars|); use decl().name().str().
  - Python dict order = insertion order; JSON objects read with nlohmann::ordered_json.
  - repr of tuples/lists/strings; hex() lowercase; 1-tuples "(x,)".
  - random.Random(20261106).getrandbits(k): CPython MT19937 with init_by_array([seed]).
  - z3.Concat(*args) is a left fold of Z3_mk_concat.

Status
  [x] stage 1 flat program   [x] stage 2 roll-up   [x] stage 3 lifting (program.py, interface.json)
  [x] stage 4 interpreter + trace validation + report.json (src/python_interpreter.cpp, src/trace_validation.cpp)
  [x] stage 5 end-to-end check (src/equation_check.cpp, `recover-program check`)
  [x] stage 6 folded RTL + Icarus validation (src/folded_rtl.cpp, `recover-program rtl`)
  [x] stage 7 runners: library_designs/e5_run_case.sh and kernel_foldings/e8_bambu_run.sh use this backend by
      default (PROGRAM_BACKEND=cpp, since 2026-11-08); PROGRAM_BACKEND=py runs the frozen Python scripts.
      Each result directory records the backend in program_backend.txt. Results produced before 2026-11-08
      under build/bench2_e5, build/bench2_v1 and build/e8 come from the Python backend.
      The review artifact (NetNotary/scripts/artifact) still calls the Python scripts.

Full comparison (2026-11-08): generate + check + rtl on every design with a Python program
(18 sweep in build/bench2_e5, 35 E8.3 in build/e8/bambu; the other 36 E8.3 configs never reached a program).
Outputs, per-design summary.txt line and the driver scripts: build/program_recovery_comparison_20261108/.
  - report.json: every field equal except flat/rolled/lifted line counts, which differ on 18 designs
    (hb_dec, sha256_lattice/oc/yosys_top, arf x8, dct8 x4, horner6_c20_m1, matvec4_c5_minf); mostly 1-21 lines,
    largest sha256_yosys_top lifted 7781 -> 7857.
  - check: every verdict and count equal to the Python checker's, except arf_c20_minf, open in both
    with solver timeouts (output_unknown), whose counts differ. horner6_c10_m1 rolled/lifted are open in
    both (state_interface_mismatch).
  - rtl (16 sweep designs with a Python RTL run, plus aes_sw_core): validation equal, 0 mismatches; hb_dec Verilog +3 lines
    (operand order). aes_sw_core simulation was stopped; its recovered.v, stimulus and testbench equal Python's.
  - Beyond Python: aes_sw128 and aes_sw_core generate in about 3 min and all three programs close
    (Python timed out at 3 h); sha256_lattice flat closes (Python MemoryError).

Build and use
  cmake -S plugins/algorithm_recovery/program_recovery -B build/program_recovery -DZ3_PACKAGE_ROOT=$(ls -d build/c3_venv/lib/python3*/site-packages/z3)
  cmake --build build/program_recovery -j 2
  build/program_recovery/recover-program generate GRAPH TRACE_DIR CONTRACT OBSERVE CASE OUT_DIR   # = program_recovery_python/decompile.py
  build/program_recovery/recover-program check GRAPH CONTRACT OBSERVE CASE OUT_JSON PROGRAMS...  # = program_recovery_python/close_chain_deep.py
  build/program_recovery/recover-program rtl GRAPH OUT_DIR TRACE_DIR [TRACE_DIR ...]           # = program_recovery_python/emit_rtl.py
  Requires libsodium, boost headers, Icarus (rtl). Scoring stays in Python.

Verified on icrc1 and qmults: report.json identical (icrc1 byte-identical), close_chain results
and verdicts identical for flat/rolled/lifted, recovered.v byte-identical and Icarus results
identical. Corpus-wide comparison: see the session's comparison runs (summarised in the
final report to the user).
  Results so far: icrc1, qmults, cordic, present_saiedhk lifted programs byte-identical, with
  identical lifting statistics, arithmetic candidates and word orders; 0.05-0.6 s per design.
  Python close_chain_deep.py closes the differing C++ flat/rolled programs of hb_dec and
  sha256_oc; sha256_lattice's flat program exceeds 6 GiB in the Python checker (as in the
  original E5 run, which checked only rolled and lifted).
  Python evaluation order: C++ does not sequence operands of '+'/function arguments; every
  z3 term or registry/local side effect must be created in Python's left-to-right order.

Interpreter plan (stage 4/5): restricted Python parser + evaluator for the emitted subset.
  Helper preamble (sgn, sra, route, pins, bits, pick, place, padword) is checked verbatim and
  implemented natively. Symbolic mode follows close_chain.py exactly: width W = 2*maxw+8,
  wrap() simplifies and turns constants into signed Python ints, z3py '<' and '>>' are signed
  (bvslt, bvashr), v[...] writes record the term and mutate the written Sym object into its
  cut symbol (shared reference semantics), pads/state .get ignore the default.
