#!/usr/bin/env python3
"""Evaluation side: does a recovered transaction program compute the documented algorithm
on FRESH random inputs (not trace data)? Port names and references are used only here.
usage: check_reference.py PROGRAM_DIR CONTRACT_JSON CASE [N]"""
import json, random, sys

prog_dir, contract_path, case = sys.argv[1:4]
N = int(sys.argv[4]) if len(sys.argv) > 4 else 2000
contract = json.load(open(contract_path))
ns = {}
exec(open(f"{prog_dir}/program.py").read(), ns)
report = json.load(open(f"{prog_dir}/report.json"))
# Loop-carried state (values left by the previous transaction) is filled with RANDOM values
# on every trial: a pass then also shows that the outputs do not depend on that state.
state_keys = [tuple(k) for k in json.load(open(f"{prog_dir}/interface.json"))["state_keys"]] if report["state_values"] else []
by_bit = {p["physical_bit"]: p for p in contract["port_bits"] if p["direction"] == "input"}
src = open(f"{prog_dir}/program.py").read()
import re
try:  # decompile.py >= roll-up writes the interface; older program directories only have literal reads
    reads = sorted({(b[3:], str(ph)) for b, ph in json.load(open(f"{prog_dir}/interface.json"))["pad_reads"]})
except FileNotFoundError:
    reads = sorted(set(re.findall(r"pads\[\('pad(\d+)', (\d+)\)\]", src)))
M = lambda w: (1 << w) - 1


def S(x):
    return [0xC, 5, 6, 0xB, 9, 0, 0xA, 0xD, 3, 0xE, 0xF, 8, 4, 7, 1, 2][x]


def present80(pt, key):
    state = pt
    for r in range(1, 32):
        state ^= key >> 16
        state = sum(S((state >> (4 * i)) & 0xF) << (4 * i) for i in range(16))
        out = 0
        for i in range(64):
            out |= ((state >> i) & 1) << ((16 * i) % 63 if i != 63 else 63)
        state = out
        key = ((key << 61) | (key >> 19)) & M(80)
        key = (S(key >> 76) << 76) | (key & M(76))
        key ^= r << 15
    return state ^ (key >> 16)


def sha1_compress(words):
    """FIPS 180-4 SHA-1 compression of one 16-word block from the standard IV."""
    rol = lambda x, n: ((x << n) | (x >> (32 - n))) & M(32)
    h = [0x67452301, 0xEFCDAB89, 0x98BADCFE, 0x10325476, 0xC3D2E1F0]
    w = list(words)
    for t in range(16, 80):
        w.append(rol(w[t - 3] ^ w[t - 8] ^ w[t - 14] ^ w[t - 16], 1))
    a, b, c, d, e = h
    for t in range(80):
        if t < 20:
            f, k = (b & c) | (~b & d), 0x5A827999
        elif t < 40:
            f, k = b ^ c ^ d, 0x6ED9EBA1
        elif t < 60:
            f, k = (b & c) | (b & d) | (c & d), 0x8F1BBCDC
        else:
            f, k = b ^ c ^ d, 0xCA62C1D6
        a, b, c, d, e = (rol(a, 5) + f + e + k + w[t]) & M(32), a, rol(b, 30), c, d
    return [(x + y) & M(32) for x, y in zip(h, [a, b, c, d, e])]


def _sha1_self_test():
    import hashlib, struct
    msg = b"NetNotary SHA-1 single-block self test, 47 bytes"[:47]
    block = msg + b"\x80" + b"\x00" * (55 - len(msg)) + struct.pack(">Q", 8 * len(msg))
    digest = b"".join(struct.pack(">I", x) for x in sha1_compress(struct.unpack(">16I", block)))
    assert digest == hashlib.sha1(msg).digest(), "SHA-1 reference self test failed"


def arf(i1, i2, i3, i4, i5, i6, G1, G2, GG1, GG2):
    """PandA-bambu examples/arf_res_sharing/module.c (int arithmetic, 32-bit wrap)."""
    m = lambda x: x & M(32)
    op1, op2, op3, op4 = m(GG1 * i1), m(GG2 * i2), m(G1 * i2), m(G2 * i1)
    op5, op6, op7, op8 = m(G1 * i3), m(G2 * i4), m(G1 * i4), m(G2 * i3)
    op9, op10, op11, op12 = m(op1 + op2), m(op3 + op4), m(op5 + op6), m(op7 + op8)
    op13, op14 = m(op11 + i5), m(i6 + op12)
    op15, op16, op17, op18 = m(G1 * op14), m(op13 * G2), m(op13 * G1), m(op14 * G2)
    op19, op20 = m(op15 * op16), m(op17 + op18)
    op21, op22, op23, op24 = m(G1 * op20), m(op19 * G2), m(op19 * G1), m(op20 * G2)
    op25, op26 = m(op21 + op22), m(op23 + op24)
    return {"o1": op13, "o2": op14, "o3": m(op9 + op25), "o4": m(op10 + op26)}


def ascon_aead128_encrypt(key, nonce, ad, pt):
    import importlib.util
    spec = importlib.util.spec_from_file_location("ascon_ref", "/home/jiahao/hal/build/bench_candidates/ascon_primas/src/ascon.py")
    mod = importlib.util.module_from_spec(spec); spec.loader.exec_module(mod)
    return mod.ascon_encrypt(key, nonce, ad, pt, variant="Ascon-AEAD128")


