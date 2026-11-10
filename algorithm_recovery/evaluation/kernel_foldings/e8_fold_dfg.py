#!/usr/bin/env python3
"""E8.2 generator: registered fixed-binding folding of a kernel DFG (e8/kernels.py) -> fold_top.v and truth.json.

usage: e8_fold_dfg.py KERNEL NSPEC POLICY SEED OUT_DIR
  NSPEC   min | 2min | 4min | 8min  (multiples of the least feasible folding factor N_min; see below)
  POLICY  asap (list scheduling, each node at its earliest slot) | random (uniform slot between earliest and latest)
Rules as e5c/e5c_gen.py (control none): a node's result is written to its bound register at the end of its slot;
same-sample operands come from strictly earlier slots; a delayed operand (node or input) is read from copy registers
d_<src>_<k> shifted at slot N-1, so a delayed node must be scheduled at slot <= N-2; units per op type as the busiest slot
needs, random binding within a slot; left-edge register sharing (lowest free register). N_min is the least N for which
the ASAP schedule satisfies these rules. 16-bit words.
"""
import heapq, json, os, random, sys
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import kernels as KM

kname, nspec, policy, seed, out = sys.argv[1], sys.argv[2], sys.argv[3], int(sys.argv[4]), sys.argv[5]
os.makedirs(out, exist_ok=True)
W = KM.W
kern = KM.KERNELS[kname]()
nodes = {v: dict(nd, operands=[list(o) for o in nd["operands"]]) for v, nd in kern["nodes"].items()}
inputs = kern["inputs"]
rng = random.Random(seed * 7919 + sum(map(ord, kname)) * 31 + {"min": 1, "2min": 2, "4min": 4, "8min": 8}[nspec] + (0 if policy == "asap" else 500))

order, done = [], set()
while len(order) < len(nodes):
    for v in sorted(nodes, key=lambda x: int(x[1:])):
        if v not in done and all(d > 0 or s in inputs or s in done for s, d in nodes[v]["operands"]):
            order.append(v); done.add(v)
succ = {v: [] for v in nodes}
delayed_read = set()
for v in order:
    for s, d in nodes[v]["operands"]:
        if s in nodes:
            if d == 0:
                succ[s].append(v)
            else:
                delayed_read.add(s)
asap = {}
for v in order:
    asap[v] = max([asap[s] + 1 for s, d in nodes[v]["operands"] if s in nodes and d == 0] or [0])
n_min = max(max(asap.values()) + 1, max([asap[v] + 2 for v in delayed_read] or [0]))
N = {"min": 1, "2min": 2, "4min": 4, "8min": 8}[nspec] * n_min
latest = {}
for v in reversed(order):
    hi = N - 2 if v in delayed_read else N - 1
    latest[v] = min([hi] + [latest[w] - 1 for w in succ[v]])
slot = {}
for v in order:
    lo = max([slot[s] + 1 for s, d in nodes[v]["operands"] if s in nodes and d == 0] or [0])
    assert lo <= latest[v], f"infeasible slot for {v}"
    slot[v] = lo if policy == "asap" else rng.randint(lo, latest[v])
    nodes[v]["slot"] = slot[v]
outputs = list(kern["outputs"].values())
out_names = list(kern["outputs"].keys())

OPS = ["add", "sub", "mul", "mulc", "addc"]
units = {}
for s in range(N):
    for t in OPS:
        here = [v for v in order if slot[v] == s and nodes[v]["op"] == t]
        rng.shuffle(here)
        for j, v in enumerate(here):
            nodes[v]["unit"] = f"u_{t}{j}"
            units.setdefault(f"u_{t}{j}", t)

delayed_of = {}
for v, nd in nodes.items():
    for s, d in nd["operands"]:
        if d > 0:
            delayed_of[s] = max(delayed_of.get(s, 0), d)


def end_of(v):
    e = [slot[w] for w in succ[v]]
    if v in delayed_of:
        e.append(N - 1)
    if v in outputs:
        e.append(N)
    return max(e) if e else slot[v]


