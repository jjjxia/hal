# Algorithm recovery from trace-guided word equations — proposal (2026-10-29)

Audience: agents continuing this work. Read all of it before writing code. Status: proposal plus one dev-set probe;
nothing here is an established result unless marked as measured, with its evidence path.

## 1. Problem
Trace-guided netlist reverse engineering (Klix et al., "Stealing Maggie's Secrets", ACM CCS 2024; HAL plugins
`sequential_symbolic_execution`, `module_identification`, `dataflow_analysis`, `bitorder_propagation` in this repo)
ends with anonymous, time-indexed word equations: for cycle T and word w, `w' = E(words of earlier cycles, inputs)`.
Turning these into an algorithm (what is computed, by which sections, with which parameters) is done by hand in the
published work: the artifact ships `whitebox_output.txt` (5 cycles of automatic equations) and `whitebox_output.py`
(about 21k lines written by the authors from them). The TCHES 2026 SoK also lists this step as manual.

Goal: automate it.
- Input: the per-cycle equations (plus word widths and the top-level ports). No instance or module names.
- Output: (a) the algorithm-level single-rate data-flow graph; (b) a partition into sections; (c) per section, a
  library family, its parameters and the input/output mapping; (d) the explicit set of ambiguities that cannot be
  resolved from the equations. Deterministic: no learned model, no probabilities, unless a later experiment shows
  that a canonical form does not exist for some class.

Out of scope for now: certifying the equations against the netlist (a separate project; equations are taken as
given), LLM baselines (the user decided not to use them as the yardstick).

## 2. Core idea: inverse folding + canonical form modulo unobservable symmetries
Real devices time-multiplex: one MAC and its registers serve several filters in turn (the Maggie device does). A
physical register is then several algorithm variables. DSP architecture synthesis calls the forward map folding
(Parhi, Wang, Brown, IEEE JSSC 27(1), 1992; Parhi, VLSI Digital Signal Processing Systems, 1999):
`D_F(U->V) = N*w(e) - P_U + v - u` (hardware delays from algorithm delays w(e), folding factor N, time slots u, v,
pipeline depth P_U). We invert it, at the level of equations:

1. Period. Rewrite every equation to relative time and canonicalize it (below) to a signature. Find the smallest P
   such that signatures repeat with period P for all words (string periodicity, linear time). No period ->
   data-dependent control: split by mode first (open item).
2. Logical signals = write events. A logical signal is (register r, slot phi): the value r receives at hardware
   cycles t = P*n + phi. Holding (no write) is storage, not an algorithm delay. A register written in two slots is
   two signals; identical equations in several slots indicate a multi-rate signal.
3. Resolve every read. A read of r' at cycle t refers to the LAST write of r' before t, at t_w = P*n' + phi'. It is
   the logical signal (r', phi') at sample n', with algorithm delay k = n - n' (edge weight z^-k). This is
   reaching-definitions analysis over unrolled time; it is the folding equation solved for w(e). Consistency check:
   every k must be a non-negative integer, otherwise P or the slot assignment is wrong -> report, do not guess.
4. Result: single-rate algorithm graph G. Pure copies become z^-k edges, not nodes.
5. Uniqueness: the only free choice is which slot starts a sample; changing it is a retiming of G (Leiserson & Saxe,
   Algorithmica 6, 1991): loop delays (hence poles) are invariant; input-output paths change only by pure delays.
   Claim to prove: G is unique up to retiming (plus slot permutations that the hardware cannot distinguish).

Worked example (two first-order IIR filters on one accumulator ACC; P = 2):
```
T[2n]   : ACC <- x + a*S1        S2 <- ACC
T[2n+1] : ACC <- ACC + b*S2      S1 <- ACC
```
Signals A0 = (ACC, 0), A1 = (ACC, 1). Resolving reads: S1 at 2n = A0[n-1], S2 at 2n+1 = A1[n-1], ACC at 2n+1 = A0[n].
So A0[n] = x[n] + a*A0[n-1] and A1[n] = A0[n] + b*A1[n-1]: the shared register splits into two sections.

## 3. Canonical form, grouping, identification
- Node canonicalization: fit every update to `floor(sum c_i * s_i) mod 2^w` with dyadic c_i and signed/unsigned
  sources, verified on full-range random inputs (already implemented: dynamicRE eq2algo/lib/pipeline.py). Updates
  that do not fit (compare, select, saturate, table) stay as labelled nonlinear nodes; they split G into linear
  islands.
- Grouping (deterministic rules, written on the Hilbert dev design; must be frozen before held-out runs):
  R1 each SCC (or self-loop) is a recursive section; R2 delay chains join their consumers; R3 adders reading the same
  delay chain form one section; R4 a feed-forward value read by exactly one feed-forward node merges into it;
  R5 a node reading only one recursive section is that section's output stage. Principled target to replace the
  rules: maximal decomposition of G into library-irreducible units.
- Canonical form of a section: exact rational transfer matrix (numerator/denominator in z^-1) from its external
  inputs to the values read outside it, with rounding sites listed separately; normalized modulo the symmetry group
  G_obs of transformations invisible in anonymous equations: (i) pure delays at section boundaries (retiming),
  (ii) I/Q swap of a complex pair (conjugates a complex filter), (iii) global fixed-point scale. Canonical form =
  orbit representative (strip boundary delays, lexicographically smallest I/Q orientation, leading coefficient 1).
