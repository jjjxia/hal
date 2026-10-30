# E3-Maggie: replace the manual whitebox_output.py automatically — findings and plan (2026-10-29)

## What the manual file actually is (measured)
Artifact: dynamicRE/reproduction/functional_dynamic_sota/dynamic/artifact/whitebox/.
- whitebox_output.py has 21,177 lines, of which 21,023 are a recorded trace (`real`: 20 signals x 1,049 cycles),
  used to initialize the first 30 cycles and to plot. The hand-written model is about 30 lines of register
  recurrences (lines 21104-21159), e.g. `r13[t] = r31[t-2] - r31[t-6]`, a direct transcription of the equations
  with r[t] = D(t).
- One line is not in the equations: `if r28[t-2] & inp[t] & 1: r28[t] += 1` (the 15-bit rounding adder).

## Probe (probe_transcribe.py, not preregistered)
Mechanical transcription of whitebox_output.txt (cycle 512 block, widths inferred from the expressions; data_i
width 14 assumed, to confirm) with the manual model's 30-cycle initialization:
- raw equations: out1 589 / out2 547 mismatching cycles of 1,018; the first divergence is r28 at cycle 32, and
  every other register diverges later (downstream).
- with the authors' one-line r28 correction: 0 / 0 mismatches.
So transcription is mechanical; the manual work was repairing one wrong equation against the trace.

## Why that equation is wrong (from earlier reproduction work, not re-verified here)
reproduction/functional_dynamic_sota/dynamic/README.md: the rounding adder's word-level function has two "control"
inputs that guided SSE fixes from the trace (all four values occur, 11 at cycles 514/515 = an ADD+1 mode), and the
artifact sampled internal controls 1 ps early; sampling at t gives 1,350/1,350 correct expressions on held-out
cycles. Likely root cause for us: those two "controls" are the operand LSBs, i.e. data, so the printed equation is
specific to the cycle's data (whitebox.py adds two hand-picked nets 1529 and 878 to the control set; that they are
the LSBs is NOT yet verified). The earlier eq2algo run, with a different control policy, recovered this word as
floor(Q/2 + x/2) and reached a bit-exact model (results/hilbert/ours, 0 mismatches on 2 x 848 cycles).

## Plan
Goal: input = guided-SSE equations of the artifact netlist + port metadata (widths, which word drives each output
port), no names; output = (1) an executable bit-exact model (what the 30 manual lines do), generated, and (2) the
algorithm description (sections, families, parameters, ambiguities), which the manual file does not contain.

C1 shipped equations (whitebox_output.txt only). Automatic transcription plus automatic divergence localization
   against the recorded trace. Expected: not bit-exact; the faulty equation is localized to one word. This tests
   error detection, not replacement; do not "repair" by guessing.
C2 regenerated equations. Rerun guided SSE on the artifact netlist (reuse the HAL 4.4 snapshot loaded by
   replay_control_sampling.py) with the E1 known-value policy: a net is control only if it is outside the fan-out of
   the data inputs, so data LSBs stay symbolic and the adder's equation is data-independent. Window of >= 20 cycles.
   Then invfold (period 1 expected; delay lines collapse) -> node fit -> code generation -> identification.
Criteria (fix in a PREREG before running C2):
- out1/out2 bit-exact on all 1,019 cycles after the same 30-cycle initialization; also from reset if possible.
- generated model needs no hand edits; size reported.
- algorithm level vs frozen GT (eq2algo/gt/gt_hilbert.json): 10 sections, families and parameters, with the
  ambiguities listed (boundary pipeline register, I/Q orientation if the port map is not given).
Caveats to state up front: the Hilbert design is the dev set of the grouping rules (algorithm-level results are not
held-out); it has no time multiplexing (P = 1), so it does not test inverse folding; the control-set change is a
frontend change relative to the artifact.

## Update 2026-10-29: use NetNotary as the gate (user's suggestion)
NetNotary (dynamicRE research_loop/runs/2026-10-23-open-gap/claude/eq2algo/thm2, algo/) proves per-word update
obligations Q_w' = F_w(sources) on the original netlist and returns counterexamples for wrong ones (negative controls
3/3 COUNTEREXAMPLE). On Hilbert it already proved 61/61 obligations from reset (thm2/hil_reset.json), giving a
75-line closed model equal to the netlist for every data sequence under the protocol. Consequences for this plan:
- C1 becomes: submit the 17 shipped equations as obligations. Expected: r28 (rounding adder) refuted with a
  counterexample (both LSBs 1), the other 16 proved. This replaces trace-based localization, which only shows the
  first divergence and only if the workload triggers it.
- The pipeline becomes SSE -> NetNotary (accept / refute) -> inverse folding -> identification. Only certified
  equations enter the recognizer, so the claim moves from "matches the equations" to "matches the netlist under the
  protocol" wherever the obligations are proved.
- The executable-model part of "replace whitebox_output.py" is essentially already done by thm2 for Hilbert; what
  is new here is the algorithm-level description and the refutation of the shipped equation.
- For folded designs (E1) the obligations are per phase with period P; closed_generic.py already handles per-phase
  forms. Certified per-phase forms + soundness of inverse folding (Theorem 1 of the paper) would give an end-to-end
  statement: the recovered algorithm graph equals the netlist from reset for every input sequence.
Open: map artifact word numbering (whitebox_output.txt) to the NetNotary netlist model (thm2 uses a rerun's numbering).
