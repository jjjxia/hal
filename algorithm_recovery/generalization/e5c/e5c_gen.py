#!/usr/bin/env python3
"""E5.C generator: random single-rate DFG + registered fixed-binding folding (Parhi style) -> Verilog + ground truth.

usage: e5c_gen.py SEED N OUT_DIR [--control none|chain|rotate]
  N        folding factor = slots per sample = cycles per transaction
  chain    out of class: one same-sample edge u->v is chained combinationally (v runs in u's slot and reads u's unit
           output, not its register); predicted: v's recovered event contains u's operation
  rotate   out of class: the register binding alternates between two allocations on even/odd samples;
           predicted: certified period 2N, recovered graph = 2-unfolding of the DFG (Theorem 6)
Ground truth (truth.json): nodes {id: {op, const, slot, operands: [[src, delay]], unit, reg[, reg_odd]}}, inputs, outputs,
delay registers, N. src is "x0"/"x1" or a node id. Every node result is written to its bound register at the end of its
slot; a delayed operand (delay d >= 1) is read from the copy register d_<v>_<d>, written at slot N-1 (a shift chain).
"""
import json, os, random, sys

W = 8
OPS = ["add", "sub", "xor", "and", "mulc"]
seed, N, out = int(sys.argv[1]), int(sys.argv[2]), sys.argv[3]
control = sys.argv[sys.argv.index("--control") + 1] if "--control" in sys.argv else "none"
os.makedirs(out, exist_ok=True)


def generate(sub):
    rng = random.Random(seed * 1000 + N * 10 + sub)
    k = rng.randint(6, 16)
    nodes = {}
    for i in range(k):
        nodes[f"n{i}"] = {"op": rng.choice(OPS), "slot": rng.randrange(N)}
        if nodes[f"n{i}"]["op"] == "mulc":
            nodes[f"n{i}"]["const"] = rng.choice([3, 5, 7, 9, 11, 13, 15])
    consumed = set()
    names = list(nodes)
    rng.shuffle(names)
    for v in names:
        nd = nodes[v]
        arity = 1 if nd["op"] == "mulc" else 2
        same = [u for u in nodes if nodes[u]["slot"] < nd["slot"]]
        delayed = [u for u in nodes if nodes[u]["slot"] <= N - 2]
        ops = []
        for _ in range(arity):
            r = rng.random()
            fresh_same = [u for u in same if u not in consumed]
            if delayed and r < 0.25:
                ops.append([rng.choice(delayed), rng.choice([1, 1, 2])])
            elif same and r < 0.85:
                u = rng.choice(fresh_same or same)
                ops.append([u, 0])
            else:
                ops.append([rng.choice(["x0", "x1"]), 0])
            if ops[-1][0] in nodes:
                consumed.add(ops[-1][0])
        nd["operands"] = ops
    outputs = [v for v in nodes if v not in consumed]
    return nodes, outputs, rng


def constant_nodes(nodes):
    """Nodes whose value never changes from the zero reset state under random inputs (64 samples)."""
    order, done = [], set()
    for _ in range(len(nodes) + 1):
        for v, nd in sorted(nodes.items()):
            if v not in done and all(not (s in nodes and dl == 0) or s in done for s, dl in nd["operands"]):
                order.append(v); done.add(v)
    if len(order) != len(nodes):
        return set(nodes)
    r, hist, seen = random.Random(99), [], {v: set() for v in nodes}
    for n in range(64):
        x = {"x0": r.getrandbits(W), "x1": r.getrandbits(W)}
        val = {}
        for v in order:
            nd = nodes[v]
            get = lambda s, dl: x[s] if s in x else (val[s] if dl == 0 else (hist[n - dl][s] if n >= dl else 0))
            a = get(*nd["operands"][0])
            b = None if nd["op"] == "mulc" else get(*nd["operands"][1])
            val[v] = {"mulc": lambda: a * nd.get("const", 1), "add": lambda: a + b, "sub": lambda: a - b,
                      "xor": lambda: a ^ b, "and": lambda: a & b}[nd["op"]]() & ((1 << W) - 1)
            seen[v].add(val[v])
        hist.append(val)
    return {v for v in nodes if len(seen[v]) < 2}


for sub in range(1000):
    nodes, outputs, rng = generate(sub)
    if 1 <= len(outputs) <= 4 and any(o[1] > 0 for n in nodes.values() for o in n["operands"]) \
            and any(o[1] == 0 and o[0] in nodes for n in nodes.values() for o in n["operands"]) \
            and not constant_nodes(nodes):
        break
else:
    raise SystemExit("no admissible graph")

chain_edge = None
if control == "chain":
    cands = [(u, v) for v, nd in nodes.items() for u, d in nd["operands"]
             if d == 0 and u in nodes and nodes[u]["slot"] + 1 == nd["slot"]]
    if not cands:
        raise SystemExit("no chainable edge")
    u, v = rng.choice(sorted(cands))
    chain_edge = [u, v]
    nodes[v]["slot"] = nodes[u]["slot"]   # v now runs in u's slot, reading u's unit output
    # v's other same-sample operands must still come from earlier slots
    for src, d in nodes[v]["operands"]:
        assert not (d == 0 and src in nodes and src != u and nodes[src]["slot"] >= nodes[v]["slot"]), "infeasible chain"

# units: per op type, as many as the busiest slot needs; random binding inside a slot
units = {}
for s in range(N):
    for t in OPS:
        here = [v for v in nodes if nodes[v]["slot"] == s and nodes[v]["op"] == t]
        rng.shuffle(here)
        for j, v in enumerate(here):
            nodes[v]["unit"] = f"u_{t}{j}"
            units.setdefault(f"u_{t}{j}", t)

