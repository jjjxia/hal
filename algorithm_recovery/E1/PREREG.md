# E1 preregistration — inverse folding on a design with shared MAC, accumulator and state (2026-10-29)

Written before the design, the frontend run and the recognizer exist. Changes after this point go to "Deviations"
at the end, with date and reason.

## Design under test (fixed now)
Algorithm (ground truth, used only for scoring): two cascaded direct-form-II biquads,
  v[n] = x[n] + a11 v[n-1] + a12 v[n-2],   u[n] = b10 v[n] + b11 v[n-1] + b12 v[n-2]
  s[n] = u[n] + a21 s[n-1] + a22 s[n-2],   y[n] = b20 s[n] + b21 s[n-1] + b22 s[n-2]
Coefficients Q1.10 (12-bit signed, integer/1024): a11 = 1280, a12 = -576, b10 = 320, b11 = -704, b12 = 448,
a21 = -384, a22 = -256, b20 = 896, b21 = 192, b22 = -512. Every product is (c * operand) >>> 10 (floor).
Hardware: ONE multiplier and ONE adder, one 24-bit accumulator ACC, a slot counter phi mod 10, a coefficient table
indexed by phi, and ONE shared 5-register state chain S1..S5 (24 bit) that shifts (S1 <= ACC, Si <= S(i-1)) only in
the two slots that complete v and s. Every chain register therefore holds v-values in some slots and s-values in
others: register grouping cannot separate the two filters. The output register Y <= ACC[19:4] once per sample.
Input x (16 bit) is read in slot 0 only.

## Frontend (fixed now)
- yosys 0.33 `synth_xilinx -flatten -nodsp -nosrl`; HAL (this repo's build) with XILINX_UNISIM_hal.hgl; the
  preprocessing loop of the CCS'24 artifact example.py.
- Words. Variant A (primary): register grouping and bit order from the RTL register names, as the authors' white-box
  run did (`use_register_ground_truth = True`); the recognizer sees only anonymized ids `w<k>` with k shuffled by a
  fixed seed. Variant B: DANA grouping; reported, not required for the decision.
- Known (control) values: from netlist simulation of the synthesized netlist, the values of every net that is not in
  the transitive fan-out (through gates and flip-flops) of the data input port x, plus all flip-flop control pins.
  Computed structurally; no names. Reset and clock are the only non-data ports, declared as port metadata.
- Cutpoints: data-dependent words whose D bits are driven by combinational gates (the authors' rule). Pure
  flip-flop copies are traversed by symbolic execution. Targets: every cutpoint word's D input and every output port,
  at every cycle of a 60-cycle window starting after a 40-cycle warm-up.
- Write events: a cutpoint word is written in a cycle iff its clock-enable value (known, from simulation) is 1, or
  it has no enable.

## Recognizer (fixed before running on the netlist)
Inverse folding exactly as PROPOSAL.md §2 (signatures on relative time, smallest period over the whole window,
logical signals = write events, reaching-definition resolution), then node canonicalization (dyadic floor-linear fit,
verified on full-range random inputs) and identification (§3: SCCs as recursive sections, rules R1–R5, exact rational
transfer functions). The recognizer is first run on a hand-written equation-level example (PROPOSAL §2 worked example)
and on equations obtained by symbolically executing the RTL schedule in Python; those runs are sanity checks only.

## Success criteria (all required)
S1 the period found automatically is 10.
S2 every read resolves to a write (no unresolved or never-written register) and every input read resolves to one
   input phase; the resolved graph has no zero-delay cycle.
S3 exactly two recursive sections, each with a denominator of degree 2, in cascade, each followed by an output stage.
S4 per section the recovered transfer function equals the RTL's b(z)/a(z) exactly as rationals, up to G_obs
   (boundary pure delays, global scale); the rounding sites are the 8 product truncations and the output slice.
S5 executing the recovered single-rate graph on the input samples reproduces the netlist-simulation output y on all
   samples of a held-out 2000-sample run (this is checked against simulation of the synthesized netlist, i.e. at the
   hardware level for that input; the claim is still only "matches these equations / this simulation").

## Decision
PASS iff S1–S5 hold for variant A. If E1 fails twice for the same root cause, stop and report what breaks inverse
folding (PROPOSAL §6). A frontend failure (HAL cannot produce the equations) is reported as a frontend failure, not as
a failure of inverse folding; the equation-level run then decides whether inverse folding itself works.

## Deviations
All logged 2026-10-29, in the order they happened.
- D1 (criterion text). S4 said "the 8 product truncations"; the design has 10 coefficients and so 10 truncated products
  (2 feedback + 3 feed-forward per biquad). Arithmetic error in this file; the scorer checks 10. Found when writing
  the scorer, before the score was computed.
- D2 (frontend, simulation). HAL's verilator simulation needs Xilinx models that the artifact does not ship (INV, IBUF,
  OBUF). The known values come instead from a cycle simulation of the HAL netlist with HAL's own gate functions
  (run_hal.py `Sim`, FDRE semantics from the .hgl next_state). Checked against the RTL reference model before use (10/10
  samples on the dev stimulus); the held-out check (S5) uses an independent simulator (iverilog with yosys cells_sim.v).
- D3 (frontend, words). HAL merges aliased nets and renames them during preprocessing, so register ground truth is
  read from the Q connection of each flip-flop instance in the synthesized Verilog instead of HAL net names.
- D4 (frontend, output format). The SSE Python binding prints expressions as trees; with a LUT-mapped multiplier one
  slot-0 equation printed to more than 60 MB. Added `get_pg_word_values_at_z3_smt2` to
  plugins/sequential_symbolic_execution/python/python_bindings.cpp: same computation, returns SMT-LIB2 text (z3 prints
  shared subterms once). Output format only. The top-level port pin groups are recreated after buffer removal.
- D5 (recognizer, changed after seeing E1 equations). (a) Input-port symbols are printed as `<port>_<t>`, not
  `top_module_<port>_<t>`; the symbol pattern now accepts both. (b) The least-squares probe of the node fit used
  magnitudes from the source width only, so the 16-bit output slice of a 24-bit word wrapped and did not fit; the probe
  magnitude now shrinks until the residual is below the rounding level. Neither change touches period finding, write
  events or read resolution. They are still post-hoc changes to the recognizer, contrary to "fixed before running".
- D6 (scorer bugs, fixed before reading the fixed result). The first score reported S5 = false (executor got x as
  unsigned) and retiming invariance = false (comparison depended on section order, which differs across origins).
- D7 (variant B). DANA (`dataflow.analyze`) segfaults on this netlist in this HAL build (with and without expected
  sizes; dana_check.py). Variant B is not run; this is a frontend failure.
