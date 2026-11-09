#!/usr/bin/env python3
"""E8 amendment 7 diagnosis (generated from e8_score.py; scoring identical): for every node whose function is not
proved, the z3 verdict of its check at expansion depth 0 and fully expanded to the inputs (60 s each), and the
number of 500 random input assignments on which the fully expanded expressions differ. Extra argument: DIAG_OUT.json.
E8.2 scorer: e5c/e5c_score.py generalised to the E8 kernels (named inputs, input delays through copy registers,
16-bit words, ops add/sub/mul/mulc/addc). Otherwise identical. Original description:
E5.C scorer: is the recovered event graph the folded DFG, up to renaming (Theorem 7)?

usage: e5c_score.py CASE_DIR TRUTH_JSON SYNTH_JSON GRAPH_JSON OUT_JSON
Mapping (evaluation side only): recovered words -> RTL registers through the synthesized netlist's names (anonymisation
keeps physical bit ids); data pads -> x0/x1 through the evaluation contract. A recovered event (word, phase) is placed at
the transaction-relative cycle floor(((phase - shift) mod P) / 2), shift = (first_event - origin) mod P, as in c3/decompile.py.
Per DFG node v (per parity copy for the rotate control):
  found     an event of v's register at v's slot;
  edges     its operands, after collapsing the copy registers d_<u>_<k> (delays add up), equal v's operands as a set of
            (producer, sample delay) -- producers are DFG nodes or the inputs x0/x1;
  function  its specialised equation, with every operand replaced by a symbol of its (producer, delay) value, is proved by z3
            equal to v's operation.
Extra events on result registers that match no DFG node are spurious. A design is exact iff every node is found with
equal edges and function and nothing is spurious. Controls: chain -> the predicted merged operand set and composed
function of the chained node; rotate -> the 2-unfolding predicted by Theorem 6 (copy j of v at cycle j*N + slot)."""
import collections, json, re, sys
import z3

case_dir, truth_path, synth_path, graph_path, out_path = sys.argv[1:6]
T = json.load(open(truth_path))
N, Wd, nodes, control = T["N"], T["width"], T["nodes"], T["control"]
INPUTS = set(T["inputs"])
contract = json.load(open(f"{case_dir}/input/evaluation_contract.json"))
G = json.load(open(graph_path))
result = {"case": contract["case"], "control": control, "N": N, "dfg_nodes": len(nodes), "graph_available": G.get("available", False)}
if not G.get("available"):
    result["outcome"] = "no graph"
    json.dump(result, open(out_path, "w"), indent=1); print(json.dumps(result)); sys.exit(0)
P = G["period"]
result["graph_period_events"] = P
J = 2 if control == "rotate" else 1
result["expected_period_events"] = 2 * N * J

# ---- names: physical bit -> (register, bit) ---------------------------------------------------------------------------
synth = json.load(open(synth_path))["modules"]["fold_top"]
REG = re.compile(r"^(r\d+|d_[A-Za-z][A-Za-z0-9]*_\d+|cyc|odd)$")
bit_reg = {}
for name, nn in synth["netnames"].items():
    if REG.match(name):
        for i, b in enumerate(nn["bits"]):
            if isinstance(b, int):
                bit_reg[b] = (name, i)
pad_port = {p["physical_bit"]: (p["name"], p["offset"]) for p in contract["port_bits"] if p["direction"] == "input"}

word_bits, grouping = {}, collections.Counter()
for wname, w in G["words"].items():
    word_bits[wname] = [bit_reg.get(int(b[1:])) for b in w["bits"]]     # word bit j -> (register, bit) or None
reg_home = collections.defaultdict(set)
for wname, owners in word_bits.items():
    for o in owners:
        if o:
            reg_home[o[0]].add(wname)
for r in sorted(reg_home):
    if (r.startswith("r") or r.startswith("d_")):
        homes = reg_home[r]
        bits = sorted(o[1] for w in homes for o in word_bits[w] if o and o[0] == r)
        if len(homes) != 1 or bits != list(range(Wd)):
            grouping["register_split_across_words"] += 1