# lifetimes and left-edge register allocation (a value lives from its write at the end of its slot to its last read)
delayed_of = {}
for v, nd in nodes.items():
    for src, d in nd["operands"]:
        if d > 0:
            delayed_of[src] = max(delayed_of.get(src, 0), d)


def end_of(v):
    e = [nodes[w]["slot"] for w, nd in nodes.items() for src, d in nd["operands"]
         if src == v and d == 0 and not (chain_edge and [v, w] == chain_edge)]
    if v in delayed_of:
        e.append(N - 1)
    if v in outputs:
        e.append(N)
    return max(e) if e else nodes[v]["slot"]


def allocate(order):
    regs = []                      # per register: last end
    for v in order:
        s = nodes[v]["slot"]
        free = [i for i, last in enumerate(regs) if last <= s]
        i = free[0] if free else len(regs)
        if i == len(regs):
            regs.append(0)
        regs[i] = end_of(v)
        yield v, i


order = sorted(nodes, key=lambda v: (nodes[v]["slot"], v))
for v, i in allocate(order):
    nodes[v]["reg"] = f"r{i}"
nregs = 1 + max(int(nd["reg"][1:]) for nd in nodes.values())
if control == "rotate":
    # odd samples: the same allocation applied to a rotated register numbering
    for v in nodes:
        nodes[v]["reg_odd"] = f"r{(int(nodes[v]['reg'][1:]) + 1) % nregs}"
    if nregs < 2 or all(nd["reg_odd"] == nd["reg"] for nd in nodes.values()):
        raise SystemExit("rotation needs two registers")

cw = max(1, (N - 1).bit_length())
L = []
ports = ["input clk", "input rst", f"input [{W-1}:0] x0", f"input [{W-1}:0] x1"] + \
        [f"output [{W-1}:0] y{i}" for i in range(len(outputs))]
L.append("module fold_top(" + ", ".join(ports) + ");")
L.append(f"  reg [{cw-1}:0] cyc;")
L.append(f"  always @(posedge clk) if (rst) cyc <= 0; else cyc <= (cyc == {N-1}) ? 0 : cyc + 1;")
if control == "rotate":
    L.append("  reg odd;")
    L.append(f"  always @(posedge clk) if (rst) odd <= 0; else if (cyc == {N-1}) odd <= ~odd;")
for i in range(nregs):
    L.append(f"  reg [{W-1}:0] r{i};")
dregs = []
for v, dmax in sorted(delayed_of.items()):
    for d in range(1, dmax + 1):
        dregs.append((v, d))
        L.append(f"  reg [{W-1}:0] d_{v}_{d};")


def reg_of(v, parity):
    return nodes[v]["reg_odd"] if (control == "rotate" and parity) else nodes[v]["reg"]


def operand(v, src, d, parity):
    if src in ("x0", "x1"):
        return src
    if chain_edge and [src, v] == chain_edge:
        return f"{nodes[src]['unit']}_y"
    if d == 0:
        return reg_of(src, parity)
    return f"d_{src}_{d}"


parities = [0, 1] if control == "rotate" else [0]
for u, t in sorted(units.items()):
    arity = 1 if t == "mulc" else 2
    L.append(f"  reg [{W-1}:0] {u}_a, {u}_b;")
    L.append("  always @* begin")
    L.append(f"    {u}_a = 0; {u}_b = 0;")
    for v in sorted(nodes, key=lambda v: nodes[v]["slot"]):
        nd = nodes[v]
        if nd.get("unit") != u:
            continue
        for par in parities:
            cond = f"cyc == {nd['slot']}" + (f" && odd == {par}" if control == "rotate" else "")
            a = operand(v, *nd["operands"][0], par)
            b = str(nd["const"]) if t == "mulc" else operand(v, *nd["operands"][1], par)
            L.append(f"    if ({cond}) begin {u}_a = {a}; {u}_b = {b}; end")
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
    nd = nodes[v]
    for par in parities:
        cond = f"cyc == {nd['slot']}" + (f" && odd == {par}" if control == "rotate" else "")
        L.append(f"      if ({cond}) {reg_of(v, par)} <= {nd['unit']}_y;")
for par in parities:
    cond = f"cyc == {N-1}" + (f" && odd == {par}" if control == "rotate" else "")
    moves = [f"d_{v}_{d} <= " + (reg_of(v, par) if d == 1 else f"d_{v}_{d-1}") + ";" for v, d in dregs]
    if moves:
        L.append(f"      if ({cond}) begin " + " ".join(moves) + " end")
L.append("    end")
L.append("  end")
for i, o in enumerate(outputs):
    if control == "rotate":   # read at cycle N, after the parity flip at the end of slot N-1
        L.append(f"  assign y{i} = odd ? {nodes[o]['reg']} : {nodes[o]['reg_odd']};")
    else:
        L.append(f"  assign y{i} = {nodes[o]['reg']};")
L.append("endmodule")
open(f"{out}/fold_top.v", "w").write("\n".join(L) + "\n")
truth = {"seed": seed, "N": N, "control": control, "width": W, "nodes": nodes, "outputs": outputs,
         "inputs": ["x0", "x1"], "registers": nregs, "delay_registers": [[v, d] for v, d in dregs],
         "units": units, "chain_edge": chain_edge}
json.dump(truth, open(f"{out}/truth.json", "w"), indent=1)
print(json.dumps({"seed": seed, "N": N, "control": control, "nodes": len(nodes), "outputs": len(outputs),
                  "registers": nregs, "delay_registers": len(dregs), "units": len(units)}))
