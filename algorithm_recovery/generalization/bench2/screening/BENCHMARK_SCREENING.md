# Benchmark-candidate screening for inverse folding (folded / time-multiplexed datapaths)

Date: 2026-11-01. All numbers below were measured in this session unless they are marked
"not verified" or "estimate". Nothing under `/home/jiahao/hal`, `/home/jiahao/NetNotary` or
`/home/jiahao/HardwareRE` was modified. Nothing was committed.

## Setup

* Clones: `/home/jiahao/hal/build/bench_candidates/<name>/src` (`git clone --depth 50` or a sparse
  `--depth 1` clone for big monorepos; commit = `git rev-parse HEAD`). Two entries are symlinks to clones
  that already existed: `hace_vivado/src -> build/c2_sources/hace` and `picorv32_pcpi_div/src ->
  build/c2_sources/picorv32`.
* Our wrappers, models and patched copies sit beside the clones in `wrap/`, `conf/` and `conv/`; the clones
  themselves were not edited.
* Synthesis: Yosys 0.33 (`2584903a060`), one run at a time, `ulimit -v 8000000`, `timeout 1200`.
  The script (`bench_candidates/_tools/synth.sh`) runs `read_verilog FILES; [chparam ...];
  synth_ice40 -top TOP -nobram -json OUT; stat`. The outputs (`synth.ys`, `yosys.log`, `stat.txt`,
  `result.txt`, `<top>.json`) are in `bench_candidates/<name>/synth/<tag>/`.
* Cell counts come from the final `stat`. DFF is the sum of all `SB_DFF*` cells.
  In every run, the only cells were `SB_LUT4`, `SB_CARRY` and `SB_DFF*`. No RAM or other primitives appeared.
* Additional tools downloaded to `/tmp/opencode/bench/tools`:
  * sv2v v0.0.13 (static binary), used for the SystemVerilog sources.
  * Bambu 2024.10 AppImage, 1.4 GB. FUSE is missing, so it ran from `--appimage-extract` (4.4 GB) with
    `APPDIR`, `PATH` and `LD_LIBRARY_PATH` set. Installing it is practical: the download took about 2 minutes.
    It generates designs, but Bambu's own `--simulate` flow failed here: Icarus Verilog is not recognised and
    the Verilator simulation does not finish correctly. We used our own Icarus testbenches instead.
* Functional checks used Icarus Verilog with our own testbenches. They are copied to
  `bench_candidates/<name>/check/`. For all eight picks, the post-synthesis iCE40 netlist
  (`write_verilog` from the JSON plus `/usr/share/yosys/ice40/cells_sim.v`) was simulated with the same
  stimulus. It reproduced the RTL outputs (or the reference values, for Ascon) exactly.
  The gate-level testbenches are in `bench_candidates/_gatelevel/`.

## Summary of all screened candidates

LUT4 / CARRY / DFF come from `synth_ice40 -nobram` (Yosys 0.33). "–" means not synthesised.