for wname, owners in word_bits.items():
    if any(o is None for o in owners):
        grouping["word_with_unnamed_bits"] += 1
merged = sum(1 for wname, owners in word_bits.items() if len({o[0] for o in owners if o and o[0] != "cyc" and o[0] != "odd"}) > 1)
result["grouping_issues"] = dict(grouping)
result["words_merging_registers"] = merged

shift = (contract["first_event"] - G["origin"]) % P
cycle_of = lambda phase: ((phase - shift) % P) // 2
rho = lambda phase: -1 if phase < shift else 0     # graph -> transaction sample coordinates (Theorem 5), as c3/decompile.py
tdelay = lambda ev_phase, src: src["delay"] + rho(ev_phase) - rho(src["phase"])
events = {(n["name"], n["phase"]): n for n in G["nodes"]}
nphases = len(next(iter(G["words"].values()))["certified_known_lsb_first"])


# chain control: if the chained producer u has no other reader and is not an output, its write is dead (v overwrites the
# same register in the same slot), so u has no event of its own -- predicted merged into v
DEAD = set()
if control == "chain" and T["chain_edge"]:
    _u, _v = T["chain_edge"]
    _other = [w for w, nd in nodes.items() for src, d in nd["operands"] if src == _u and not (w == _v and d == 0)]
    _clash = [w for w in nodes if w != _u and nodes[w]["reg"] == nodes[_u]["reg"] and nodes[w]["slot"] == nodes[_u]["slot"]]
    if not _other and _u not in T["outputs"] and _clash:
        DEAD.add(_u)
result["dead_chained_producer"] = sorted(DEAD)


def reg_of(v, j):
    return nodes[v]["reg_odd"] if (control == "rotate" and j == 1) else nodes[v]["reg"]


def owner_of_result(r, c):
    j, s = divmod(c, N)
    hits = [v for v in nodes if reg_of(v, j) == r and nodes[v]["slot"] == s and v not in DEAD]
    return (hits[0], j) if len(hits) == 1 else None


def parse(smt2):
    return z3.parse_smt2_string(smt2)[0].arg(1)


def const_bv(bits_lsb, w):
    return z3.BitVecVal(int(bits_lsb[::-1], 2), w)


def free_vars(expr):
    seen, out, stack = set(), [], [expr]
    while stack:
        x = stack.pop()
        if x.get_id() in seen:
            continue
        seen.add(x.get_id())
        if z3.is_const(x) and x.decl().kind() == z3.Z3_OP_UNINTERPRETED:
            out.append(x)
        stack.extend(x.children())
    return out


def specialise(expr, phase):
    subs = []
    for e in free_vars(expr):
        kind, _, base = str(e).partition(":")
        table = G["words"] if kind == "word" else G["inputs"] if kind == "input" else None
        if table and base in table:
            known = table[base]["certified_known_lsb_first"][phase % nphases]
            w = e.size()
            if "x" not in known:
                subs.append((e, const_bv(known, w)))
            elif known.strip("x"):
                mask = const_bv("".join("1" if c != "x" else "0" for c in known), w)
                val = const_bv("".join(c if c != "x" else "0" for c in known), w)
                subs.append((e, (e & ~mask) | val))
    return z3.simplify(z3.substitute(expr, *subs)) if subs else z3.simplify(expr)


def val_sym(key):
    src, copy, d = key
    return z3.BitVec(f"V|{src}|{copy}|{d}", Wd)


UNRESOLVED = []


MEMO_RV, MEMO_SV = {}, {}


def register_value(wname, phase, delay, r, depth=0):
    key = (wname, phase, delay, r)
    if key not in MEMO_RV:
        MEMO_RV[key] = _register_value(wname, phase, delay, r, depth)
    return MEMO_RV[key]


