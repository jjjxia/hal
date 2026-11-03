#!/usr/bin/env python3
"""Evaluation side for STATEFUL recovered programs (streaming filters).

The recovered program is called once per protocol transaction, starting from the all-zero
loop-carried state, with the state it returns fed back. Data pads read in the same
transaction cycle form one input sample; samples are assigned in cycle order.

1. Impulse readout: feed a unit impulse at each sample position of one transaction and read
   the output stream. The (signed) responses, interleaved by input position, are the
   coefficients the program applies; the first nonzero response fixes the latency.
   Nothing about the filter is given to this step.
2. Reference: compare with the RTL constants (evaluation side only), then compare the program
   on N transactions of fresh random samples with the exact convolution by the RTL
   coefficients at the latency read in step 1.

usage: check_stream.py PROGRAM_DIR CONTRACT_JSON CASE [N]"""
import collections, json, random, re, sys

prog_dir, contract_path, case = sys.argv[1:4]
N = int(sys.argv[4]) if len(sys.argv) > 4 else 2000
contract = json.load(open(contract_path))
src = open(f"{prog_dir}/program.py").read()
ns = {}
exec(compile(src, f"{prog_dir}/program.py", "exec"), ns)
transaction = ns["transaction"]

def wrap(v, w):
    v &= (1 << w) - 1
    return v - (1 << w) if v >> (w - 1) else v


def clip(v, w_out):
    lo, hi = -(1 << (w_out - 1)), (1 << (w_out - 1)) - 1
    return min(max(v, lo), hi)


def round_nearest(v, w_in, w_out, safe):
    """uhd round.v, round_to_nearest=1: out = in[hi:drop] + in[drop-1] (w_out-bit wrap); 'safe' skips the
    correction when it would overflow (only generated when w_in - w_out > 1)."""
    drop = w_in - w_out
    u = v & ((1 << w_in) - 1)
    corr = (u >> (drop - 1)) & 1
    if safe and drop > 1 and not (u >> (w_in - 1)) & 1 and ((u >> w_out) & ((1 << (w_in - 1 - w_out)) - 1)) == (1 << (w_in - 1 - w_out)) - 1:
        corr = 0
    out = wrap((u >> drop) + corr, w_out)
    err = wrap(u - ((out & ((1 << w_out) - 1)) << drop), drop + 1)
    return out, err


def round_to_zero(v, w_in, w_out):
    drop = w_in - w_out
    u = v & ((1 << w_in) - 1)
    corr = 1 if (u >> (w_in - 1)) & 1 and u & ((1 << drop) - 1) else 0
    return wrap((u >> drop) + corr, w_out)


def small_hb_dec_model(x, newest):
    """Sample-level model of EttusResearch small_hb_dec.v (WIDTH=18, INTWIDTH=17, ACCWIDTH=30), written
    from the RTL: round_sd 18->17 on every input sample, taps r[n], r[n-2], r[n-3], r[n-4], r[n-6] on
    every second sample, MULT18X18S products rounded 36->30 toward zero, round_sd 30->19, clip to 18."""
    a, b = wrap(-10690, 18), wrap(75809, 18)
    e_in, e_out, r, ys = 0, 0, [], []
    for n in newest:  # one output per entry; samples before the stream start are zero (reset state)
        while len(r) <= n:
            s = clip(wrap(x[len(r)], 18) + e_in, 18)  # add2_and_clip_reg (register updated on the strobe)
            rn, e_in = round_nearest(s, 18, 17, safe=True)
            r.append(rn)
        R = lambda k: r[n - k] if n - k >= 0 else 0
        sum_a, sum_b, middle = wrap(R(0) + R(6), 18), wrap(R(2) + R(4), 18), wrap(R(3) * 2, 18)
        acc = wrap(middle * (1 << 10) + round_to_zero(b * sum_b, 36, 30) + round_to_zero(a * sum_a, 36, 30), 30)
        s2 = clip(acc + e_out, 30)
        out, e_out = round_nearest(s2, 30, 19, safe=True)
        ys.append(clip(out, 18))
    return ys


RTL = {  # evaluation-side ground truth, from the RTL sources
    "fir_decim": {"coefficients": [213, -962, 1778, 1778, -962, 213], "impulse": 1,
                  "source": "analogdevicesinc/hdl fir_decim.v coeffphase1_{1,2,3} (sfix12_En11), symmetric pre-adder"},
    # halfband taps in units of 2^-6 of the 30-bit accumulator: a, 0, b, middle (= 2^11 * 2^6), b, 0, a
    "small_hb_dec": {"coefficients": [-10690, 0, 75809, 131072, 75809, 0, -10690], "impulse": 1 << 16,
                     "readout_scale": (1 << 15) / (1 << 17), "model": small_hb_dec_model,
                     "source": "EttusResearch uhd small_hb_dec.v coeff_a=-10690, coeff_b=75809, middle tap {d3,0}<<10; round_sd/round/clip/MULT18X18S"},
}

inputs = {p["physical_bit"]: p for p in contract["port_bits"] if p["direction"] == "input"}
outputs = collections.defaultdict(list)
for p in contract["port_bits"]:
    if p["direction"] == "output":
        outputs[p["name"]].append(p)
try:
    interface = json.load(open(f"{prog_dir}/interface.json"))
    reads = sorted({(int(b[3:]), ph) for b, ph in interface["pad_reads"]})
    state_keys_if = sorted(tuple(k) for k in interface["state_keys"])
    out_ports_if = sorted({o[0] for o in interface["observations"]})