| # | Candidate (top) | Author / organisation | Category | LUT4 / CARRY / DFF | Status | Reason |
|---|---|---|---|---|---|---|
| 1 | ascon_primas (`ascon_core`, V1) | R. Primas (TU Graz) | cipher, AEAD | 3762 / 11 / 486 | **PICK** | Round-iterative 320-bit state; CC0; checked against pyascon; needs sv2v |
| 2 | oc_present (`present_encryptor_top`) | R. Ameli (Ferdowsi U.) | cipher | 287 / 3 / 153 | **PICK** | 31-round reuse of one state register; paper vectors reproduced |
| 3 | oc_sha_core (`sha1`) | marsgod (OpenCores) | hash | 1574 / 224 / 893 | **PICK** | 80 rounds plus a 16-word sliding message window; checked against hashlib |
| 4 | adi_fir_decim (`fir_decim`) | Analog Devices | folded DSP | 518 / 54 / 200 | **PICK** | One pre-adder/multiplier/accumulator shared by 3 taps; exactly linear and time-invariant |
| 5 | ettus small_hb_dec | Ettus Research | folded DSP | 884 / 220 / 330 | **PICK** | One multiplier and accumulator shared by 2 coefficients |
| 6 | ibex multdiv_slow (`ibex_md_top`) | lowRISC | iterative arithmetic | 1049 / 36 / 143 | **PICK** | Radix-2 mult/div with time-shared ALU adder; fixed timing when `data_ind_timing_i=1` |
| 7 | bambu_arf (`arf`) | PandA-Bambu (PoliMi), ARF benchmark | HLS with sharing | 2270 / 242 / 336 | **PICK** | 14 products on 1 shared multiplier; 11 shared registers; 15 states |
| 8 | oc_fixedpoint `qdiv` | T. Burke / S. Skalicky | iterative arithmetic | 520 / 227 / 265 | **PICK** | 47-iteration shift-subtract divider; LGPL; upstream LSB defect characterised |
| 9 | oc_cordic (`cordic`, ITERATE) | D. Drinkard (OpenCores) | iterative CORDIC | 527 / 114 / 55 | alternate | Very good structure, but no license is stated and the source needs 2 patches |
| 10 | ettus `hb_dec` | Ettus Research | folded DSP | 2078 / 225 / 818 | alternate | 2 multipliers × 4 phases plus SRL memories; needs our SRL16E model |
| 11 | present_saiedhk (`PRESENT_ENCRYPT`) | S. H. Khayat | cipher | 281 / 3 / 152 | alternate | Duplicate algorithm; 1-line patch (`$display` aborts Yosys 0.33) |
| 12 | oc_sha3 `f_permutation` | H. Hsing | Keccak-f[1600] | 4448 / 0 / 1625 | alternate (size) | Above the size sweet spot |
| 13 | oc_aes_core (`aes_cipher_top`) | R. Usselmann | AES-128 | 5588 / 4 / 562 | alternate (size) | 20 S-boxes in LUT logic |
| 14 | secworks_aes (`aes_core`) | J. Strömbergson | AES | 9204 / 8 / 2476 | rejected (size) | AES-128 encrypt-only wrapper is still 4570 / 4 / 2209 (15×128 round-key memory as FFs) |
| 15 | secworks_siphash | J. Strömbergson | ARX keyed hash | 1235 / 260 / 462 | alternate | Same author as Maggie's SHA-256 |
| 16 | secworks_chacha | J. Strömbergson | ARX stream cipher | 3240 / 1049 / 1100 | alternate | Same author |
| 17 | secworks_sha256 | J. Strömbergson | hash | 2784 / 378 / 1034 | **already in use** | Maggie's sha256 is secworks (arXiv 2312.06195, App. C), used in RQ1 |
| 18 | oc_sha_core `sha256` | marsgod | hash | 2419 / 375 / 1103 | alternate | Repo testbench fails under Icarus (race); not re-verified |
| 19 | oc_fixedpoint `qmults` | T. Burke | iterative multiplier | 183 / 108 / 192 | alternate | Same author as qdiv |
| 20 | bambu `icrc1` (PowerStone crc) | Bambu-generated | HLS fixed-trip loop | 69 / 30 / 52 | alternate (tiny) | 8-iteration loop with if-converted select; 50/50 checks pass |
| 21 | hace_vivado `gaussian` | Vivado HLS 2019.1 via HACE | HLS loop nest | 1542 / 95 / 400 | alternate | Multiplier submodule missing (we re-created it); transaction >10k cycles (estimate) |
| 22 | hace_vivado `matrix` | Vivado HLS via HACE | HLS | 1474 / 77 / 378 | rejected | 32³ loop nest, about 3·10⁵ cycles per transaction (estimate) |
| 23 | hace_vivado `gsum` | Vivado HLS via HACE | HLS | 5722 / 275 / 913 | rejected | 4 separate multipliers (no operator sharing); large |
| 24 | hace LegUp outputs | LegUp 5.0 via HACE | HLS | – | rejected | `legup_mult_*` library modules are missing; extra clock ports; "academic use only" header |
| 25 | oc_des area_opt (`des`) | R. Usselmann | DES | fails | **already in use** | Matches the paper's DES (external `roundSel`, no control registers); upstream `area_opt/key_sel.v` declares `key_selh`, so it does not elaborate unmodified |
| 26 | picorv32 `pcpi_div` | YosysHQ | iterative divider | 673 / 214 / 200 | not a new pick | Same repo/author as the pcpi_mul already used |
| 27 | secworks_gift | J. Strömbergson | cipher | – | rejected | README says "Does not work. Do Not Use." |
| 28 | inmcm Simon_Speck_Ciphers | C. McCoy | cipher | – | rejected | VHDL only; ghdl not installed |
| 29 | ascon-hw-public (Kandi et al.) | IIT team | cipher | – | rejected | No license file; external round counter; bit-serial wrapper |
| 30 | Verilog-Time-Multiplexed-FIR | D. Diesmo | folded FIR | – | rejected | No controller (schedule supplied externally); non-synthesisable `always @(posedge CLK, EN)` / `always @(RST)`; latch |
| 31 | Multi_IIR | D. Hatch | folded IIR | – | rejected | Xilinx `blk_mem_gen` IP plus AXI; no license |
| 32 | VexRiscv MulDiv | SpinalHDL | iterative arithmetic | – (not cloned) | rejected | Exists only inside generated CPU Verilog; instruction-driven |
| 33 | SERV | O. Kindgren | bit-serial CPU | – (not cloned) | rejected | Instruction-driven, data-dependent schedule |

