# Evaluation plan (2026-10-29, agreed with the user)

Three parts. Part 1 is finished elsewhere; parts 2 and 3 are to do. Read PROPOSAL.md first.

## Part 1 — errors of trace-guided equation recovery, and that certification generalizes (DONE, other repos)
Purpose: show that equations recovered by Maggie-style trace-guided symbolic execution are wrong in practice, that
other dynamic/trace-guided recovery methods produce the same kind of error, and that certification rejects them in
general — while being incomplete (correct updates needing data invariants stay uncertified).
Evidence (do not redo; cite): /home/jiahao/HardwareRE/dynamicRE/research_loop/runs/2026-10-23-open-gap/claude/algo/
- allbench/ (audit of all 14 artifact netlists: 5 processed netlists not equivalent; authors' concretization rule:
  canny 18/207 and 27/248 wrong words accepted by the analysed-window check; FINDING_MUX3.md, CORRECTIONS.md).
- othermethods_review/OTHER_METHODS_SUMMARY.md (F1 invariant mining, F2 fitting, F2+cec, F3 authors' rule;
  18,564 certified candidates, 0 wrong on any replayed window; F2+cec accepts 36 wrong; unmodified Daikon 5.9.0:
  17 trace-consistent wrong invariants on canny; Trace2Model results pending).
- faultinj/RESULT.md (699 equation mutants over 4 designs: 0 false certifications; trace checks accept 14-56%).
- Incompleteness: 1518 (canny) / 89 (Hilbert) rejected candidates agree with all traces / reachable samples.
Paper: /home/jiahao/NetNotary (branch inverse-folding for the merged version).

## Part 2 — how many open-source designs the algorithm recovery recovers (accuracy and time)
No published baseline exists for automatic equation-to-algorithm recovery (the Maggie paper: "weeks to days",
final interpretation by a signal-processing expert; it states the effectiveness cannot be expressed in numbers).
Report absolute accuracy and time; internal ablations (e.g. register-level grouping without inverse folding) serve
as reference points, not as baselines from the literature.

Benchmarks (fixed list; add only with a recorded reason):
| design | source | why it is here | ground truth |
|---|---|---|---|
| Hilbert transformer, Xilinx (+ Lattice) | Klix et al. CCS'24 artifact | DEV set (rules were written on it); fully parallel; P = 2 | white-box sections (eq2algo/gt) |
| ZipCPU dspfilters: slowfil (one multiplier time-multiplexed over all taps, ring-buffer sample memory), slowfil_srl, genericfir, fastfir | https://github.com/ZipCPU/dspfilters (LGPL-3.0) | time multiplexing WITH shared state in memory; same function in parallel and folded form | coefficients in the RTL |
| Yamaha OPN2 (YM2612/YM3438) FM synthesis | IKAOPN https://github.com/ika-musume/IKAOPN (BSD; die-shot based, cycle accurate); jt12 https://github.com/jotego/jt12 (operator section described as an exact replica; check licence) | a real commercial chip (Sega Mega Drive) reconstructed from die photos; 6 channels x 4 operators time-multiplexed on one operator pipeline; nonlinear sine/exp ROMs | Yamaha datasheet + community documentation (operator algorithms 0-7) |
| HLS-generated designs | CHStone (adpcm, gsm, jpeg, ...; https://www.github.com/ferrandi/CHStone) compiled with Bambu/PandA (open source) at 2-3 resource constraints | folding by an independent tool; C source = ground truth; varying multiplexing | C source |
| constructed folded designs (E1, E2 in PROPOSAL.md) | ours | controlled folding factor, shared state, multi-rate | by construction |
Out-of-library negatives: wrong coefficients, wrong widths, algorithms absent from the library -> must be "no match".
Metrics: algorithm graph exact up to G_obs (retiming, slot symmetries, I/Q conjugation, scale); section family and
parameters; false matches on negatives; wall time per stage (HAL module identification, guided SE, certification,
inverse folding, identification) and end-to-end, single runs on the 7.8 GB machine, reported as such.
Split: Hilbert = dev; everything else = held out, rules R1-R5 and the library frozen before the first run.
Before running each design: check licence, synthesizability with yosys (Xilinx 7-series / iCE40), and record the
guided-SE cutpoint policy.

## Part 3 — 1-2 real commercial FPGAs, recovered automatically (IP protected)
Selection conditions: (a) FPGA family with an open bitstream database (iCE40: Project IceStorm and the Maggie
tooling; also ECP5/Trellis, 7-series/X-Ray); (b) the bitstream is obtainable from official, publicly distributed
firmware (no device unlocking, no circumvention of protection); (c) the design is DSP/control-like (multiply-add
chains, SB_MAC16 use, periodic state updates) so that inverse folding and identification apply.
Candidates (the Maggie paper lists iCE40 use in Apple Vision Pro, Apple TV 4K, HTC Vive Pro, Samsung Galaxy S5,
Pebble Time):
| device | FPGA (teardown) | function | fit |
|---|---|---|---|
| Apple Vision Pro | Lattice ICE5LP4K iCE40 Ultra (iFixit chip ID) — same part family as Maggie (iCE5LP4K in iPhone 7) | unconfirmed; sits next to an ADI TMC5072 stepper-motor driver; EE Times speculates a sensor-fusion hub | promising if motor control / sensor processing |
| HTC Vive Pro | Lattice iCE40HX8K (iFixit teardown) | unconfirmed; likely display/interface | check |
| Apple TV 4K | iCE40 (per Maggie paper) | unknown | check |
| Samsung Galaxy S5 | iCE40 (per Maggie paper) | unknown (IR/sensor uses are typical for iCE40 in phones) | check |
| Pebble Time | iCE40 (per Maggie paper); community firmware needs snowy_fpga.bin | display driver | poor fit (not DSP) |
Feasibility step (half a day per device, ONLY after the user approves the device): download the official public
firmware; locate the iCE40 bitstream; convert to a gate-level netlist; decide (c) from the netlist structure.
Ethics and IP (agreed): the paper reports method, effort and verification outcome only, never the recovered
algorithm, coefficients or anything that would let readers rebuild the IP (as the Maggie authors did: "to protect
Apple's IP, we do not disclose such details"). Notify the IP owner (and Lattice if relevant) before submission.
Confirm the legal position (good-faith security research; Lattice removed its EULA clause against bitstream reverse
engineering in 2020) with the advisor / institution before any analysis. Keep recovered artifacts out of public
repositories.

## Status
Part 1 done (pending: Trace2Model numbers). Part 2: benchmark list fixed above, nothing run. Part 3: candidates
listed, no firmware downloaded, awaiting the user's choice of device.