alloc = sorted(nodes, key=lambda v: (slot[v], int(v[1:])))
active, free, nregs = [], [], 0
for v in alloc:
    while active and active[0][0] <= slot[v]:
        heapq.heappush(free, heapq.heappop(active)[1])
    i = heapq.heappop(free) if free else nregs
    if i == nregs:
        nregs += 1
    heapq.heappush(active, (end_of(v), i))
    nodes[v]["reg"] = f"r{i}"

cw = max(1, (N - 1).bit_length())
L = ["module fold_top(" + ", ".join(["input clk", "input rst"] + [f"input [{W-1}:0] {x}" for x in inputs] +
                                    [f"output [{W-1}:0] {o}" for o in out_names]) + ");"]
L.append(f"  reg [{cw-1}:0] cyc;")
L.append(f"  always @(posedge clk) if (rst) cyc <= 0; else cyc <= (cyc == {N-1}) ? 0 : cyc + 1;")
for i in range(nregs):
    L.append(f"  reg [{W-1}:0] r{i};")
dregs = [(s, d) for s, dm in sorted(delayed_of.items()) for d in range(1, dm + 1)]
for s, d in dregs:
    L.append(f"  reg [{W-1}:0] d_{s}_{d};")


def operand(src, d):
    if d > 0:
        return f"d_{src}_{d}"
    return src if src in inputs else nodes[src]["reg"]


bound = {}
for v in sorted(nodes, key=lambda v: slot[v]):
    bound.setdefault(nodes[v]["unit"], []).append(v)
for u, t in sorted(units.items()):
    L.append(f"  reg [{W-1}:0] {u}_a, {u}_b;")
    L.append("  always @* begin")
    L.append(f"    {u}_a = 0; {u}_b = 0;")
    for v in bound[u]:
        nd = nodes[v]
        a = operand(*nd["operands"][0])
        b = f"{W}'d{nd['const']}" if t in ("mulc", "addc") else operand(*nd["operands"][1])
        L.append(f"    if (cyc == {slot[v]}) begin {u}_a = {a}; {u}_b = {b}; end")
    L.append("  end")
    L.append(f"  wire [{W-1}:0] {u}_y = {u}_a {dict(add='+', sub='-', mul='*', mulc='*', addc='+')[t]} {u}_b;")
L.append("  always @(posedge clk) begin")
L.append("    if (rst) begin")
for i in range(nregs):
    L.append(f"      r{i} <= 0;")
for s, d in dregs:
    L.append(f"      d_{s}_{d} <= 0;")
L.append("    end else begin")
for v in alloc:
    L.append(f"      if (cyc == {slot[v]}) {nodes[v]['reg']} <= {nodes[v]['unit']}_y;")
if dregs:
    L.append(f"      if (cyc == {N-1}) begin")
    for s, d in dregs:
        L.append(f"        d_{s}_{d} <= " + (operand(s, 0) if d == 1 else f"d_{s}_{d-1}") + ";")
    L.append("      end")
L.append("    end")
L.append("  end")
for o, v in zip(out_names, outputs):
    L.append(f"  assign {o} = {nodes[v]['reg']};")
L.append("endmodule")
open(f"{out}/fold_top.v", "w").write("\n".join(L) + "\n")
truth = {"kernel": kname, "nspec": nspec, "policy": policy, "seed": seed, "N": N, "N_min": n_min, "control": "none",
         "width": W, "nodes": nodes, "outputs": outputs, "output_names": out_names, "inputs": inputs, "registers": nregs,
         "delay_registers": [[s, d] for s, d in dregs], "units": units, "chain_edge": None}
json.dump(truth, open(f"{out}/truth.json", "w"), indent=1)
print(json.dumps({"kernel": kname, "N": N, "N_min": n_min, "policy": policy, "nodes": len(nodes), "registers": nregs,
                  "delay_registers": len(dregs), "units": len(units)}))