except FileNotFoundError:
    interface = None
    reads = sorted({(int(a), int(b)) for a, b in re.findall(r"pads\[\('pad(\d+)', (\d+)\)\]", src)})
names = {inputs[pad]["name"] for pad, _ in reads}
assert len(names) == 1, f"one data port expected, program reads {names}"
port = names.pop()
in_width = sum(1 for p in inputs.values() if p["name"] == port)
cycles = sorted({phase // 2 for _, phase in reads})
state_keys = state_keys_if if interface else sorted({eval(k) for k in re.findall(r"new_state\[(\(.*?\))\] =", src)})
out_ports = out_ports_if if interface else sorted({eval(k)[0] for k in re.findall(r"outputs\[(\(.*?\))\] =", src)})
assert len(out_ports) == 1, out_ports
out_port = out_ports[0]
out_width = len(outputs[out_port])


def signed(v, w):
    return v - (1 << w) if (v >> (w - 1)) & 1 else v


def run(samples):
    """samples: flat list of signed input samples, len = k * len(cycles). Returns one output per transaction."""
    state = {key: 0 for key in state_keys}
    ys = []
    for t in range(len(samples) // len(cycles)):
        by_cycle = {c: samples[t * len(cycles) + i] & ((1 << in_width) - 1) for i, c in enumerate(cycles)}
        pads = {(f"pad{pad}", phase): (by_cycle[phase // 2] >> inputs[pad]["offset"]) & 1 for pad, phase in reads}
        outs, state = transaction(pads, state)
        first = min((ph, v) for (name, ph), v in outs.items() if name == out_port)
        ys.append(signed(first[1], out_width))
    return ys


per_tx = len(cycles)
# E5 designs: a reference module c3/e5_refs/<case>.py may define STREAM with the keys of the RTL entries above
import importlib.util as _ilu, os as _os
_ref = _os.path.join(_os.path.dirname(_os.path.abspath(__file__)), "e5_refs", f"{case}.py")
if case not in RTL and _os.path.exists(_ref):
    _spec = _ilu.spec_from_file_location(f"e5ref_{case}", _ref); _m = _ilu.module_from_spec(_spec); _spec.loader.exec_module(_m)
    if hasattr(_m, "STREAM"):
        RTL[case] = _m.STREAM
# impulse window (E5 amendment k): 16 transactions, or enough to read out every tap of the reference filter plus
# latency; 16 for the frozen filters (6 and 7 taps), 20 for hb_dec's 31 taps
T = max(16, len(RTL.get(case, {}).get("coefficients", [])) // per_tx + 5)
assert run([0] * (per_tx * T)) == [0] * T, "zero input from zero state must give zero output"
responses = {}
amplitude = RTL.get(case, {}).get("impulse", 1)
for pos in range(per_tx):
    x = [0] * (per_tx * T); x[pos] = amplitude
    responses[pos] = run(x)
# coefficient for input at sample position pos seen in output m: tap index (m*per_tx + per_tx-1 - pos) - latency
taps = {}
for pos, ys in responses.items():
    for m, y in enumerate(ys):
        if y:
            taps[m * per_tx + (per_tx - 1 - pos)] = y
assert taps, "impulse produced no output"
lo, hi = min(taps), max(taps)
recovered = [taps.get(k, 0) for k in range(lo, hi + 1)]
latency_samples = lo  # output m depends on samples up to index m*per_tx + per_tx-1 - lo
report = {"case": case, "data_port": port, "samples_per_transaction": per_tx, "sample_cycles": cycles,
          "state_values": len(state_keys), "impulse_coefficients": recovered, "latency_samples": latency_samples}

if case in RTL:
    ref = RTL[case]
    report["impulse_amplitude"] = amplitude
    report["rtl_coefficients"] = ref["coefficients"]; report["rtl_source"] = ref["source"]
    h = ref["coefficients"]
    if "readout_scale" in ref:  # rounding inside the filter: the readout can only match to within 1 LSB
        scaled = [c * ref["readout_scale"] for c in h]
        report["rtl_coefficients_at_readout_scale"] = scaled
        report["readout_max_abs_error"] = max(abs(u - v) for u, v in zip(recovered, scaled)) if len(recovered) == len(h) else None
        report["coefficients_match"] = report["readout_max_abs_error"] is not None and report["readout_max_abs_error"] <= 1
    else:
        report["coefficients_match"] = recovered == h
    rng = random.Random(20261105)
    T2 = N
    x = [rng.randrange(-(1 << (in_width - 1)), 1 << (in_width - 1)) for _ in range(per_tx * T2)]
    y = run(x)
    newest = [m * per_tx + per_tx - 1 - latency_samples for m in range(T2)]
    if "model" in ref:
        model = ref["model"](x, newest)
    else:
        model = [signed(sum(h[k] * x[n - k] for k in range(len(h)) if 0 <= n - k < len(x)) & ((1 << out_width) - 1), out_width) for n in newest]
    report["reference_kind"] = "bit-exact RTL sample model" if "model" in ref else "exact convolution with the RTL coefficients"
    mismatch, first = 0, None
    for m in range(T2):
        expected = model[m]
        if y[m] != expected:
            mismatch += 1
            if first is None:
                first = {"transaction": m, "program": y[m], "reference": expected}
    report.update({"fresh_random_transactions": T2, "mismatches": mismatch, "first": first,
                   "status": "PASS" if mismatch == 0 and report["coefficients_match"] else "FAIL"})
else:
    report["status"] = "READOUT_ONLY"
json.dump(report, open(f"{prog_dir}/stream_check.json", "w"), indent=1)
print(json.dumps(report))
