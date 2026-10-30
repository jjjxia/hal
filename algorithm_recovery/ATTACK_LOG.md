# Adversarial review log (2026-10-29 / 30)

An adversarial subagent attacked the theory (full.tex, ext.tex in /home/jiahao/algorithm-recovery-paper) and the code
(invfold/, E1/, E4_certified/) in five rounds, each against a fresh snapshot; every finding has a reproducer under
attack/cases*/ (docstring = the counterexample, .out = the output on that snapshot). attack_regress/run_cases.py runs
any round's cases against the LIVE code (env CASES=cases, cases2, cases3, cases4). Classes: (a) theorem false as stated,
(b) assumption unstated/too weak, (c) silent wrong result, (d) crash/hang/error, (e) survived.

## Survived every round (e)
Lemma 1 (periods/gcd), Lemma 2 (representative independence), Prop 3 (well-formedness), Thm 4 (soundness), Thm 5
(origin = retiming), Thm 6 (period multiple = unfolding), Prop 9 (partition and kappa invariant under origin), the
Blocking theorem (a)-(c) including "alignment search unnecessary", the block-transform error lemma (for q = twiddle
quantum), class_by_bisection convexity, DSP48E1 timing, trace_bit's rule, the SRL model, lazy UNKNOWN evaluation (no
leaks). Randomized differential tests of the core inverse folding: 637 (round 1), 395 (round 2), 178 (round 3)
design/origin runs, 0 silent mismatches.

## Findings and fixes (most severe first per round)
Round 1 (theory + code as of E1/E4):
- (c) node fit truncated coefficients to 8 fractional bits -> exact coefficient extraction (later: exact classes).
- (b)+(c) FDRE sync reset overrides CE: E1 frontend read CE only (FIR recovered as integrator) -> effective write = CE or R,
  R writes the reset value; model definition amended.
- (c) R4 implemented differently from the paper; linear sections read only by nonlinear nodes had no outputs -> rules
  reimplemented (nonlinear nodes opaque, R4 only among linear feed-forward nodes).
- (c) execute() ignored round mode; (c) compile cache keyed by id(text); (d) copy cycles hang -> fixed.
- (c) certified one-period windows skipped cross-period checks -> pre-window write pattern checked.
- (a) Cor 8 "most folded algorithm" false when binding varies (ping-pong, circular buffers) -> restricted to fixed
  binding; Blocking theorem recovers the stream-rate algorithm in the linear case.
- (b) idle-slot writes, two roundings per cycle, input-capture copies, kappa labels, SMT ops -> dead-node elimination,
  'quant' nodes, input copies collapse, rounding signature, ops added.
- X1/X2/X3 planned extensions: block transforms need per-row/column delays and input permutations; I/Q orientations are
  independent; interleaved channels and held inputs are unobservable (now reported as ambiguities); memory caveats
  (circular buffers, read-during-write modes, latency); support signatures do not determine DES key permutations
  (we use syntactic templates), XTEA rounds differ by constants (confirmed: false negative), XOR Feistel test misses
  modular-add Feistel and accepts non-invertible maps -> partner check, additive variant.
Round 2 (after fixes):
- (c) 'quant' accepted coefficients outside their class (FFT16 Q15 twiddles as 181/256) -> exact coefficient classes.
- (c) DFT labels changed with the origin -> canonical labeling, rotations named.
- (c) multi-port LTI called time-varying -> MIMO pseudo-circulant test.
- (c) DFT bound used entry denominators (q^L) and did not scale with |s| -> node quantum, bound proportional to |s|.
- (c) 3-multiplier complex products failed complex linearity -> tolerance tied to the rounding bound.
- (c)/(a) reroll template dropped input phases (substitution argument false) -> phases as parameters; Feistel
  bijectivity; (a) coordinate-free schedule proposition vacuous with partial maps -> restated (total extension).
- (d)/(c) dead nodes in frames, greedy row/column delays, cat sources in fits, memory theorem statement -> fixed.
Round 3:
- (c) periodic extension silently wrong when control is not P-periodic (decimation, circular buffer) -> checked on the
  trace after the window.
- (c) exact-class fit missed nodes with sources > 8 bits -> exhaustive classes by bisection for 9..12 bits, refinement.
- (c) quant accepted hidden saturation; > 3 narrow sources; unsigned flag lost after collapse; reachable UNKNOWN missed by
  sampling; MIMO 'complex_filter' unchecked; unresolved memory read crashed -> fixed; (b) DSP/RAM assumptions now asserted.
Round 4:
- (c) wrong floor fits for 13..16-bit sources (11/120) -> exhaustive CEGIS verification; now 0/120 wrong.
- (c) periodic check ignored RAM write-data mapping -> compared; trace-length check added.
- (c) dead zone (coring) accepted as quant -> mid-band criterion.
- (c) joint and high-slice UNKNOWN guards missed -> constructive witnesses for equality-conjunction guards.
Round 5 (time-boxed): round-4 fixes verified (d1-d4; wide sources 30/30 and Q23 40/40 exact). fft_exec's calibration
and comparison judged sound (data-dependent outputs, shift chosen on sim1 among 12, held out on sim2/sim3, frames >= 1
independent of the zero start). The FFT64 frame matrix is a DFT only up to per-input/per-output phases, with reversed
time order; the review asked for H itself to be validated: fft_Hcheck.py shows H agrees with the simulation for only
~half of the output components, so the frame-level FFT64 reading was withdrawn (E4_certified/RESULT.md). The newest
code (13-16-bit CEGIS, mid-band quant rule, unknown_witnesses, trace-length check) was not attacked yet.

## Still open (stated in the paper)
Reachability of UNKNOWN for guards that are not equality conjunctions (sampling only); coefficients of sources wider
than 16 bits (sampled refinement, not exhaustive); rerolling incompleteness (XTEA needs parametric symbolic
execution); existence of a total extension in the DES schedule test; per-output scaling (scaled DCTs) not in the DFT
group; the FFT64 block-boundary behaviour (below) needs an RTL check.