- Library = parameterized families defined by invariants, parameters read in closed form, no search for linear
  islands: comb(N): 1 - z^-N; real pole/integrator/resonator(theta, rho): poles rho*e^{+-j theta}; biquad(b, a);
  FIR(h) with subclasses (linear phase, antisymmetric Hilbert FIR); complex FIR via the 2x2 pattern [[A, -B], [B, A]]
  = A + jB; CIC; DFT_N / butterfly (matrix). Several valid labels -> most specific in a fixed hierarchy, others
  kept as aliases (naming convention, not probability).
- Composition: collapse identified sections to nodes and parse the section-level graph (e.g. comb + resonator bank =
  frequency-sampling filter; overall response analytic -> Hilbert/analytic-signal filter).

## 4. Evidence so far (measured)
Dev design: Xilinx Hilbert transformer from the CCS'24 artifact (fully parallel: no time multiplexing, so it does not
test inverse folding). Code and log: /home/jiahao/HardwareRE/dynamicRE/research_loop/runs/2026-10-23-open-gap/claude/
eq2algo/grouping/ (group_identify.py, RESULT.md). Input words.json from eq2algo's canonicalizer; white-box GT
(gt/words.json) used only for scoring.
- Grouping: 8/10 GT sections exact; 10/10 up to one boundary pipeline register (DANA_module_32 between
  real_pole_filter_2 and comb_stage1) — symmetry (i).
- Identification: 2 combs N=4, 2 real-pole sections z^2 = 1/2 (rounding sites listed), 2 resonators 0/180 deg,
  2 resonators +-90 deg, 2 complex FIRs with zeros at 30/150 deg (GT names -30/-150: symmetry (ii)).
- Earlier eq2algo kill test on the same design (results/hilbert/*/score.json): bit-exact executable model, poles,
  response and roundings correct; its comb score failed because the library had no complex-FIR entry (the four
  delay-2 differences are parts of the complex FIRs), now fixed by the 2x2 rule.

## 5. Prior work (check before any novelty claim)
Forward theory: folding (Parhi 1992/1999), unfolding, retiming (Leiserson & Saxe 1983/1991), synchronous data flow
(Lee & Messerschmitt 1987, unverified from memory). Neighbours: Template-Based Circuit Understanding (FMCAD 2014;
templates + SMT on combinational circuits), "There and Back Again: A Netlist's Tale with Much Egraphin'" (LATTE 2024;
e-graph component identification), Linear Analysis and Optimization of Stream Programs (PLDI 2003; linear filter
extraction from StreamIt programs), SPIRAL/SPL (forward: transform -> implementation), HLS verification by FSMD
equivalence (behaviour given, only checked), attacks on locked RTL that abstract to FSMD. Two web searches found no
work recovering an algorithm data-flow graph from a folded schedule; that is NOT a literature review. Required
search terms: RTL-to-C decompilation, FSMD inference, schedule/binding recovery, HLS reverse engineering, loop
recovery from hardware traces; also check Parhi's unfolding chapter for an inverse.
Expected contribution if it survives: (1) inverse folding with a uniqueness-up-to-retiming theorem; (2) canonical
identification modulo G_obs (ambiguity characterized exactly instead of ranked). Grouping rules and transfer-function
matching are engineering, not claims.

## 6. Experiments, in order (preregister each before running; report failures)
E1 (decisive, first): a time-multiplexed design in which STATE is shared too — one multiply-accumulate unit and a
shared register/RAM computing two biquads in turn (mimicking the Maggie device). Write the RTL, synthesize (yosys,
Xilinx 7-series), run HAL module identification + guided symbolic execution over one or more periods with a FIXED
cutpoint policy, then check: P found automatically; inverse folding yields two separate biquads with all k
non-negative integers; coefficients equal the RTL's; differences only within G_obs. Note: two filters on one MAC
with SEPARATE state registers is not a test (register grouping already separates them).
E2: constructed benchmarks with ground truth by construction: random DSP algorithm graphs (FIR/IIR cascades, filter
banks, CIC, complex filters) folded with the standard folding transformation at several folding factors and
pipelinings, synthesized, recovered. Freeze rules R1-R5 and the library before running.
E3: open-source DSP cores (FIR, CIC, CORDIC, FFT) and the artifact Hilbert (as an RQ4-style "replace the manual
whitebox_output.py" test: only whitebox_output.txt and port metadata as input).
Negative / out-of-library cases: wrong coefficients, wrong widths, algorithms not in the library -> must come out
as "no match", never as a confident label.
Metrics: algorithm graph exact up to G_obs; section partition (score endpoints and section function, not internal
node membership, since boundary delays are ambiguous); family and parameter accuracy; false-match rate on negatives;
runtime vs folding factor, period and design size. Resources: one heavy job at a time (7.8 GB RAM + swap).

Decision rules (fixed now): if E1 fails for the same reason twice, stop and report what breaks inverse folding.
If rules R1-R5 need changes on held-out designs, report every change as a deviation.

## 7. Known hard cases (open)
Non-periodic schedules (data-dependent control) -> per-mode graphs; state in BRAM -> logical signal
(memory, address(phi), phi), addresses from counters; multi-rate -> P = lcm of rates; fixed-point nonlinearity
(rounding, saturation) -> linear part identified exactly, quantizer sites listed (candidate canonical class:
"linear + quantizers"); the folding inverse needs the equations of every written register over a full period, which
depends on the cutpoint policy of guided symbolic execution.

## 8. Rules for agents
- Never use instance/module names or RTL in the recognizer; only for scoring, and say so.
- Fix the cutpoint/target policy of guided symbolic execution before generating test equations.
- Distinguish "matches the recovered equations" from "matches the hardware"; this project claims only the former.
- Do not push this branch; origin is the upstream emsec/hal repository.
