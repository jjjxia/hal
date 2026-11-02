#!/usr/bin/env python3
"""E2.4 N3 evaluation: run the recovered program as a stream from the all-zero (reset) state
and compare with y_t = (a_{t-1} + b_{t-1}) + (a_t ^ b_t) mod 2^16, p = 0 before the first
transaction. usage: check_hidpipe.py PROGRAM_DIR CONTRACT_JSON [N]"""
import json, random, sys
prog, contract_path = sys.argv[1:3]
N = int(sys.argv[3]) if len(sys.argv) > 3 else 2000
c = json.load(open(contract_path))
ns = {}
exec(open(f"{prog}/program.py").read(), ns)
iface = json.load(open(f"{prog}/interface.json"))
bits = {p["physical_bit"]: p for p in c["port_bits"] if p["direction"] == "input"}
reads = [(b, ph) for b, ph in iface["pad_reads"]]
state = {tuple(k): 0 for k in iface["state_keys"]}
rng = random.Random(20261107)
prev, bad, first = 0, 0, None
for t in range(N):
    a, b = rng.getrandbits(16), rng.getrandbits(16)
    val = {"a": a, "b": b}
    pads = {(pad, ph): (val[bits[int(pad[3:])]["name"]] >> bits[int(pad[3:])]["offset"]) & 1 for pad, ph in reads}
    outs, state = ns["transaction"](pads, state)
    got = next(v for (port, ph), v in outs.items() if port == "y")
    exp = (prev + (a ^ b)) & 0xFFFF
    if got != exp:
        bad += 1; first = first or {"t": t, "program": got, "reference": exp}
    prev = (a + b) & 0xFFFF
r = {"case": "neg_hidpipe", "stream_transactions": N, "state_values": len(state), "mismatches": bad, "first": first,
     "status": "PASS" if bad == 0 else "FAIL"}
json.dump(r, open(f"{prog}/stream_check.json", "w"), indent=1)
print(json.dumps(r))
