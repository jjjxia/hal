"""A3: 'write = clock-enable 1' is false for flip-flops with a synchronous reset that overrides CE (Xilinx FDRE/FDSE).
rtl/clr_fir.v: folded 3-tap FIR (P = 4), `if (phi==0) acc <= 0; else acc <= acc + c*op;`. yosys synth_xilinx maps the
clear to FDRE.R with CE = 1'h1 (see rtl/clr_fir_xilinx.v). The E1 frontend (E1/run_hal.py) takes write enables from the
CE pins only and the equation of a word from its D pins (HAL SSE target i_D), so in slot 0 it emits
acc <- acc + 7*d2 (the D-pin function under the default mux select), CE = 1, while the hardware writes 0.
This file builds exactly that frontend equation system, runs the recognizer, and compares with the true hardware.
Expected truth: y = 3 x[n-1] + 5 x[n-2] + 7 x[n-3] (FIR). Recognizer: a recursive section with a pole at z = 1."""
import sys, os, random
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from harness import *
W = {'acc': 16, 'd0': 16, 'd1': 16, 'd2': 16, 'y': 16}; IN = {'x': 16}; OUT = {'yo': 16}
def mac(T, k, d): return '(bvadd %s (bvmul %s %s))' % (R('acc', T), c(k, 16), R(d, T))
def step_hw(T, frontend):
    phi = T % 4; wr = {}
    if phi == 0:
        wr.update({'d0': X('x', T), 'd1': R('d0', T), 'd2': R('d1', T), 'y': R('acc', T)})
        wr['acc'] = mac(T, 7, 'd2') if frontend else c(0, 16)   # D pins vs. what FDRE.R=1 writes
    else: wr['acc'] = mac(T, {1: 3, 2: 5, 3: 7}[phi], {1: 'd0', 2: 'd1', 3: 'd2'}[phi])
    return wr, {'yo': R('y', T)}
fe = lambda T: step_hw(T, True); hw = lambda T: step_hw(T, False)
eqs, meta = build(W, IN, OUT, fe, 60, range(20, 60))
F = recover(eqs, meta); rep = report(F)
print(identify.summary(rep))
rng = random.Random(3); xs = {'x': [rng.randrange(-100, 100) & 0xffff if t >= 8 else 0 for t in range(60)]}
bad_fe, n = check_execute(F, W, IN, OUT, fe, 60, xs, {r: 0 for r in W}, 8)
bad_hw, n = check_execute(F, W, IN, OUT, hw, 60, xs, {r: 0 for r in W}, 8)
print('recovered model vs frontend equations: %d/%d mismatches; vs the hardware (FDRE.R semantics): %d/%d mismatches' % (len(bad_fe), n, len(bad_hw), n))
