# E4: inverse folding on NetNotary-certified forms (exploratory, not preregistered; 2026-10-29)

Input: the certified per-phase forms of NetNotary (dynamicRE .../claude/algo, CERT_INDEX.md), one certified period
each, plus structural metadata from the .hal (widths, D/Q bit maps, output-port aliases, enable nets). Period taken
from the certificate. Enables (XTEA) read from the trace the forms came from. Time alignment calibrated on that trace
(sim1), then fixed; sim2/sim3 are held out. Code: run_cert.py, ../invfold/{invfold,halforms}.py. Outputs: out/.

| | DES (P = 16) | XTEA (P = 80) |
|---|---|---|
| logical signals | 32 (2 words x 16 slots), all nonlinear | 244 |
| resolution errors | none | none |
| inputs | desIn in 1 phase; key in 16 phases (constant in the workload) | block in 1 phase; key in 64 phases |
| least-delay origin | load slot: feed-forward 16-round chain, total delay 0 | total delay 0 (16 origins tie: idle slots) |
| other origins | 15/15: exactly 3 delay-1 edges (retiming) | 63 origins 12, 1 origin 8 |
| execution vs netlist simulation | 0/704 mismatches on sim1, sim2, sim3 | data words 0/1312 on sim1, sim2, sim3 |

XTEA: the 3-bit control FSM word (DANA_module_20, data-independent) mismatches 632/640: its D pin group has 2 bits
and its Q group 3, so the D->Q map (built per flip-flop) is incomplete. Frontend gap, not a datapath result; fix by a
partial per-flip-flop map.
Frontend fixes made during this run (not method changes): port pin groups `*_ordered` have their own bit order; DANA
i_D and o_Q groups are ordered independently (map D->Q per flip-flop, as NetNotary does); output port `result` is
the Q bits of four words (alias per bit).
Not shown yet: that the 16 DES / 64 XTEA round nodes are the same round function up to key-bit selection (temporal
rerolling); FFT (state in BRAM). Execution agreement is a test of the implementation and alignment; the guarantee
comes from the NetNotary certificate plus Theorem 1.

## Update 2026-10-30: FFT64 (Xilinx), P = 128, with memories and DSPs (exploratory, not preregistered)
Driver fft_run.py; checks fft_memcheck.py, fft_exec.py, fft_blocks.py; outputs out/fft_*.json.
- Inputs: NetNotary's four forms files for FFT64 (main, suspects, 108/109, 125), the .hal, and trace sim1 for control.
- Memories: 3 block RAMs (SDP, READ_FIRST, latency 1 or 2) and 68 SRL16E shift registers (depth 3), resolved by the
  address-specific last write and transparently (written data are words, ports or other memories' read data).
  The memory model alone predicts every RAM read output on 3 traces x 3 RAMs (500/500 each; fft_memcheck).
- DSP48E1: 4 multipliers of the complex twiddle multiply, modelled as an A-register pseudo-word (routed A input,
  traced through multiplexers under known control and the protocol SHIFT = 0) and a P pseudo-word = A_reg x B, with B
  the data-independent twiddle read from the trace; all configuration assumptions asserted.
- Bit order inferred for word 125 (its pin groups carry none) from pure routing.
- Steady-state resolution (periodic extension), with control checked 128-periodic on the trace after the window
  (RAM addresses and word enables are 128-periodic from cycle 176 on).
- Result: 8,576 nodes, 0 resolution errors, 0 nonlinear, 0 incomplete equations (UNKNOWN_OPERATION occurs in 128
  equations of word 116 but is unreachable under the checks).
- Execution (fft_exec): the recovered single-rate graph reproduces DOR/DOI on 604/604 cycles of sim1 and of the
  held-out sim2 and sim3 (frames >= 1; input alignment +1 cycle calibrated on sim1, as for DES).
- Frame level (fft_blocks): all 8,192 entries of the frame matrix are complex-linear ([[a,-b],[b,a]]) with |c| = 1/8;
  112 of 128 outputs depend on 64 consecutive input samples; within a window 57 samples follow one DFT bin exactly
  (e.g. bin 3 with time 0 at window position 7) while a few samples at the window start do not fit any common time
  permutation; 8 of every 64 outputs use a window shifted by one sample. Interpretation (pending an RTL check of the
  START protocol): a scaled (1/8) 64-point DFT with a block-boundary misalignment under the certified protocol
  START = 0. Not claimed as identified.
Open point, recorded honestly: the RTL (dynamicRE harder_netlists/algo_designs/fft64/RTL/bufram64c1.v) uses a 2 x 64
ping-pong input buffer, written in natural order and read 8x8-transposed (addrr = {addr[2:0], addr[5:3]}), with blocks
aligned by a START-reset counter; by design that has no block-boundary hazard, and NetNotary's audit does not list
FFT64 among the processed netlists that disagree with their sources. So the observed misalignment is either due to the
START timing of this workload (the trace pulses START; the certificate's protocol takes START = 0) or an error in my
frame analysis. The execution check (604/604 on three traces) shows the recovered graph equals the netlist; only the
frame-level reading is in question.

### Correction (round 5 of the adversarial review, 2026-10-30 02:10)
fft_exec validates the recovered graph with its RAW node expressions; the frame matrix H is built from the FITTED
coefficients and had not been validated. fft_Hcheck.py executes H as a linear model on the three traces: only 123,
124 and 122 of 256 output components agree with DOR/DOI within 4 LSB (out/fft_Hcheck.json); the others deviate by
hundreds to tens of thousands. Hence the frame-level reading above (DFT structure with misaligned samples; the
per-input phase factors every 8 samples and the reversed time order found by the review) is NOT validated and may be
an artifact of the fits or of linearization (candidates: 'quant' nodes with approximate coefficients, a sign or bit
order error in a fitted DSP/twiddle node, intermediate overflow for full-scale inputs). Withdrawn until H agrees with
the simulation. What stands: the recovered graph itself (604/604 on three traces), 0 resolution errors, and the memory,
SRL and DSP modelling.
Output wrap excluded: comparing modulo 2^19 gives the same counts (out/fft_Hcheck_mod.json).
