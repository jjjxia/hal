Evaluation code
===============
The implementation is C++ (plugins/algorithm_recovery): `hal-recover` turns a netlist into a certified
event graph, `recover-program` turns the event graph into untimed Python programs and folded RTL.
This directory holds what runs around it: input preparation, runners, the CPython checks of the
generated programs, scoring and summaries.

Layout (renamed on 2026-11-08; the old names were plan identifiers):
  library_designs/      library-sweep and initial benchmarks: preparation (prepare_bench2.py), the C1 pipeline
                        (run_bench2.sh), the E5 runner, RTL oracle, RQ3 baselines (t12_*), summaries
                                                                       (was bench2/; experiments E5, E2.x)
  program_checks/       CPython checks of the generated programs: validate_programs.py (held-out
                        transactions), close_chain.py (end-to-end proof), check_reference.py and
                        check_stream.py with the reference algorithms (e5_refs/, *_truth.py)   (was c3/)
  synthetic_foldings/   random dataflow graphs folded with known bindings          (was e5c/; E5.C)
  kernel_foldings/      standard kernels folded by our generator and by Bambu      (was e8/; E8)
  scale/                scale and ablation experiments                             (was e6/; E6)
  vendor_synthesis/     vendor-tool netlists                                        (was e7/; E7)
  hace_comparison/      HACE on gate-level netlists                                 (was hace_gate_level/)
  protocols/            frozen protocols
  check_taint_crossrun.py  cross-run taint check of the C1 pipeline; storage_reuse.py  ablation helper

Per design the chain is
  run_bench2.sh: hal-recover import, groups, trace, graph (certificate and event graph in graph/), variant
                 trace, cross-run taint check
  recover-program generate -> program/; validate_programs.py -> program/validation.json;
  check_reference.py or check_stream.py; close_chain.py -> close_chain.json; recover-program rtl -> rtl/
Until 2026-11-08 the certificate came from a separate `hal-recover analyze` stage (analysis/certificate/,
identical up to output paths) and the programs from Python scripts (program_recovery_python/decompile.py,
emit_rtl.py). Result directories produced before that date are laid out that way.

Frozen protocols, result records and stage receipts keep the paths that were valid when they
were written: replace algorithm_recovery/generalization by algorithm_recovery/evaluation and
the old subdirectory names by the new ones above. Result directories under build/ keep their
names (bench2_e5, bench2_v1, e8/, e5c/, ...), because receipts record their absolute paths.

Early experiments
  The code of experiments the paper no longer reports was deleted on 2026-11-08; their result records
  remain: PLAN.txt, RESULTS.txt (independent-source trials), C1_NATIVE_RESULTS.txt, C2_CONTRACT.txt,
  C2_RESULTS.txt, C2_*_PROTOCOL.txt (supplied-equation structural evaluation), INDEX_*.txt (firmware),
  and ../E1, ../e1, ../E3_maggie, ../E4_certified, ../attack_regress, ../attack (records only),
  ../ATTACK_LOG.md. The code is in the git history before that date.
