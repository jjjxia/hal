# E1 result — inverse folding on a shared-MAC / shared-accumulator / shared-state design (2026-10-29)

Verdict: **PASS** (variant A, all of S1–S5, PREREG.md). Variant B not run (DANA segfault, deviation D7).
Read PREREG.md (criteria and deviations D1–D7) first; several frontend and two recognizer changes were made during this
run, all listed there.

## What was run
Design `biquad2_folded.v`: two cascaded DF-II biquads on one multiplier, one adder, one 24-bit accumulator, a shared
5-register state chain shifted in two of the 10 slots. RTL = algorithm model (`ref_model.py`, 38/38 samples) and the
yosys netlist = RTL (gate-level iverilog, 38/38). Netlist: 946 cells, 164 FFs, multiplier in LUTs (`-nodsp`).
Frontend (`run_hal.py`): HAL preprocessing, words from register names (variant A), known values = nets outside the
fan-out of x (29 nets: slot counter, clock tree, constants), guided SSE over cycles 40–99.
Recognizer (`../invfold/`): `invfold.py` (period, write events, read resolution, node fit, copy collapse),
`identify.py` (R1/R4/R5 sections, exact rational transfer functions, library classes, executor).

## Results (out_dev = preregistered cutpoint rule; out_cutall = every data word a cutpoint, post-hoc robustness run)
| | preregistered rule | all data words cut |
|---|---|---|
| cutpoints | accumulator only (chain and output register are FF copies, traversed by SSE) | all 7 data words |
| S1 period | 10 | 10 |
| S2 errors / input phases / zero-delay cycle | 0 / x in one phase (slot 4) / none | same |
| logical signals | 10 accumulator signals | 10 accumulator + 10 chain + 1 output; the 10 chain signals collapse to delays |
| S3 sections | 2 recursive, cascaded, each 2 SCC nodes + 3 output-stage nodes | same; output register joins section 2 |
| S4 section 1 | (5/16, −11/16, 7/16)/(1, −5/4, 9/16) = RTL b1/a1 exactly | same |
| S4 section 2 | z^-1 (7/8, 3/16, −1/2)/(1, 3/8, 1/4) = RTL b2/a2 exactly | z^-1 (7/128, 3/256, −1/32)/(…) = RTL × 1/16 (output slice; global scale) |
| rounding sites | 10 floor products; output = floor(ACC/16) | 10 products + output slice |
| S5 held-out | 20,025 cycles (2,002 samples) bit-exact vs gate-level simulation | same |
| all 10 slot origins | identical section forms; boundary delays move between sections (0/1) | same |

The strict scorer flags S4 false for the robustness run only because it also compares gain and counts exactly 10
rounding sites; both differences are the output slice (symmetry (iii) and the extra site that PREREG S4 names).

In the robustness run each chain register splits into two logical signals that belong to different filters, e.g.
the first chain register is a copy of the s-signal in one slot and of the v-signal in the other. Under the
preregistered rule this disambiguation is done jointly by SSE (which follows the copies through the known clock
enables back to the accumulator write) and by inverse folding (which maps that write time to slot and sample); in the
robustness run inverse folding does all of it.

## What this does and does not show
- Shows: on one constructed folded design, the period, slot splitting, read resolution and canonical identification
  recover both filters exactly, and the free choice of slot origin acts as a retiming (section forms invariant,
  only boundary delays change), as PROPOSAL §2 predicts. This is evidence for the uniqueness claim, not a proof.
- Does not show: anything about schedules that are not strictly periodic, state in BRAM/LUTRAM, multi-rate designs,
  saturation, word recovery without register names (variant B blocked), designs not written by us, or novelty.
  The design was written by the same agent that wrote the recognizer; E2 (folding by construction) and E3 are needed.
- Novelty: an earlier internal probe (dynamicRE research_loop runs/2026-10-23-open-gap/claude/c5b_multiplexed/
  RESULT.md) names VeriIntel2C/MIRROR (folded-loop and array recovery at RTL level) and HACE (resource-sharing
  reversal) as direct predecessors of folded-datapath recovery. PROPOSAL §5 does not list them yet; check them
  before any novelty claim.

## Cost
SSE 170 s (preregistered rule, 120 targets) / 136 s (all cut, 480 targets); 33 MB of SMT-LIB text per run;
recognizer about 25 s per origin; peak RAM below 1 GB.

## Reproduce (from algorithm_recovery/E1, outputs in build/, ignored by git)
```
yosys -q -l build/synth.log synth.ys && cd build && sed "s/1'hx/1'h0/g" biquad2_folded_xilinx.v > net.v
python3 -c "import random; r=random.Random(7); open('stim_dev.txt','w').write('\n'.join(str(r.randrange(-8192,8192)) for _ in range(120)))"
python3 ../run_hal.py net.v stim_dev.txt out_dev            # CUT_ALL=1 ... out_cutall for the robustness run
python3 ../../invfold/identify.py out_dev/equations.json out_dev/meta.json out_dev/recovered.json
mkdir -p heldout && python3 -c "import random; r=random.Random(20261030); open('heldout/stim.txt','w').write('\n'.join(str(r.randrange(-8192,8192)) for _ in range(20030))+'\n')"
iverilog -o heldout/gl.vvp ../tb_file.v biquad2_folded_xilinx.v /usr/share/yosys/xilinx/cells_sim.v && (cd heldout && vvp gl.vvp +ncyc=20030)
python3 ../score_e1.py out_dev heldout
```
Requires the HAL build of this branch (rebuilt `sequential_symbolic_execution` plugin, deviation D4).
