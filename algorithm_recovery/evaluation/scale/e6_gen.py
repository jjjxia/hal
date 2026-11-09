#!/usr/bin/env python3
"""E6 generator: the E5.C model (random single-rate DFG + registered fixed-binding folding) at scale.

usage: e6_gen.py SEED K N OUT_DIR
Same node, operand, binding, register and copy-register rules as e5c/e5c_gen.py (control none), with data structures
that scale to K = 100,000 nodes: operands are drawn while slots are processed in order (same-sample operands from
earlier slots, preferring unconsumed nodes), outputs are all nodes without a reader, and constant-from-reset nodes are
kept (E6 equations start data registers from unknown values). Writes fold_top.v and truth.json.
"""
import heapq, json, os, random, sys

W = 8
OPS = ["add", "sub", "xor", "and", "mulc"]
seed, K, N, out = int(sys.argv[1]), int(sys.argv[2]), int(sys.argv[3]), sys.argv[4]
os.makedirs(out, exist_ok=True)
rng = random.Random(seed * 1000003 + K * 101 + N)

names = [f"n{i}" for i in range(K)]
nodes = {}
for v in names:
    nd = {"op": rng.choice(OPS), "slot": rng.randrange(N)}
    if nd["op"] == "mulc":
        nd["const"] = rng.choice([3, 5, 7, 9, 11, 13, 15])
    nodes[v] = nd
by_slot = [[] for _ in range(N)]
for v in names:
    by_slot[nodes[v]["slot"]].append(v)
delayed_cands = [v for v in names if nodes[v]["slot"] <= N - 2]

consumed = set()
pool = []                  # nodes of earlier slots
fresh, fresh_pos = [], {}  # unconsumed nodes of earlier slots (swap-remove list)


def consume(u):
    consumed.add(u)
    i = fresh_pos.pop(u, None)
    if i is not None:
        last = fresh.pop()
        if i < len(fresh):
            fresh[i] = last
            fresh_pos[last] = i


for s in range(N):
    here = list(by_slot[s])
    rng.shuffle(here)
    for v in here:
        nd = nodes[v]
        ops = []
        for _ in range(1 if nd["op"] == "mulc" else 2):
            r = rng.random()
            if delayed_cands and r < 0.25:
                ops.append([rng.choice(delayed_cands), rng.choice([1, 1, 2])])
            elif pool and r < 0.85:
                ops.append([rng.choice(fresh) if fresh else rng.choice(pool), 0])
            else:
                ops.append([rng.choice(["x0", "x1"]), 0])
            if ops[-1][0] in nodes:
                consume(ops[-1][0])
        nd["operands"] = ops
    for v in by_slot[s]:
        pool.append(v)
        if v not in consumed:
            fresh_pos[v] = len(fresh)
            fresh.append(v)
outputs = [v for v in names if v not in consumed]
assert any(o[1] > 0 for nd in nodes.values() for o in nd["operands"]), "no delayed edge"
assert any(o[1] == 0 and o[0] in nodes for nd in nodes.values() for o in nd["operands"]), "no same-sample edge"

# units: per op type as many as the busiest slot needs; random binding inside a slot
units = {}
for s in range(N):
    for t in OPS:
        here = [v for v in by_slot[s] if nodes[v]["op"] == t]
        rng.shuffle(here)
        for j, v in enumerate(here):
            nodes[v]["unit"] = f"u_{t}{j}"
            units.setdefault(f"u_{t}{j}", t)

# lifetimes and left-edge allocation (lowest free register; a register is free once its last read slot <= s)
delayed_of, last_read = {}, {}
for v, nd in nodes.items():
    for src, d in nd["operands"]:
        if src in nodes:
            if d > 0:
                delayed_of[src] = max(delayed_of.get(src, 0), d)
            else:
                last_read[src] = max(last_read.get(src, -1), nd["slot"])
out_set = set(outputs)


def end_of(v):
    e = [last_read[v]] if v in last_read else []
    if v in delayed_of:
        e.append(N - 1)
    if v in out_set:
        e.append(N)
    return max(e) if e else nodes[v]["slot"]


