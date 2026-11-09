#!/usr/bin/env python3
"""E3.5 for Bambu ARF (evaluation side): which module.c operation does each recovered arithmetic
node compute? Runs the recovered program on random inputs, reads every arithmetic node's value
(bits in the recorded output order), and matches it against op1..op28 of
PandA-bambu examples/arf_res_sharing/module.c on all trials.
usage: arf_truth.py PROGRAM_DIR CONTRACT_JSON [N]"""
import collections, json, random, re, sys

prog, contract_path = sys.argv[1:3]
N = int(sys.argv[3]) if len(sys.argv) > 3 else 200
contract = json.load(open(contract_path))
report = json.load(open(f"{prog}/report.json"))
iface = json.load(open(f"{prog}/interface.json"))
src = open(f"{prog}/program.py").read().replace("    return outputs, new_state\n", "    return outputs, new_state, v\n")
ns = {}
exec(src, ns)
inputs = {p["physical_bit"]: p for p in contract["port_bits"] if p["direction"] == "input"}
M = (1 << 32) - 1
MUL = {1: ("GG1", "i1"), 2: ("GG2", "i2"), 3: ("G1", "i2"), 4: ("G2", "i1"), 5: ("G1", "i3"), 6: ("G2", "i4"),
       7: ("G1", "i4"), 8: ("G2", "i3"), 15: ("G1", 14), 16: (13, "G2"), 17: (13, "G1"), 18: (14, "G2"),
       19: (15, 16), 21: ("G1", 20), 22: (19, "G2"), 23: (19, "G1"), 24: (20, "G2")}
ADD = {9: (1, 2), 10: (3, 4), 11: (5, 6), 12: (7, 8), 13: (11, "i5"), 14: ("i6", 12), 20: (17, 18),
       25: (21, 22), 26: (23, 24), 27: (9, 25), 28: (10, 26)}


def ops(x):
    op = {}
    val = lambda a: x[a] if isinstance(a, str) else op[a]
    for k in range(1, 29):
        if k in MUL:
            op[k] = (val(MUL[k][0]) * val(MUL[k][1])) & M
        else:
            op[k] = (val(ADD[k][0]) + val(ADD[k][1])) & M
    return op


rng = random.Random(20261108)
nodes = [a for a in report["arithmetic"] if "output_order" in a]
matches = {i: None for i in range(len(nodes))}
names = ["i1", "i2", "i3", "i4", "i5", "i6", "G1", "G2", "G3", "G4", "GG1", "GG2"]
for t in range(N):
    x = {n: rng.getrandbits(32) for n in names}
    pads = {}
    for pad, ph in iface["pad_reads"]:
        p = inputs[int(pad[3:])]
        pads[(pad, ph)] = (x[p["name"]] >> p["offset"]) & 1
    outs, _, v = ns["transaction"](pads, collections.defaultdict(int))  # unread-state default; outputs do not depend on it (check_reference randomises it)
    op = ops(x)
    for i, a in enumerate(nodes):
        node = a["node"]
        if node[0] == "output":
            raw = outs[(node[1], node[2])]
        elif isinstance(node[1], int):
            raw = v[(node[0], node[1])]
        else:
            continue  # loop templates (none in ARF)
        value = sum(((raw >> o) & 1) << q for q, o in enumerate(a["output_order"]))
        hit = {k for k, y in op.items() if y == value}
        matches[i] = hit if matches[i] is None else matches[i] & hit
result = []
for i, a in enumerate(nodes):
    result.append({"node": a["node"], "kind": a["kind"], "proof": a["proof"], "operands": len(a["operand_widths"]),
                   "matches_module_c": sorted(matches[i]) if matches[i] is not None else None})
mul_ops = sorted({k for r in result if r["kind"] == "bilinear" for k in (r["matches_module_c"] or [])})
add_ops = sorted({k for r in result if r["kind"] == "linear" for k in (r["matches_module_c"] or [])})
summary = {"trials": N, "nodes": len(result),
           "multiplication_nodes_matched_to": mul_ops, "module_c_multiplications": sorted(MUL),
           "addition_nodes_matched_to": add_ops, "module_c_additions": sorted(ADD),
           "unmatched_nodes": [r["node"] for r in result if not r["matches_module_c"]], "per_node": result}
json.dump(summary, open(f"{prog}/arf_truth.json", "w"), indent=1)
print(json.dumps({k: v for k, v in summary.items() if k != "per_node"}))
