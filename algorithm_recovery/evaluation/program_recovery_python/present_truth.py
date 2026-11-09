#!/usr/bin/env python3
"""E3.5 for PRESENT (evaluation side): is the recovered loop body's state update exactly one
PRESENT round (addRoundKey with key[79:16], sBoxLayer, pLayer) in the RTL bit order?
DANA word bits are mapped to RTL register bits through the non-anonymous synthesized netlist
(anonymisation keeps physical bit ids). The recovered tables and index tuples are taken from
the generated program and evaluated as they are.
usage: present_truth.py PROGRAM_DIR GRAPH_JSON SYNTH_JSON [N]"""
import json, random, re, sys

prog, graph_path, synth_path = sys.argv[1:4]
N = int(sys.argv[4]) if len(sys.argv) > 4 else 2000
src = open(f"{prog}/program.py").read()
ns = {}
exec(src.split("def transaction(")[0], ns)  # helpers and module-level tables only
body = src[src.index("for j in range(16):  # 16 instances of TABLE0"):]
m = re.search(r"\|= place\((TABLE\d+)\[bits\(s0, \*(IN\d+)\[j\]\) \^ bits\(s1, \*(IN\d+)\[j\]\)\], \*(OUT\d+)\[j\]\)", body)
T, IA, IB, O = (ns[x] for x in m.groups())
syn = json.load(open(synth_path))
top = next(m for m in syn["modules"].values() if str(m.get("attributes", {}).get("top", "0")).strip("0") != "")
name_of = {}
for nm, d in top["netnames"].items():
    for i, b in enumerate(d["bits"]):
        if isinstance(b, int) and (b not in name_of or len(nm) < len(name_of[b][0])):
            name_of[b] = (nm, i)
g = json.load(open(graph_path))
pos = lambda w, reg: [name_of[int(b[1:])][1] if name_of[int(b[1:])][0] == reg else None for b in g["words"][w]["bits"]]
state_pos, key_pos = pos("DANA_module_0", "state"), pos("DANA_module_1", "key")
assert None not in state_pos and None not in key_pos
to_dana = lambda x, p: sum(((x >> r) & 1) << i for i, r in enumerate(p))
from_dana = lambda x, p: sum(((x >> i) & 1) << r for i, r in enumerate(p))
SPEC = [0xC, 5, 6, 0xB, 9, 0, 0xA, 0xD, 3, 0xE, 0xF, 8, 4, 7, 1, 2]


def spec_round(state, key):
    s = state ^ (key >> 16)
    s = sum(SPEC[(s >> (4 * i)) & 0xF] << (4 * i) for i in range(16))
    return sum(((s >> i) & 1) << ((16 * i) % 63 if i != 63 else 63) for i in range(64))


def recovered_round(state, key):
    s0, s1 = to_dana(state, state_pos), to_dana(key, key_pos)
    u = 0
    for j in range(16):
        u |= ns["place"](T[ns["bits"](s0, *IA[j]) ^ ns["bits"](s1, *IB[j])], *O[j])
    return from_dana(u, state_pos)


rng = random.Random(20261109)
bad = sum(recovered_round(st, k) != spec_round(st, k) for st, k in ((rng.getrandbits(64), rng.getrandbits(80)) for _ in range(N)))
# structural reading in the RTL order: which state nibble feeds instance j, where its outputs go
inst = []
for j in range(16):
    nib = sorted({state_pos[k] // 4 for k in IA[j]})
    inst.append({"instance": j, "state_nibble": nib, "key_bits": sorted(key_pos[k] for k in IB[j]),
                 "outputs_rtl": [state_pos[k] for k in O[j]]})
res = {"case": "present", "random_state_key_pairs": N, "mismatches_vs_spec_round": bad,
       "status": "PASS" if bad == 0 else "FAIL", "each_instance_reads_one_nibble": all(len(x["state_nibble"]) == 1 for x in inst),
       "nibbles_covered": sorted(x["state_nibble"][0] for x in inst if len(x["state_nibble"]) == 1),
       "key_bits_are_round_key": all(min(x["key_bits"]) >= 16 for x in inst), "instances": inst}
json.dump(res, open(f"{prog}/present_truth.json", "w"), indent=1)
print(json.dumps({k: v for k, v in res.items() if k != "instances"}))
