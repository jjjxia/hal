# E1a result (2026-10-29): inverse folding splits a datapath whose state is shared
Design: tm_biquad.v — two direct-form-II biquads in cascade on ONE multiply-accumulate unit; the accumulator acc and
the temporary w are shared by both filters, state in st0..st3, 16 slots per sample. Equations: per-slot next-state
expressions from the yosys word-level netlist (proc/flatten/opt), slot concrete, rst 0, data symbolic (e1a.py) —
i.e. RTL-level, not yet HAL-recovered (E1b).
- Inverse folding: acc splits into 12 write events (partial sums), w into w@3 / w@11; state copies eliminated
  (st0, st1 -> w@3 with delays 1, 2). Lemma 1 (no negative delay): holds.
- Theorem 1 check: the recovered graph simulated sample by sample equals the RTL simulation (Icarus) on y1 and y2:
  63 samples, 0 mismatches.
- Identification (inline single zero-delay consumers except registers driving output ports; exact rational fit of
  floor(sum c_i s_i), verified on 300 random points): biquad 1 recurrence 1125/1024, -375/1024 (= 18000/16384,
  -6000/16384), output 1/8, 1/4, 1/8 -> low-pass biquad (double zero at -1); biquad 2 recurrence 3125/2048, -625/1024
  (= 25000/16384, -10000/16384), input y1, output 1/4, -1/2, 1/4 -> high-pass biquad (double zero at +1). All
  coefficients equal the RTL's.
Deviation found on the way: inlining y1 (single internal consumer) composed two roundings and broke the exact fit;
rule added: registers driving module outputs stay nodes (they are observable).
Not shown yet: period discovered from a trace (here the 4-bit slot counter gives P = 16 directly); equations from
HAL's gate-level pipeline (E1b); memories, multi-rate, origin-shift invariance (Lemma 2) as a separate check.

## Lemma 2 / Corollary 1 check (all 16 slot origins)
ORIGIN=0..15 (which hardware slot starts a sample): no negative delays; identical poles, numerators and labels in
all 16 runs; the only difference is a pure delay on one output stage (z^-1 on y1 for origins 4-7, on y2 for
12-15) — retiming, as Lemma 2 predicts; the canonical form (pure delay stripped) is identical in all 16.
Two implementation fixes this check forced (both are the OPEN points of THEORY.md):
- granularity: inlining only across zero-delay edges made the node set origin-dependent (partial sums such as
  acc@0 or acc@6 survived for some origins); inlining a single-consumer node across delayed edges (shifting its
  references) makes it origin-independent here;
- canonical form: output-stage numerators must be read over all delays with the leading pure delay stripped (the
  first version read delays 0-2 only and misreported z^-1(1/8,1/4,1/8) as a zero at -2).