order = sorted(names, key=lambda v: (nodes[v]["slot"], v))   # same order rule as e5c_gen.py
active, free, nregs = [], [], 0
for v in order:
    s = nodes[v]["slot"]
    while active and active[0][0] <= s:
        heapq.heappush(free, heapq.heappop(active)[1])
    if free:
        i = heapq.heappop(free)
    else:
        i, nregs = nregs, nregs + 1
    heapq.heappush(active, (end_of(v), i))
    nodes[v]["reg"] = f"r{i}"

cw = max(1, (N - 1).bit_length())
L = []
ports = ["input clk", "input rst", f"input [{W-1}:0] x0", f"input [{W-1}:0] x1"] + \
        [f"output [{W-1}:0] y{i}" for i in range(len(outputs))]
L.append("module fold_top(" + ", ".join(ports) + ");")
L.append(f"  reg [{cw-1}:0] cyc;")
L.append(f"  always @(posedge clk) if (rst) cyc <= 0; else cyc <= (cyc == {N-1}) ? 0 : cyc + 1;")
for i in range(nregs):
    L.append(f"  reg [{W-1}:0] r{i};")
dregs = [(v, d) for v, dmax in sorted(delayed_of.items()) for d in range(1, dmax + 1)]
for v, d in dregs:
    L.append(f"  reg [{W-1}:0] d_{v}_{d};")


def operand(src, d):
    if src in ("x0", "x1"):
        return src
    return nodes[src]["reg"] if d == 0 else f"d_{src}_{d}"


bound = {}
for v in sorted(names, key=lambda v: nodes[v]["slot"]):
    bound.setdefault(nodes[v]["unit"], []).append(v)
for u, t in sorted(units.items()):
    L.append(f"  reg [{W-1}:0] {u}_a, {u}_b;")
    L.append("  always @* begin")
    L.append(f"    {u}_a = 0; {u}_b = 0;")
    for v in bound.get(u, []):
        nd = nodes[v]
        a = operand(*nd["operands"][0])
        b = str(nd["const"]) if t == "mulc" else operand(*nd["operands"][1])
        L.append(f"    if (cyc == {nd['slot']}) begin {u}_a = {a}; {u}_b = {b}; end")
    L.append("  end")
    expr = {"add": "+", "sub": "-", "xor": "^", "and": "&", "mulc": "*"}[t]
    L.append(f"  wire [{W-1}:0] {u}_y = {u}_a {expr} {u}_b;")
L.append("  always @(posedge clk) begin")
L.append("    if (rst) begin")
for i in range(nregs):
    L.append(f"      r{i} <= 0;")
for v, d in dregs:
    L.append(f"      d_{v}_{d} <= 0;")
L.append("    end else begin")
for v in order:
    L.append(f"      if (cyc == {nodes[v]['slot']}) {nodes[v]['reg']} <= {nodes[v]['unit']}_y;")
if dregs:
    L.append(f"      if (cyc == {N-1}) begin")
    for v, d in dregs:
        L.append(f"        d_{v}_{d} <= " + (nodes[v]["reg"] if d == 1 else f"d_{v}_{d-1}") + ";")
    L.append("      end")
L.append("    end")
L.append("  end")
for i, o in enumerate(outputs):
    L.append(f"  assign y{i} = {nodes[o]['reg']};")
L.append("endmodule")
open(f"{out}/fold_top.v", "w").write("\n".join(L) + "\n")
truth = {"seed": seed, "K": K, "N": N, "control": "none", "width": W, "nodes": nodes, "outputs": outputs,
         "inputs": ["x0", "x1"], "registers": nregs, "delay_registers": [[v, d] for v, d in dregs], "units": units}
json.dump(truth, open(f"{out}/truth.json", "w"))
print(json.dumps({"seed": seed, "K": K, "N": N, "nodes": K, "outputs": len(outputs), "registers": nregs,
                  "delay_registers": len(dregs), "units": len(units),
                  "edges": sum(len(nd["operands"]) for nd in nodes.values()),
                  "delayed_edges": sum(1 for nd in nodes.values() for o in nd["operands"] if o[1] > 0)}))
