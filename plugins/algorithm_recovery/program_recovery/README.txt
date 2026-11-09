Program recovery: certified event graph -> untimed programs and folded RTL
==========================================================================
recover-program is built with the algorithm_recovery plugin (bin/hal_plugins/recover-program) but does not
link HAL. Its input is the event graph written by `hal-recover graph`.

  recover-program generate GRAPH_JSON CONTRACT_JSON OBSERVE_JSON CASE OUT_DIR
      OUT_DIR/program_flat.py     one transaction as straight-line code over the certified equations
      OUT_DIR/program_rolled.py   the same with repeated phases rolled into proven loops
      OUT_DIR/program.py          lifted: proven word arithmetic, routes, XOR layers and tables
      OUT_DIR/interface.json      pad reads, loop-carried state values and observations of transaction()
      OUT_DIR/report.json         structure: loops, lifting statistics, tables, arithmetic candidates,
                                  word orders, sizes; OUT_DIR/proofs/ holds the lifting proof queries
  recover-program rtl GRAPH_JSON OUT_DIR TRACE_DIR [TRACE_DIR ...]
      OUT_DIR/recovered.v         folded Verilog with the recovered word registers and a cycle counter
      OUT_DIR/report.json         Icarus simulation against each original-gate trace, word by word

The programs are Python, so they are run and checked by CPython, not by this tool
(algorithm_recovery/evaluation/program_checks):
  validate_programs.py GRAPH TRACE_DIR CONTRACT OUT_DIR   runs the three programs on the held-out transactions
                                                          of the trace -> OUT_DIR/validation.json
  close_chain.py GRAPH CONTRACT OBSERVE CASE OUT_JSON PROGRAMS...
                                                          proves each program equal to the certified
                                                          equations over one transaction, for all inputs

Sources
  src/event_graph.*          graph loading, certified specialisation, retiming to transaction coordinates,
                             observations and liveness
  src/transaction_program.*  flat program (build_flat) and roll-up (build_rollup: runs of compatible phases,
                             iteration constants and counters, z3 proof of every iteration)
  src/lift_tables.*          routing and table lifting of bit-level node functions
  src/lift_arithmetic.*      word-level arithmetic with inferred bit orders
  src/lifted_program.*       the lifted program and interface.json
  src/expr_eval.*            concrete evaluation of z3 terms (lifting tests)
  src/python_printer.*, python_format.h   Python source for z3 terms; integer and repr formatting
  src/python_json.h          JSON with arbitrary-precision integers (report.json)
  src/python_random.h        CPython's MT19937 getrandbits stream, so the lifting test vectors stay those
                             of the reported runs
  src/program_report.*       report.json
  src/folded_rtl.cpp         folded RTL and its Icarus validation
  src/main.cpp               CLI

z3 version
  recover-program links the libz3 of the pip package z3-solver 5.3.3, which close_chain.py also uses.
  HAL's system z3 4.8.12 gives the same loops, lifting results and validation, but simplify() returns much
  larger terms (dct8_c10_m1: 20,901 instead of 9,076 program lines; lattice4_c5_minf +1,450). HAL itself
  stays on 4.8.12; recover-program runs in its own process. Configure HAL with
    -DPROGRAM_RECOVERY_Z3_ROOT=<venv>/lib/python3.X/site-packages/z3
  (build/python_venv in this tree; venv/ in the review artifact).

History
  Until 2026-11-08 this stage was a set of Python scripts (decompile.py, lift.py, lift_arith.py, emit_rtl.py);
  all results produced before that date come from them (git history before the deletion). The C++ version
  was checked against them on every design that has a program (18 in build/bench2_e5, 34 in build/e8/bambu;
  outputs and driver scripts in build/program_recovery_comparison_20261108/):
  - report.json: every field equal except program line counts, which differ on 18 designs because z3
    shares and orders subterms differently (mostly 1-21 lines; largest sha256_yosys_top lifted 7,781 ->
    7,857). Byte identity is not attainable: simplify orders commutative arguments by AST id.
  - trace validation and the end-to-end check: same verdicts and counts as the Python results
    (arf_c20_minf is open in both, by solver timeouts; horner6_c10_m1 rolled/lifted are open in both).
  - rtl: same validation on the 16 sweep designs with an RTL run, 0 mismatches.
  - aes_sw128 and aes_sw_core, which timed out after 3 h in Python, generate in about 3 min.
