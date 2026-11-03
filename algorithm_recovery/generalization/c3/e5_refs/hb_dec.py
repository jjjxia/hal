"""E5 evaluation-side reference for case hb_dec (EttusResearch uhd, fpga/usrp2/sdr_lib/hb_dec.v, WIDTH=24 default).

hb_dec is a STREAMING halfband decimator (state carried across transactions: 16 odd samples + 8..10 even samples in
SRL16 memories, and the error-feedback register of the input rounder). It has no per-transaction pure function, so
check_reference's trial() convention (random loop-carried state) does not apply; trial() below raises. The reference
for c3/check_stream.py is STREAM (same keys as check_stream.RTL entries: coefficients, impulse, model, source).

Independence: the filter taps are the RTL constants (hb_dec.v:113-129; "myfilt = round(2^18 * halfgen4(.7/4,8))",
hb_dec.v:21-22 -- halfgen4 (Matlab, ldoolittle) is not available, so an independent re-derivation of the taps is NOT
practical). The model is the exact integer convolution with those taps plus the rounding/clipping steps the RTL
documents (round_sd, round, clip, sign_extend in sdr_lib); it is a sample-level model, not a cycle-level transcription.

Sample-level model (WIDTH = 24, INTWIDTH = 17, products 36 bit):
  r[n]  = round_sd 24 -> 17 of input sample x[n] (hb_dec.v:43-44): s = clip25->24(x[n] + err[n-1]) registered on the
          strobe (add2_and_clip_reg.v), r = round-to-nearest(-safe) of s dropping 7 bits (round.v), err = s - (r << 7)
          (8-bit), fed back to the next sample (round_sd.v). err = 0 and all memories 0 after reset.
  The output m is produced for every 2nd sample (the odd-phase ones, hb_dec.v:55-62); with N = index of that sample:
  acc   = sum_{j=0..15} h_odd[j] * r[N - 2j], h_odd = (-107, 445, -1271, 2959, -6107, 11953, -24706, 82359) mirrored
          (taps (0,15),(1,14),...,(7,8) of the 16-deep odd SRL, hb_dec.v:84-100, :113-141), exact (no overflow, :161)
  y     = clip25->24( wrap25( round 36->25 (acc) + (r[N-15] << 6) ) )        (hb_dec.v:167-182)
  i.e. the 31-tap halfband [A 0 B 0 ... H 0.5 H ... 0 B 0 A] (hb_dec.v:19) in units of 2^-18 with centre 2^17.
Impulse readout: an input impulse of 2^18 gives r = 2^11 with zero rounding error, so the output stream equals the
integer taps exactly (COEFFICIENTS below).

Protocol (spec.json, measured on the RTL): cpi = 4 (non-data, must equal the strobe spacing, see README), stb_in = 1 in
cycles 0 and 4 of every 8-cycle transaction; the cycle-0 sample is the even phase, the cycle-4 sample the odd phase
(odd toggles from 0 after reset, hb_dec.v:55-59). One output per transaction: stb_out = 1 in cycle 15 relative to the
transaction whose cycle-4 sample is the newest one (= cycle 7 of the next transaction frame).
"""

H_ODD = (-107, 445, -1271, 2959, -6107, 11953, -24706, 82359)
COEFFICIENTS = []
for _k in range(31):
    if _k == 15:
        COEFFICIENTS.append(1 << 17)
    elif _k % 2 == 0:
        _j = _k // 2
        COEFFICIENTS.append(H_ODD[_j] if _j < 8 else H_ODD[15 - _j])
    else:
        COEFFICIENTS.append(0)
assert COEFFICIENTS[14] == COEFFICIENTS[16] == 82359 and COEFFICIENTS == COEFFICIENTS[::-1]


def wrap(v, w):
    v &= (1 << w) - 1
    return v - (1 << w) if v >> (w - 1) else v


def clip(v, w_out):
    lo, hi = -(1 << (w_out - 1)), (1 << (w_out - 1)) - 1
    return min(max(v, lo), hi)


def round_nearest(v, w_in, w_out):
    """round.v with round_to_nearest=1: out = in[hi:drop] + in[drop-1] (w_out-bit wrap); the correction is skipped when
    in >= 0 and in[w_in-2:w_out] is all ones (generated because w_in - w_out > 1). err = in - (out << drop), drop+1 bits."""
    drop = w_in - w_out
    u = v & ((1 << w_in) - 1)
    corr = (u >> (drop - 1)) & 1
    if drop > 1 and not (u >> (w_in - 1)) & 1 and ((u >> w_out) & ((1 << (w_in - 1 - w_out)) - 1)) == (1 << (w_in - 1 - w_out)) - 1:
        corr = 0
    out = wrap((u >> drop) + corr, w_out)
    err = wrap(u - ((out & ((1 << w_out) - 1)) << drop), drop + 1)
    return out, err


def model(x, newest):
    """x: input samples (signed or unsigned 24-bit ints) in strobe order from reset; newest: for each output, the index
    of its newest input sample. Returns the signed 24-bit outputs."""
    r, e_in, ys = [], 0, []
    for n in newest:
        while len(r) <= n:
            s = clip(wrap(x[len(r)], 24) + e_in, 24)
            rn, e_in = round_nearest(s, 24, 17)
            r.append(rn)
        R = lambda k: r[n - k] if n - k >= 0 else 0
        acc = sum(COEFFICIENTS[k] * R(k) for k in range(31) if k != 15)
        acc_rnd, _ = round_nearest(acc, 36, 25)
        ys.append(clip(wrap(acc_rnd + R(15) * (1 << 6), 25), 24))
    return ys


STREAM = {"coefficients": COEFFICIENTS, "impulse": 1 << 18, "model": model,
          "source": "EttusResearch uhd hb_dec.v (WIDTH=24): coeff1 = -107/445/-1271/2959, coeff2 = -6107/11953/-24706/82359 "
                    "(hb_dec.v:113-129), centre r[n-15] << 6 (:173-177); round_sd 24->17, round 36->25, clip 25->24"}


def _self_test():
    imp = [0] * 40
    imp[0] = 1 << 18
    assert model(imp, list(range(31))) == COEFFICIENTS  # impulse readout = integer taps
    assert model([0] * 8, [1, 3, 5, 7]) == [0, 0, 0, 0]


_self_test()


def trial(rng, contract):
    raise NotImplementedError("hb_dec is a streaming design with loop-carried state: use STREAM with c3/check_stream.py")