def qdiv(a, b):
    ma, mb = a & M(31), b & M(31)
    mag = (ma << 15) // mb
    out = (((a ^ b) >> 31) << 31) | ((mag & ~1) & M(31))
    return out, int(mag >= (1 << 32))


_sha1_self_test()
rng = random.Random(20261104)
mismatch, first, compared, compared_missing = 0, None, 0, set()
for trial in range(N):
    per_cycle = {}
    if case == "present":
        key, pt = rng.getrandbits(80), rng.getrandbits(80)
        per_cycle = {0: {"data_i": key}, 1: {"data_i": pt}}
        expected = {"data_o": present80(pt & M(64), key)}
    elif case == "ibex_mul":
        a, b = rng.getrandbits(32), rng.getrandbits(32)
        per_cycle = {c: {"op_a_i": a, "op_b_i": b} for c in range(33)}
        expected = {"result_o": (a * b) & M(32)}
    elif case == "qdiv":
        a, b = rng.getrandbits(32), rng.getrandbits(32)
        while b & M(31) == 0:
            b = rng.getrandbits(32)
        per_cycle = {0: {"i_dividend": a, "i_divisor": b}}
        q, ovf = qdiv(a, b)
        expected = {"o_quotient_out": q, "o_overflow": ovf}
    elif case == "bambu_arf":
        names = ["i1", "i2", "i3", "i4", "i5", "i6", "G1", "G2", "G3", "G4", "GG1", "GG2"]
        x = {n: rng.getrandbits(32) for n in names}
        per_cycle = {c: x for c in range(contract["period_cycles"])}  # data_mode hold
        expected = arf(*(x[n] for n in ["i1", "i2", "i3", "i4", "i5", "i6", "G1", "G2", "GG1", "GG2"]))
    elif case == "ascon":
        # cocotb convention of the source repository: byte j of a 32-bit word is bits 8j..8j+7
        key, nonce, ad, pt = (bytes(rng.getrandbits(8) for _ in range(16)) for _ in range(4))
        word = lambda b, w: int.from_bytes(b[4 * w:4 * w + 4], "little")
        per_cycle = {}
        for w in range(4):
            per_cycle.setdefault(1 + w, {})["key"] = word(key, w)
            per_cycle.setdefault(5 + w, {})["bdi"] = word(nonce, w)
            per_cycle.setdefault(22 + w, {})["bdi"] = word(ad, w)
            per_cycle.setdefault(44 + w, {})["bdi"] = word(pt, w)
        ct, tag = ascon_aead128_encrypt(key, nonce, ad, pt)
        out = bytes(ct) + bytes(tag)  # ciphertext (16 B) || tag (16 B)
        expected = {("bdo", "cycle", k): word(out, k) for k in range(8)}
    elif case == "sha1":
        block = [rng.getrandbits(32) for _ in range(16)]
        # RTL timing (evaluation side): the command written in cycle 0 takes effect at cycle 1;
        # rounds 0..15 then latch text_i, so word j is sampled in cycle 1 + j.
        per_cycle = {1 + j: {"text_i": block[j]} for j in range(16)}
        expected = {("text_o", k): v for k, v in enumerate(sha1_compress(block))}
    else:
        raise SystemExit("no reference for " + case)
    pads = {}
    for pad, phase in reads:
        p = by_bit[int(pad)]; cycle = int(phase) // 2
        value = per_cycle.get(cycle, {}).get(p["name"], rng.getrandbits(64))  # unread/free cycles: random
        pads[(f"pad{pad}", int(phase))] = (value >> p["offset"]) & 1
    outs, _ = ns["transaction"](pads, {k: rng.getrandbits(512) for k in state_keys})
    by_port = {}
    for (port, phase), v in sorted(outs.items(), key=lambda kv: kv[0][1]):
        by_port.setdefault(port, []).append((phase, v))
    for key, v in expected.items():
        if isinstance(key, tuple) and len(key) == 3:  # k-th observed CYCLE of the port (both phases)
            port, _, k = key
            cyc = sorted({ph // 2 for ph, _ in by_port.get(port, [])})
            checked = [(ph, val) for ph, val in by_port.get(port, []) if k < len(cyc) and ph // 2 == cyc[k]]
            observed = checked
        else:
            port, k = key if isinstance(key, tuple) else (key, None)
            observed = by_port.get(port, [])
            checked = observed if k is None else observed[k:k + 1]
        if not checked:
            mismatch += 1; compared_missing.add(key if isinstance(key, str) else f"{port}[{k}]")
            continue
        for phase, value in checked:
            compared += 1
            if value != v:
                mismatch += 1
                if first is None:
                    first = {"trial": trial, "port": port, "phase": phase, "program": value, "reference": v}
result = {"case": case, "fresh_random_trials": N, "mismatches": mismatch, "first": first, "compared_values": compared,
          "missing_observations": sorted(compared_missing),
          "status": "PASS" if mismatch == 0 and compared else "FAIL", "pad_reads": len(reads)}
json.dump(result, open(f"{prog_dir}/reference_check.json", "w"), indent=1)
print(json.dumps(result))