Already in use, not re-picked: ZipCPU dblclockfft, seqcordic and slowfil; YosysHQ picorv32 `pcpi_mul`;
secworks SHA-256 (Maggie); the OpenCores DES above.

## Recommended final picks (8 picks, 8 authors/organisations, 4 categories)

| Pick | Category | Author / org | LUT4 / CARRY / DFF | Cycles per transaction | Yosys time |
|---|---|---|---|---|---|
| Ascon-AEAD128 (`ascon_core` V1) | cipher (round-iterative) | Primas / TU Graz | 3762 / 11 / 486 | 75 | 14.3 s |
| PRESENT-80 (`present_encryptor_top`) | cipher (round-iterative) | Ameli / Ferdowsi U. | 287 / 3 / 153 | 33 | 0.8 s |
| SHA-1 (`sha1`) | hash (round-iterative) | marsgod / OpenCores | 1574 / 224 / 893 | 92 | 3.0 s |
| ADI `fir_decim` | folded DSP | Analog Devices | 518 / 54 / 200 | 10 (5 per input) | 1.2 s |
| Ettus `small_hb_dec` | folded DSP | Ettus Research | 884 / 220 / 330 | 8 (4 per input) | 1.7 s |
| Ibex slow MULT/DIV (`ibex_md_top`) | iterative arithmetic | lowRISC | 1049 / 36 / 143 | 33 (MUL) / 37 (DIV) | 1.8 s |
| Bambu ARF, 1 multiplier (`arf`) | HLS with sharing | PoliMi Bambu (ARF from Jain'89) | 2270 / 242 / 336 | 16 | 3.4 s |
| `qdiv` fixed-point divider | iterative arithmetic | Burke / Skalicky | 520 / 227 / 265 | 48 | 1.1 s |

Possible substitutions: CORDIC (`oc_cordic`) for `qdiv` if its missing license is acceptable. It has the
nicer structure: 16 micro-rotations with iteration-dependent shifts and constants. Use `hb_dec` instead of
`small_hb_dec` for deeper folding. Use Keccak-f or OpenCores AES if one larger design is wanted.

### 1. Ascon-AEAD128, variant V1 (`ascon_primas`)
* **Source and build**
  * URL: https://github.com/rprimas/ascon-verilog
  * Commit: `e1069549a1895f376391530a1842694ea8bb044b`
  * License: CC0-1.0
  * Top: `ascon_core`
  * Files: `rtl/ascon_core.sv`, which includes `config.sv`, `functions.sv`, `asconp.sv` and `register.sv`.
  * Parameters: V1, meaning `UROL=1` round per cycle and `CCW=32` bits.
  * Preprocessing: `sv2v -I src/rtl -DV1 src/rtl/ascon_core.sv > conv/ascon_core_v1.v`. Yosys 0.33 cannot parse
    the multi-dimensional packed arrays (`asconp.sv:28`).
* **Storage reuse**
  * The single 320-bit register `reg_state_i` (`ascon_core.sv:79`) is reused throughout the transaction.
  * It is written with the Ascon-p round output whenever the FSM is in INIT, PRO_AD, PRO_MSG or FINAL
    (`ascon_core.sv:374-375`). The round counter is reloaded with 12 or 8 (`ascon_core.sv:439-442`) and
    decremented every cycle (`ascon_core.sv:445`).
  * It also takes 32-bit slices during nonce, AD and message absorption (`ascon_core.sv:349`), plus padding,
    key additions and domain separation.
  * In the 75-cycle transaction it holds the results of 12+8+8+8+12 = 48 permutation rounds plus 15 absorb,
    pad and key-add updates. The FSM trace was recorded in the protocol CSV.
  * The 128-bit `reg_key_i` (`ascon_core.sv:76`) is written word by word.
* **Data-independent schedule**
  * FSM next-state logic (`ascon_core.sv:254-340`) depends only on: `fsm_q`, `round_cnt_q`, `word_cnt_q`, `mode`,
    the handshake inputs (`key_valid`, `bdi_valid`, `bdi_type`, `bdi_eot`, `bdi_eoi`, `bdo_ready`, `bdo_eoo`)
    and `ready` signals derived from the FSM.
  * Key and data values never steer control. The only data comparison is tag verification in decryption mode
    (`auth_intern_d`), which affects a flag, not the FSM.
* **Inputs**
  * Non-data: `clk`, `rst` (async, active-high), `mode`, `key_valid`, `bdi_valid`, `bdi_type`, `bdi_eot`,
    `bdi_eoi`, `bdo_ready`, `bdo_eoo`.
  * Data: `key[31:0]`, `bdi[31:0]`.
* **Synthesis**
  * 3762 LUT4 / 11 CARRY / 486 DFF (271 SB_DFFER, 214 SB_DFFR, 1 SB_DFFS); 14.3 s.
* **Reference and checks**
  * The repo ships `ascon.py` (pyascon) and a cocotb `test.py`. cocotb is not installed, so that test was not run.
  * Our Icarus testbench ran 5 random (key, nonce, AD[16], PT[16]) vectors. Ciphertext and tag matched pyascon
    on both the RTL and the gate-level netlist.
* **Risks**
  * Needs sv2v.
  * Handshake inputs must follow the recorded control vector.
  * Async reset.
  * Near the top of the size range.

### 2. PRESENT-80 (`oc_present`)
* **Source and build**
  * URL: https://github.com/freecores/present_encryptor
  * Commit: `953c39e01b29e73c39487dc8e14e4f3d62cd23a9`
  * License: LGPL-2.1-or-later (file headers)
  * Top: `present_encryptor_top`
  * Files: `rtl/verilog/{present_encryptor_top,sub_per,substitution,permutation,sbox,key_update}.v`
  * No preprocessing.
* **Storage reuse**
  * `state` is loaded at `data_load` (`present_encryptor_top.v:82`) and then overwritten by the SP-network
    output in every non-load cycle (`:88`).
  * `key` holds the evolving round key (`:76`, `:89`).
  * `round_counter` is set to 1 at load (`:83`) and incremented every cycle (`:87`).
  * One 64-bit register therefore carries 31 logical round states.
  * The output `data_o = state ^ key[79:16]` is combinational (`:67`).
* **Data-independent schedule**
  * The only control inputs are `key_load` and `data_load` (`:74`, `:80`).
  * The round counter enters the datapath only through the key schedule (`key_update.v:51`).
* **Inputs**
  * Non-data: `clk_i`, `key_load`, `data_load`.
  * Data: `data_i[79:0]` (key in cycle 0, plaintext in cycle 1).
* **Synthesis**
  * 287 LUT4 / 3 CARRY / 153 DFF; 0.8 s.
* **Reference and checks**
  * The repo testbench `tb/testbench.v` reproduces the four PRESENT-paper test vectors.
  * Our strictly periodic 33-cycle testbench passed 10/10 random vectors against a Python PRESENT-80 model,
    on both RTL and gate level.
* **Risks**
  * There is no reset pin.
  * The key register is overwritten by the key schedule, so every transaction must reload the key.

### 3. SHA-1 (`oc_sha_core`, top `sha1`)
* **Source and build**
  * URL: https://github.com/freecores/sha_core
  * Commit: `f90e67a6040d7dd5de54188ed0b8b7ec05d4ac85`
  * License: OpenCores BSD-style notice (free use and redistribution with the copyright notice kept; no warranty)
  * File: `rtl/sha1.v`
  * No preprocessing.
* **Storage reuse**
  * `A..E` are rewritten in every one of the 80 rounds (`case (round)` at `sha1.v:185`). The round update is
    `next_A` at `:136`.
  * `W0..W14` and `Wt` form a 16-word sliding window. Rounds 0-15 load it from `text_i` (`:187-412`).
    Rounds 16-79 shift it and insert the expanded word (`:420-505`, using `SHA1_Wt_1` at `:133`).
  * The round function `ft` (`:131`) and the constant register `Kt` (`:537-550`) change with the round index.
  * Round 80 adds the chaining values `H0..H4` (`:510-518`).
* **Data-independent schedule**
  * Once `cmd[1]` is written (`:112`, `:189`), `round` advances unconditionally to 80 and then clears `busy`.
  * The digest is read out through a fixed 5-word counter.
* **Inputs**
  * Non-data: `clk_i`, `rst_i` (sync, active-high), `cmd_i[2:0]`, `cmd_w_i`.
  * Data: `text_i[31:0]`.
* **Synthesis**
  * 1574 LUT4 / 224 CARRY / 893 DFF; 3.0 s.
* **Reference and checks**
  * The repo's `bench/test_sha1.v` and `test_sha256.v` print ERROR (output 0) under Icarus. They change inputs
    right after `@(posedge clk)`, which is a race.
  * Our race-free testbench used a strictly periodic 92-cycle schedule with random data on `text_i` outside the
    load window. All 5 random single-block messages matched `hashlib.sha1` on RTL and gate level.
* **Risks**
  * The repo testbench cannot be relied on.
  * Input words are taken only in cycles 1-16.

### 4. ADI `fir_decim` (decimate-by-2 polyphase FIR, generic MAC branch)
* **Source and build**
  * URL: https://github.com/analogdevicesinc/hdl
  * Commit: `1eb1555998e4caa7e19da403e7a64f6767f0116f`
  * License: GPL-2.0 or ADI-BSD (dual, per file header)
  * File: `library/axi_adc_decimate/fir_decim.v`
  * Parameter: `chparam -set USE_DSP48E 0 fir_decim`. This selects the author's own generic MAC branch;
    the default branch instantiates a DSP48E1.
* **Storage reuse**
  * `count` steps 0→1→2 for each output (`fir_decim.v:108-109`) and selects one of three coefficients (`:145`).
  * The same `pre_adder` (`:248`), `product` (`:253`) and `_sum` (`:257-259`) registers hold the values for
    3 symmetric tap pairs in consecutive cycles.
  * The bit-sliced `storage0/1` shift memories (`:128`, `:131`) are read with a `count`-dependent index
    (`:137-138`).
* **Data-independent schedule**
  * `phase` toggles on `clk_enable` (`:90`); `active` starts on `clk_enable & phase` (`:97`); `count` is set
    by `:98`, `:108`; `ready` comes from `:116`.
  * None of them depends on `filter_in`.
* **Inputs**
  * Non-data: `clk`, `reset`, `clk_enable`.
  * Data: `filter_in[11:0]`.
* **Synthesis**
  * 518 LUT4 / 54 CARRY / 200 DFF; 1.2 s.
* **Reference and checks**
  * The repo has no testbench, only the MATLAB file `m2k_decim.m`.
  * Our check fed one impulse and 300 random samples. A least-squares fit gives an exact (residual 0)
    decimate-by-2 FIR with h = [213, −962, 1778, 1778, −962, 213], which matches the coefficient constants
    in the RTL. Gate-level output was identical to RTL.
* **Risks**
  * Needs the `USE_DSP48E=0` override.
  * The memories have `initial` values.

### 5. Ettus `small_hb_dec` (halfband decimator, `[A 0 B 0.5 B 0 A]`)
* **Source and build**
  * URL: https://github.com/EttusResearch/uhd (sparse clone)
  * Commit: `d1be63490e3f3df22ff2f4ea1315f9aada468562`
  * License: GPL-3.0-or-later (file headers)
  * Top: `small_hb_dec`
  * Files: `fpga/usrp2/sdr_lib/{small_hb_dec,round_sd,add2_and_clip_reg,add2_and_clip,round,clip,sign_extend}.v`
    and `fpga/usrp2/models/MULT18X18S.v`. The multiplier model is Ettus' own GPL behavioural model.
  * Parameter: `WIDTH=18`.
* **Storage reuse**
  * One `MULT18X18S` (`small_hb_dec.v:105`) is shared between coefficient B and coefficient A. The operand
    and coefficient muxes are switched by `go_d1` (`:102-103`).
  * The product register and `accum` hold different partial results in consecutive cycles:
    `accum <= middle + B·sum_b` at `go_d2` (`:119`) and `accum += A·sum_a` at `go_d3` (`:121`).
  * `sum_a` and `sum_b` are written by `go` (`:92-93`).
* **Data-independent schedule**
  * `phase` toggles on input strobes (`:54`); `go = stb & phase` (`:55`); the `go_d*` delay chain is at `:66`.
* **Inputs**
  * Non-data: `clk`, `rst`, `bypass` (fixed 0), `run` (fixed 1), `stb_in`.
  * Data: `data_in[17:0]`.
* **Synthesis**
  * 884 LUT4 / 220 CARRY / 330 DFF; 1.7 s.
* **Reference and checks**
  * The repo `hb_dec_tb.v` drives this module from `input.dat`; we did not run it.
  * Our impulse test gives odd-phase taps [−2672, 18952, 18952, −2672]/65536 and a centre tap of 0.5.
    Gate-level output was identical to RTL.
* **Risks**
  * `round_sd` uses error-feedback rounding, so the filter has state and is slightly non-linear.
  * Several registers have no reset. RTL simulation needed `rst` held for about 20 cycles to avoid X;
    iCE40 FFs power up at 0.

### 6. Ibex slow multiplier/divider (`ibex_multdiv_slow`, top `ibex_md_top`)
* **Source and build**
  * URL: https://github.com/lowRISC/ibex (sparse clone)
  * Commit: `f4515520c0045bb8ef1e72d46ed82499a6ac5f18`
  * License: Apache-2.0
  * Files: `rtl/ibex_pkg.sv`, `ibex_alu.sv`, `ibex_multdiv_slow.sv`, `ibex_multdiv_fast.sv`, `ibex_ex_block.sv`
    and `vendor/lowrisc_ip/ip/prim/rtl/prim_assert.sv`. The fast unit is only needed for elaboration and is pruned.
  * Parameters: `RV32M=RV32MSlow`, `RV32B=None`, `BranchTargetALU=0`.
* **Our wrapper**
  * `ibex_multdiv_slow/wrap/ibex_md_top.sv` (about 30 lines) instantiates `ibex_ex_block` and adds the two 34-bit
    `imd_val` registers. Upstream they live in `ibex_id_stage.sv:445-452` and the wrapper reproduces that code.
  * It also ties off the ALU and branch inputs.
  * Conversion: `sv2v -DSYNTHESIS -DYOSYS` produces `conv/ibex_md_top.v`.
* **Storage reuse**
  * `imd_val_q[0]` (the accumulator/remainder window) and `imd_val_q[1]` are rewritten in every iteration
    (port logic at `ibex_multdiv_slow.sv:87-92`).
  * `op_b_shift_q`, `op_a_shift_q` and `op_numerator_q` shift every iteration.
  * The single ALU adder (`alu_adder_ext_i`, `:77-79`) is time-shared between: operand negation (MD_ABS_A/B),
    the 32 add/subtract iterations, and the final sign change (MD_CHANGE_SIGN).
  * `multdiv_count` runs 31→0 (`:226`, `:246`).
* **Data-independent schedule**
  * With `data_ind_timing_i=1`, the only data-dependent transitions (early exit for small MUL operands and
    division by zero, `:194`, `:210`, `:222`, `:255`) are disabled.
  * With `data_ind_timing_i=0` the schedule depends on the data, so the pin must be tied to 1.
* **Inputs**
  * Non-data: `clk_i`, `rst_ni`, `md_op_i`, `signed_mode_i`, `mult_en_i`, `div_en_i`, `data_ind_timing_i`.
  * Data: `op_a_i`, `op_b_i`.
* **Synthesis**
  * 1049 LUT4 / 36 CARRY / 143 DFF; 1.8 s.
* **Reference and checks**
  * Reference is the RISC-V M semantics, computed with a Python model.
  * 200 handshake operations (MUL, MULH, DIV, REM, DIVU, including divide-by-0 and INT_MIN/−1) and 60 strictly
    periodic operations all matched, on RTL and gate level.
  * `valid_o` appeared at a fixed offset in every period.
* **Risks**
  * Needs sv2v plus our wrapper.
  * Operands are read in several states, so they must be held for the whole period.

### 7. Bambu-generated ARF with one shared multiplier (`bambu_arf`, top `arf`)
* **Source and generation**
  * Input: PandA-bambu `examples/arf_res_sharing/module.c` at commit `c2ba6936ca2ed63137095fea0b630a1c66e20e63`.
    This is the classic auto-regressive lattice filter HLS benchmark (16 multiplications, 12 additions;
    Jain 1989 / Mukherjee 1996). The commit is identical to the revision of the Bambu 2024.10 binary.
  * Generation command:
    `bambu module.c --top-fname=arf wrap/constraints_ui_mult1.xml --generate-interface=INFER`
    with Bambu 2024.10. Output: `gen_shared/arf.v`, 1401 lines.
  * The constraint file is ours and limits `ui_mult_expr_FU` to n=1. The upstream XML constrains `mult_expr_FU`,
    which no longer binds because 2024.10 turns the products into unsigned operations. With the upstream XML
    Bambu instantiated 14 multipliers.
  * License: the input belongs to the PandA repo (GPL-3.0); the generated library components carry the
    `PANDA_LGPLv3` tag.
* **Storage reuse**
  * One `ui_mult_expr_FU` instance (`arf.v:759-765`) is fed through the operand mux trees MUX_24/MUX_25 and
    performs all 14 products, one per state.
  * 11 `register_SE` (`arf.v:693-757`) hold all temporaries. Bambu's log reports "11 registers (LB:6)".
  * `controller_arf` (`arf.v:774`, states S_0..S_14 from `:852`) is a 15-state FSM.
* **Data-independent schedule**
  * The C code is straight-line. Bambu reports minimum = maximum = 15 cycles.
* **Inputs**
  * Non-data: `clock`, `reset` (active-low), `start_port`.
  * Data: `i1..i6`, `G1`, `G2`, `GG1`, `GG2`. `G3` and `G4` are unused by the C function.
* **Synthesis**
  * 2270 LUT4 / 242 CARRY / 336 DFF; 3.4 s.
* **Reference and checks**
  * Reference is the C function with 32-bit wrap-around.
  * 20 handshake vectors and 20 strictly periodic (16-cycle) vectors matched on RTL and gate level.
* **Risks**
  * Tool-generated, so cite Bambu 2024.10 and the constraint file.
  * Outputs `o1..o4` are combinational and valid only when `o*_vld` is high.
  * Inputs are read in several states.

### 8. Fixed-point divider `qdiv` (`oc_fixedpoint`)
* **Source and build**
  * URL: https://github.com/freecores/verilog_fixed_point_math_library
  * Commit: `a94eb0b2b1e4117e942e4a93fc3773bc78607c28`
  * License: LGPL-2.1
  * File: `qdiv.v`
  * Parameters: `Q=15`, `N=32`
  * No preprocessing.
* **Storage reuse**
  * `reg_working_dividend`, `reg_working_divisor` and `reg_working_quotient` are rewritten in each of the
    47 iterations (`qdiv.v:86-99`).
  * The quotient bit is written at a variable index `[reg_count]` (`:92`).
* **Data-independent schedule**
  * Start is accepted only when `reg_done && i_start` (`:72`). `reg_count` runs N+Q−1 = 46 down to 0
    (`:75`, `:97`).
  * The comparison at `:91` only selects the update. It does not change control flow.
* **Inputs**
  * Non-data: `i_clk`, `i_start`.
  * Data: `i_dividend`, `i_divisor`, sampled only at start.
* **Synthesis**
  * 520 LUT4 / 227 CARRY / 265 DFF; 1.1 s.
* **Reference and checks**
  * The repo testbenches (`TestDiv*.v`) were not run.
  * Our check: the output always equals the ideal Q15 quotient with bit 0 forced to 0. This is an upstream defect:
    `reg_quotient` is copied before the last quotient bit is set.
  * The ideal-division comparison fails on 52/100 random cases. The LSB-cleared model matches 100/100 handshake
    and 20/20 strictly periodic (48-cycle) cases, on RTL and gate level.
* **Risks**
  * No reset; relies on `initial` values.
  * The LSB defect must be modelled in any reference.

## Proposed fixed protocols

The clock is implicit. "cN" is the cycle index within one period; inputs change at the falling edge before the
sampling edge. Inputs not listed are free data, but only the listed windows influence the result.

| Pick | Reset / warm-up | Fixed non-data pins | Period N | Strobe / control pattern | Data windows | Output observation |
|---|---|---|---|---|---|---|
| Ascon `ascon_core` | `rst=1` for 1 cycle (async, active-high) | – | 75 | Replay `ascon_primas/check/ascon_aead128enc_ad16_pt16_v1.csv`: c0 `mode=1` (AEAD-ENC) with `key_valid=1`; c1-4 `key_valid=1`; c5-8 `bdi_type=NONCE`, `bdi_valid=4'hf` (`eot` in c8); c9-21 idle; c22-25 AD; c44-47 PT with `bdo_ready=1`, `eot`/`eoi` in c47; c71-74 `bdo_ready=1` | `key` in c1-4; `bdi` in c5-8, c22-25, c44-47 | CT on `bdo` in c44-47, tag on `bdo` in c71-74 |
| PRESENT | none (no reset pin) | – | 33 | c0 `key_load=1`; c1 `data_load=1`; c2-32 both 0 | `data_i`=key in c0, `data_i[63:0]`=plaintext in c1 | `data_o` during c0 of the next period |
| SHA-1 | `rst_i=1` for ≥1 cycle (sync) | – | 92 | c0 `cmd_w_i=1`, `cmd_i=3'b010`; c84 `cmd_w_i=1`, `cmd_i=3'b001`; otherwise `cmd_w_i=0` | `text_i` = W0..W15 in c1-16 | `text_o` H0..H4 at c87-91 |
| ADI `fir_decim` | `reset=1` for ≥1 cycle (sync) | – | 5 per input, 10 per output | `clk_enable=1` in c0 of every 5-cycle slot | `filter_in` while `clk_enable=1` | `filter_out` when `ce_out=1` (every 10 cycles) |
| Ettus `small_hb_dec` | `rst=1` for 20 cycles (RTL needs it) | `bypass=0`, `run=1` | 4 per input, 8 per output | `stb_in=1` in c0 of every 4-cycle slot | `data_in` while `stb_in=1` | `data_out` when `stb_out=1` (every 8 cycles) |
| Ibex `ibex_md_top` | `rst_ni=0` for ≥1 cycle (async) | `data_ind_timing_i=1`; `md_op_i`, `signed_mode_i` fixed per run; `mult_en_i=1` (MUL runs) or `div_en_i=1` (DIV runs) held | 33 (MULL/MULH), 37 (DIV/REM) | none beyond the held enable; one operation per period | `op_a_i`, `op_b_i` held for the whole period | `result_o` when `valid_o=1` (fixed offset) |
| Bambu ARF | `reset=0` for ≥1 cycle (active-low) | – | 16 | c0 `start_port=1` | `i1..i6`, `G1`, `G2`, `GG1`, `GG2` held for the whole period | `o2`@c1, `o1`@c3, `o3`@c12, `o4`@c13 (when `o*_vld`); `done_port`@c13 |
| `qdiv` | none (`initial` values) | – | 48 | c0 `i_start=1` | `i_dividend`, `i_divisor` in c0 | `o_quotient_out` and `o_overflow` once `o_complete` rises (c47) |

## Notes on rejected and secondary sources

* **HACE** (`build/c2_sources/hace`, commit `a8d16f7…`, GPL-3.0).
  * It ships Vivado HLS 2019.1 and LegUp 5.0 RTL for 10 PolyBench-style kernels.
  * The Vivado files omit the `*_mul_32s_32s_32_5_1` submodules. We re-created the standard 5-stage template
    (`hace_vivado/wrap/*_mul_model.v`) to synthesise gsum, gaussian and matrix.
  * The schedules are data-independent: if-conversion is used, and loops have fixed trip counts. `kmp` is the
    exception, with data-dependent while loops.
  * But trip counts are 10³-10⁵ iterations per transaction. gsum allocates 4 separate multipliers.
  * The LegUp files instantiate missing `legup_mult_*` library modules, carry `clk2x` and `clk1x_follower`
    ports, and a "research/academic use only" header.
  * Best HACE option: `gaussian` (1 multiplier, 1542 LUT4), but the transaction is long (estimate >10k cycles;
    not simulated).
* **Bambu** is the better HLS route because the sharing level can be controlled through the constraint XML.
  The PandA repository's `examples/` also contains CHStone, MachSuite, PolyBench and hls_study kernels.
  Note that `examples/crc/spec.c` does not compile with the host's `math.h`; we extracted `icrc1` verbatim.
* **secworks**: SHA-256 is already in use through Maggie. AES is too large: the 15×128-bit key memory becomes
  1920 FFs. GIFT is marked non-working upstream. SipHash and ChaCha are valid alternates but add no new author.
* **OpenCores DES** (Usselmann `area_opt`): its interface (external `roundSel`, no control registers) matches the
  paper's DES. As cloned it does not elaborate (`key_sel.v` defines `key_selh`). We did not patch it.

## Not verified or caveats

* Repo testbenches not run: Ascon cocotb, Ettus `hb_dec_tb`, `qdiv` `TestDiv*`, Keccak, OpenCores AES, secworks
  testbenches.
* Not simulated by us: `hb_dec`, `present_saiedhk`, `f_permutation`, `oc_aes_core`, secworks cores and the HACE
  designs. Their cycle counts in the table are estimates from the RTL and are marked as such.
* The CORDIC patches were checked: the shifter rewrite is equivalent on 200k random cases, and the patched core
  passes the repo's 91-angle testbench (max error 2.6·10⁻⁴). Its license status is still unresolved.
* We have not run any of these designs through the paper's pipeline. Suitability for the method is argued
  from the RTL and from simulation.