def _register_value(wname, phase, delay, r, depth=0):
    """Value of register r as read through a source record (word, phase, delay): a value symbol of the DFG node copy
    that wrote it, or, for a copy register, the value its copy event moved (delays add up)."""
    if r.startswith("r"):
        o = owner_of_result(r, cycle_of(phase))
        if o is None:
            UNRESOLVED.append((wname, phase, r)); return z3.BitVec(f"U|{wname}|{phase}|{r}", Wd)
        return val_sym((o[0], o[1], delay))
    if r.startswith("d_") and depth < 16:
        ev = events.get((wname, phase))
        if ev is None:
            UNRESOLVED.append((wname, phase, r)); return z3.BitVec(f"U|{wname}|{phase}|{r}", Wd)
        return slice_value(ev, r, delay, depth + 1, partial=True)   # amendment 4: the read bit is in this word
    return z3.BitVec(f"C|{r}|{phase}", Wd)


def slice_value(ev, r, outer_delay=0, depth=0, partial=False):
    key = (ev["name"], ev["phase"], r, outer_delay, partial)
    if key not in MEMO_SV:
        MEMO_SV[key] = _slice_value(ev, r, outer_delay, depth, partial)
    return MEMO_SV[key]


SPEC = {}


def _slice_value(ev, r, outer_delay=0, depth=0, partial=False):
    """Register r's new value written by event ev, with every source replaced by DFG-level value symbols."""
    k = (ev["name"], ev["phase"])
    if k not in SPEC:
        SPEC[k] = specialise(parse(ev["smt2"]), ev["phase"])
    e = SPEC[k]
    by_symbol = {s["symbol"]: s for s in ev["sources"]}
    subs = []
    for var in free_vars(e):
        s = by_symbol.get(str(var))
        if s is None:
            continue
        if s["kind"] == "input":
            port, off = pad_port[int(s["name"][3:])]
            subs.append((var, z3.Extract(off, off, val_sym((port, None, outer_delay))) if port in INPUTS else z3.BitVecVal(0, 1)))
        else:
            owners = word_bits[s["name"]]
            bits = []
            for o in owners:
                if o is None or o[0] in ("cyc", "odd"):
                    bits.append(z3.BitVec(f"C|{s['name']}|{len(bits)}", 1))
                else:
                    rv = register_value(s["name"], s["phase"], outer_delay + tdelay(ev["phase"], s), o[0], depth)
                    bits.append(z3.Extract(o[1], o[1], rv))
            subs.append((var, z3.Concat(*reversed(bits)) if len(bits) > 1 else bits[0]))
    e = z3.substitute(e, *subs) if subs else e
    out = [None] * Wd
    for j, o in enumerate(word_bits[ev["name"]]):
        if o and o[0] == r:
            out[o[1]] = z3.Extract(j, j, e)
    if any(b is None for b in out):
        if not partial:
            return None
        # E8 amendment 4: a copy register split across words, read through one of its words: the bits held by this
        # word are exact, the others are unconstrained (the reader extracts only bits of this word)
        out = [b if b is not None else z3.BitVec(f"C|split|{ev['name']}|{ev['phase']}|{r}|{i}", 1) for i, b in enumerate(out)]
    return z3.simplify(z3.Concat(*reversed(out)))


def expected_key(src, d, j):
    if src in INPUTS:
        return (src, None, d)
    k = j - d
    return (src, k % J, -(k // J)) if J > 1 else (src, 0, d)


def truth_expr(v, j, chained=False):
    nd = nodes[v]
    def opnd(src, d):
        if chained and T["chain_edge"] and [src, v] == T["chain_edge"]:
            return truth_expr(src, j)
        return val_sym(expected_key(src, d, j))
    a = opnd(*nd["operands"][0])
    if nd["op"] == "mulc":
        return a * nd["const"]
    if nd["op"] == "addc":
        return a + nd["const"]
    b = opnd(*nd["operands"][1])
    return {"add": a + b, "sub": a - b, "xor": a ^ b, "and": a & b, "mul": a * b}[nd["op"]]


def equal(a, b):
    s = z3.Solver(); s.set("timeout", 60000); s.add(a != b)
    return s.check() == z3.unsat


# E8 amendment 2: compare on the DFG-reachable domain. Value symbols V|u|copy|d of DFG nodes are replaced by u's own
# definition (operands at delay d + their delay), recursively up to EXPAND_DEPTH levels; inputs stay free. Needed when a
# certified constant bit (e.g. bit 0 of 10*w) lets the certificate specialise a consumer's equation: equal on every
# reachable value, different on unreachable ones. E5.C's odd constants never produce such bits.
EXPAND_DEPTH = 4
_EXP = {}


def expand_sym(u, j, d, depth):
    key = (u, j, d, depth)
    if key not in _EXP:
        nd = nodes[u]
        def opnd(src, dd):
            if src in INPUTS:
                return val_sym((src, None, d + dd))
            if depth <= 0:
                return val_sym((src, 0, d + dd))
            return expand_sym(src, j, d + dd, depth - 1)
        a = opnd(*nd["operands"][0])
        if nd["op"] in ("mulc", "addc"):
            e = a * nd["const"] if nd["op"] == "mulc" else a + nd["const"]
        else:
            b = opnd(*nd["operands"][1])
            e = {"add": a + b, "sub": a - b, "xor": a ^ b, "and": a & b, "mul": a * b}[nd["op"]]
        _EXP[key] = e
    return _EXP[key]


def expand(expr, depth):
    subs = []
    for x in free_vars(expr):
        parts = str(x).split("|")
        if parts[0] == "V" and parts[1] in nodes:
            subs.append((x, expand_sym(parts[1], 0 if parts[2] == "None" else int(parts[2]), int(parts[3]), depth)))
    return z3.substitute(expr, *subs) if subs else expr


DIAG = []
FULL_PROOFS = []     # E8 amendment 5: nodes proved only by the full expansion to the inputs


def equal_reachable(a, b):
    """Iterative deepening: one level of producer definitions usually suffices (constant bits of a product).
    Amendment 5: then the full expansion to the inputs (depth = number of DFG nodes), which is the reachable-domain
    comparison itself (depths 0..3 only bound its cost); same 60 s per check."""
    for depth in range(0, EXPAND_DEPTH):
        if equal(expand(a, depth), expand(b, depth)):
            return True
    if equal(expand(a, len(nodes)), expand(b, len(nodes))):
        FULL_PROOFS.append(1)
        return True
    return False


def depends(expr, key):
    """Does expr change when the value symbol `key` alone changes (z3)?"""
    x = [v for v in free_vars(expr) if str(v) == key][0]
    y = z3.BitVec(key + "|alt", x.size())
    sv = z3.Solver(); sv.set("timeout", 60000)
    sv.add(expr != z3.substitute(expr, (x, y)))
    return sv.check() != z3.unsat


def value_keys(expr):
    return {str(x) for x in free_vars(z3.simplify(expr)) if str(x).startswith(("V|", "U|", "C|"))}


# ---- score ------------------------------------------------------------------------------------------------------------
reg_events = collections.defaultdict(list)          # (register, cycle) -> word events writing it
for (wname, ph), ev in events.items():
    for r in {o[0] for o in word_bits[wname] if o and (o[0].startswith("r") or o[0].startswith("d_"))}:
        reg_events[(r, cycle_of(ph))].append(ev)
per_node, matched = [], set()
for j in range(J):
    for v in sorted(nodes):
        if v in DEAD:
            continue
        r, c = reg_of(v, j), j * N + nodes[v]["slot"]
        evs = reg_events.get((r, c), [])
        rec = {"node": v, "copy": j, "found": len(evs) == 1}
        if len(evs) != 1:
            rec.update(edges=False, function=False, note=f"{len(evs)} events")
            per_node.append(rec); continue
        matched.add((r, c))
        chained = bool(control == "chain" and T["chain_edge"] and T["chain_edge"][1] == v)
        rx = slice_value(evs[0], r)
        tx = truth_expr(v, j, chained=chained)
        if rx is None:
            rec.update(edges=False, function=False, note="slice incomplete"); per_node.append(rec); continue
        rec["function_strict"] = equal(rx, tx)
        rec["function"] = rec["function_strict"] or equal_reachable(rx, tx)
        if not rec["function"]:
            import random as _r
            _s = z3.Solver(); _s.set("timeout", 60000); _s.add(rx != tx); _v0 = str(_s.check())
            ea, eb = expand(rx, len(nodes)), expand(tx, len(nodes))
            _s2 = z3.Solver(); _s2.set("timeout", 60000); _s2.add(ea != eb); _vf = str(_s2.check())
            fv = list({str(x): x for x in list(free_vars(ea)) + list(free_vars(eb))}.values())
            _r.seed(1); bad = 0
            for _ in range(500):
                sub = [(x, z3.BitVecVal(_r.getrandbits(x.size()), x.size())) for x in fv]
                if not z3.is_true(z3.simplify(z3.substitute(ea == eb, *sub))):
                    bad += 1
            DIAG.append({"node": v, "copy": j, "op": nodes[v]["op"], "edges": rec.get("edges"), "z3_depth0": _v0,
                         "z3_full": _vf, "random_mismatches": bad, "random_trials": 500})
        got, exp = value_keys(rx), value_keys(tx)
        rec["edges_syntactic"] = got == exp
        sem = {k for k in got if depends(rx, k)}       # operands whose value can change the node's value
        rec["edges"] = sem == exp
        if not rec["edges"] or not rec["edges_syntactic"]:
            rec["got"] = sorted(got); rec["got_semantic"] = sorted(sem); rec["expected"] = sorted(exp)
        per_node.append(rec)
# events on result registers that are not DFG node writes must be holds (the register's previous value)
def hold_value(r, c):
    writes = sorted(cc for cc in range(J * N) if owner_of_result(r, cc))
    if not writes:
        return None
    before = [cc for cc in writes if cc < c]
    o = owner_of_result(r, before[-1] if before else writes[-1])
    return val_sym((o[0], o[1], 0 if before else 1))


spurious = []
for (r, c), evs in reg_events.items():
    if not r.startswith("r") or (r, c) in matched:
        continue
    for ev in evs:
        rx, hv = slice_value(ev, r), hold_value(r, c)
        if rx is None or hv is None or not equal(rx, hv):
            spurious.append([r, c])
exact_nodes = sum(1 for x in per_node if x["found"] and x["edges"] and x["function"])
result.update({
    "period_as_predicted": P == result["expected_period_events"],
    "nodes_scored": len(per_node), "nodes_found": sum(x["found"] for x in per_node),
    "nodes_edges_exact": sum(bool(x.get("edges")) for x in per_node),
    "nodes_edges_syntactic_exact": sum(bool(x.get("edges_syntactic")) for x in per_node),
    "nodes_function_proved": sum(bool(x.get("function")) for x in per_node),
    "nodes_function_proved_strict": sum(bool(x.get("function_strict")) for x in per_node),
    "nodes_exact": exact_nodes, "unresolved_reads": len(UNRESOLVED), "spurious_result_events": len(spurious),
    "exact": exact_nodes == len(per_node) and P == result["expected_period_events"] and not grouping and not UNRESOLVED
             and not spurious,
    "failures": [x for x in per_node if not (x["found"] and x["edges"] and x["function"])][:10],
})
result["outcome"] = "exact" if result["exact"] else ("grouping" if grouping else "mismatch")
result["nodes_function_proved_full_expansion_only"] = len(FULL_PROOFS)   # amendment 5
json.dump(result, open(out_path, "w"), indent=1, default=str)
json.dump({"case": result["case"], "outcome": result["outcome"], "unproved": DIAG}, open(sys.argv[6], "w"), indent=1)
print(json.dumps({k: result[k] for k in ("case", "outcome", "graph_period_events", "expected_period_events", "nodes_scored",
                                         "nodes_found", "nodes_edges_exact", "nodes_function_proved", "unresolved_reads", "spurious_result_events",
                                         "grouping_issues", "words_merging_registers")}))
