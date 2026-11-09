#!/usr/bin/env python3
"""C2 source-word adapter for the pinned, unchanged ZipCPU hwbfly RTL.

This is a reference/equation adapter, never a recovery implementation.  Numeric
component and shift-lane boundaries and increasing binary significance are
supplied priors.  All effective register writes, including moves and the 13 to
11 bit product slices, are retained.  Oracle provenance is propagated forward
from the source operations without importing InvFold's producer resolver.

    python3 algorithm_recovery/generalization/c2_hwbfly.py prepare --out build/c2/hwbfly
    python3 algorithm_recovery/generalization/c2_hwbfly.py run-reference --out build/c2/hwbfly

The latter compiles ordinary (NOT FORMAL) RTL with Icarus.  It compares every
word before/after every edge, preserves X observations as null, and separately
executes the exact SMT equations on observed pre-edge values.  No recovery is
invoked.  Results are finite simulation checks, not equivalence proofs.
Only meta.window equations enter recovery.json; the complete finite trace's
equations are kept separately in reference_equations.json for adapter checks.
"""
from __future__ import annotations

import argparse
import hashlib
import importlib.util
import json
import random
import re
import subprocess
from pathlib import Path
from typing import Any


PIN = "3378b77d83a98b06184656a5cb9b54e50dfe4485"
DEFAULT_SOURCE = Path("/tmp/netnotary-bench-fft-20261101")
PARAMETERS = {"IWIDTH": 4, "CWIDTH": 6, "OWIDTH": 5, "CKPCE": 3, "SHIFT": 0}
SCOPE = (
    "Unchanged non-FORMAL RTL, supplied source numeric-word boundaries and "
    "bit order, fixed i_ce once per three clocks, three reset clocks then "
    "real finite warmup. All source register boundaries retained; control "
    "ce_phase verified separately and enables supplied. Oracle samples are "
    "physical period frames, not algorithm transaction numbers. Finite tests."
)

# (source role, width, Verilog hierarchical observation).  Packed source
# registers are cut only at numeric component/lane boundaries; no operation
# boundary is removed by this partition.
WORD_SPECS = [
    ("left_r", 4, "r_left[7:4]"), ("left_i", 4, "r_left[3:0]"),
    ("right_r", 4, "r_right[7:4]"), ("right_i", 4, "r_right[3:0]"),
    ("coef_r", 6, "r_coef[11:6]"), ("coef_i", 6, "r_coef[5:0]"),
    ("aux_1", 1, "r_aux"), ("aux_2", 1, "r_aux_2"),
    ("sum_r", 5, "r_sum_r"), ("sum_i", 5, "r_sum_i"),
    ("dif_r", 5, "r_dif_r"), ("dif_i", 5, "r_dif_i"),
    ("icoef_r", 6, "ir_coef_r"), ("icoef_i", 6, "ir_coef_i"),
    ("leftv_aux", 1, "leftv[10]"),
    ("leftv_r", 5, "leftv[9:5]"), ("leftv_i", 5, "leftv[4:0]"),
    ("leftvv_aux", 1, "leftvv[10]"),
    ("leftvv_r", 5, "leftvv[9:5]"), ("leftvv_i", 5, "leftvv[4:0]"),
    ("pipe_c0", 7, "CKPCE_THREE.mpy_pipe_c[20:14]"),
    ("pipe_c1", 7, "CKPCE_THREE.mpy_pipe_c[13:7]"),
    ("pipe_c2", 7, "CKPCE_THREE.mpy_pipe_c[6:0]"),
    ("pipe_d0", 6, "CKPCE_THREE.mpy_pipe_d[17:12]"),
    ("pipe_d1", 6, "CKPCE_THREE.mpy_pipe_d[11:6]"),
    ("pipe_d2", 6, "CKPCE_THREE.mpy_pipe_d[5:0]"),
    ("shared_product", 13, "CKPCE_THREE.mpy_pipe_out"),
    ("product_1_slice", 11, "CKPCE_THREE.rp_one"),
    ("product_2_slice", 11, "CKPCE_THREE.rp_two"),
    ("product_3", 13, "CKPCE_THREE.rp_three"),
    ("aligned_product_1", 11, "CKPCE_THREE.rp2_one"),
    ("aligned_product_2", 11, "CKPCE_THREE.rp2_two"),
    ("aligned_product_3", 13, "CKPCE_THREE.rp2_three"),
    ("saved_aux", 1, "left_saved[10]"),
    ("saved_r", 5, "left_saved[9:5]"), ("saved_i", 5, "left_saved[4:0]"),
    ("output_aux", 1, "o_aux"),
    ("multiply_r", 13, "mpy_r"), ("multiply_i", 13, "mpy_i"),
    ("rounded_left_r", 5, "rnd_left_r"),
    ("rounded_left_i", 5, "rnd_left_i"),
    ("rounded_right_r", 5, "rnd_right_r"),
    ("rounded_right_i", 5, "rnd_right_i"),
]
ROLE_WORD = {r: f"w{i}" for i, (r, _, _) in enumerate(WORD_SPECS)}
WORD_WIDTH = {ROLE_WORD[r]: w for r, w, _ in WORD_SPECS}
ROLE_WIDTH = {r: w for r, w, _ in WORD_SPECS}
INPUT_WIDTH = {f"x{i}": w for i, w in enumerate([4, 4, 4, 4, 6, 6, 1])}
OUTPUT_WIDTH = {"y0": 10, "y1": 10, "y2": 1}
INITIAL_ZERO = {
    "aux_1", "aux_2", "leftv_aux", "leftv_r", "leftv_i", "leftvv_aux",
    "leftvv_r", "leftvv_i", "saved_aux", "saved_r", "saved_i", "output_aux",
}


def dump(path: Path, value: Any) -> None:
    path.write_text(json.dumps(value, indent=2, sort_keys=True) + "\n")


def sha(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def source_identity(source: Path) -> dict[str, Any]:
    commit = subprocess.check_output(
        ["git", "-C", str(source), "rev-parse", "HEAD"], text=True).strip()
    if commit != PIN:
        raise ValueError(f"source HEAD {commit} differs from frozen {PIN}")
    paths = [source / "rtl" / name for name in ("hwbfly.v", "convround.v")]
    for p in paths:
        relative = p.relative_to(source).as_posix()
        original = subprocess.check_output(["git", "-C", str(source), "show", f"{PIN}:{relative}"])
        if original != p.read_bytes():
            raise ValueError(f"source file modified: {p}")
    return {"commit": commit, "repository": "https://github.com/ZipCPU/dblclockfft",
            "license": "LGPL-3.0-or-later", "parameters": PARAMETERS,
            "files": {str(p.relative_to(source)): sha(p) for p in paths}}


def bv(value: int, width: int) -> str:
    return f"(_ bv{value & ((1 << width) - 1)} {width})"


def sx(term: str, extra: int) -> str:
    return f"((_ sign_extend {extra}) {term})" if extra else term


def extract(term: str, hi: int, lo: int) -> str:
    return f"((_ extract {hi} {lo}) {term})"


def op(name: str, *args: str) -> str:
    return "(" + " ".join([name, *args]) + ")"


def rounded(term: str) -> str:
    """Both frozen convround instantiations take bits 8:4 and round bits 3:0.

    Keep every RHS occurrence after expansion of the source's intermediate
    wires.  This also checks that the scorer does not deduplicate operands.
    """
    trunc = extract(term, 8, 4)
    up = op("bvadd", trunc, bv(1, 5))
    return op("ite", op("=", extract(term, 3, 3), bv(0, 1)), trunc,
              op("ite", op("distinct", extract(term, 2, 0), bv(0, 3)), up,
                 op("ite", op("=", extract(trunc, 0, 0), bv(1, 1)), up, trunc)))


def signed(value: int, width: int) -> int:
    return value - (1 << width) if value & (1 << (width - 1)) else value


def numeric_round(value: int | None) -> int | None:
    if value is None:
        return None
    high = (value >> 4) & 31
    low = value & 15
    return (high + (low > 8 or (low == 8 and (high & 1)))) & 31


def reference_step(state: dict, inputs: dict, reset: bool, ce: bool, phase: int):
    """Independent integer/NBA source model; RHS always reads old state.

    This code does not evaluate the emitted SMT and never resolves historical
    producers. None represents a wholly unknown numeric word.
    """
    new = dict(state)

    def calc(role, args, function):
        values = [state[a] for a in args]
        new[role] = None if None in values else function(*values) & ((1 << ROLE_WIDTH[role]) - 1)

    if reset:
        for role in INITIAL_ZERO:
            new[role] = 0
    elif ce:
        new["aux_1"] = inputs["x6"]
        new["aux_2"] = state["aux_1"]
        for suffix, source in (("aux", "aux_2"), ("r", "sum_r"), ("i", "sum_i")):
            new[f"leftv_{suffix}"] = state[source]
            new[f"leftvv_{suffix}"] = state[f"leftv_{suffix}"]
            new[f"saved_{suffix}"] = state[f"leftvv_{suffix}"]
        new["output_aux"] = state["saved_aux"]
    if ce:
        for i, role in enumerate(["left_r", "left_i", "right_r", "right_i", "coef_r", "coef_i"]):
            new[role] = inputs[f"x{i}"]
        for suffix in ("r", "i"):
            calc(f"sum_{suffix}", [f"left_{suffix}", f"right_{suffix}"],
                 lambda a, b: signed(a, 4) + signed(b, 4))
            calc(f"dif_{suffix}", [f"left_{suffix}", f"right_{suffix}"],
                 lambda a, b: signed(a, 4) - signed(b, 4))
            new[f"icoef_{suffix}"] = state[f"coef_{suffix}"]
        calc("product_1_slice", ["shared_product"], lambda a: a)
        for i in (1, 2, 3):
            new[f"aligned_product_{i}"] = state[f"product_{i}_slice" if i != 3 else "product_3"]
        calc("multiply_r", ["aligned_product_1", "aligned_product_2"],
             lambda a, b: signed(a, 11) - signed(b, 11))
        calc("multiply_i", ["aligned_product_3", "aligned_product_1", "aligned_product_2"],
             lambda a, b, c: signed(a, 13) - signed(b, 11) - signed(c, 11))
        for suffix in ("r", "i"):
            value = state[f"saved_{suffix}"]
            left = None if value is None else (signed(value, 5) << 4) & 2047
            new[f"rounded_left_{suffix}"] = numeric_round(left)
            new[f"rounded_right_{suffix}"] = numeric_round(state[f"multiply_{suffix}"])
    valid = ce or phase < 2
    if phase == 0:
        for prefix, r, i, width in (("c", "icoef_r", "icoef_i", 6), ("d", "dif_r", "dif_i", 5)):
            calc(f"pipe_{prefix}0", [r], lambda a, w=width: signed(a, w))
            calc(f"pipe_{prefix}1", [i], lambda a, w=width: signed(a, w))
            calc(f"pipe_{prefix}2", [r, i], lambda a, b, w=width: signed(a, w) + signed(b, w))
    elif valid:
        for prefix in ("c", "d"):
            new[f"pipe_{prefix}0"] = state[f"pipe_{prefix}1"]
            new[f"pipe_{prefix}1"] = state[f"pipe_{prefix}2"]
            new[f"pipe_{prefix}2"] = 0
    if valid:
        calc("shared_product", ["pipe_c0", "pipe_d0"], lambda a, b: signed(a, 7) * signed(b, 6))
    if phase == 0:
        calc("product_2_slice", ["shared_product"], lambda a: a)
    if phase == 1:
        new["product_3"] = state["shared_product"]
    new_phase = 3 if reset else 0 if ce else phase + 1 if phase != 3 else phase
    return new, new_phase


def source_equations(T: int, reset: bool, ce: bool, phase: int):
    """All enabled source assignments; separate from numeric reference code."""
    q = lambda role: f"{ROLE_WORD[role]}_o_Q_{T}"
    equations: dict[str, tuple[str, str]] = {}

    def put(role, expression, kind=None):
        if kind is None:
            match = re.fullmatch(r"(w\d+)_o_Q_\d+", expression)
            port = re.fullmatch(r"(x\d+)_\d+", expression)
            identity = ((match and WORD_WIDTH[match[1]] == ROLE_WIDTH[role])
                        or (port and INPUT_WIDTH[port[1]] == ROLE_WIDTH[role]))
            kind = "copy" if identity else "compute"
        equations[role] = (expression, kind)

    if reset:
        for role in INITIAL_ZERO:
            put(role, bv(0, ROLE_WIDTH[role]), "initialization")
    elif ce:
        put("aux_1", f"x6_{T}")
        put("aux_2", q("aux_1"))
        for suffix, source in (("aux", "aux_2"), ("r", "sum_r"), ("i", "sum_i")):
            put(f"leftv_{suffix}", q(source))
            put(f"leftvv_{suffix}", q(f"leftv_{suffix}"))
            put(f"saved_{suffix}", q(f"leftvv_{suffix}"))
        put("output_aux", q("saved_aux"))
    if ce:
        for i, role in enumerate(["left_r", "left_i", "right_r", "right_i", "coef_r", "coef_i"]):
            put(role, f"x{i}_{T}")
        for suffix in ("r", "i"):
            left, right = sx(q(f"left_{suffix}"), 1), sx(q(f"right_{suffix}"), 1)
            put(f"sum_{suffix}", op("bvadd", left, right))
            put(f"dif_{suffix}", op("bvsub", left, right))
            put(f"icoef_{suffix}", q(f"coef_{suffix}"))
        put("product_1_slice", extract(q("shared_product"), 10, 0))
        for i in (1, 2, 3):
            put(f"aligned_product_{i}", q(f"product_{i}_slice" if i != 3 else "product_3"))
        one, two = sx(q("aligned_product_1"), 2), sx(q("aligned_product_2"), 2)
        put("multiply_r", op("bvsub", one, two))
        put("multiply_i", op("bvsub", op("bvsub", q("aligned_product_3"), one), two))
        for suffix in ("r", "i"):
            left = op("concat", sx(q(f"saved_{suffix}"), 2), bv(0, 4))
            put(f"rounded_left_{suffix}", rounded(left))
            put(f"rounded_right_{suffix}", rounded(q(f"multiply_{suffix}")))
    valid = ce or phase < 2
    if phase == 0:
        for prefix, r, i in (("c", "icoef_r", "icoef_i"), ("d", "dif_r", "dif_i")):
            put(f"pipe_{prefix}0", sx(q(r), 1))
            put(f"pipe_{prefix}1", sx(q(i), 1))
            put(f"pipe_{prefix}2", op("bvadd", sx(q(r), 1), sx(q(i), 1)))
    elif valid:
        for prefix in ("c", "d"):
            put(f"pipe_{prefix}0", q(f"pipe_{prefix}1"))
            put(f"pipe_{prefix}1", q(f"pipe_{prefix}2"))
            put(f"pipe_{prefix}2", bv(0, ROLE_WIDTH[f"pipe_{prefix}2"]))
    if valid:
        put("shared_product", op("bvmul", sx(q("pipe_c0"), 6), sx(q("pipe_d0"), 7)))
    if phase == 0:
        put("product_2_slice", extract(q("shared_product"), 10, 0))
    if phase == 1:
        put("product_3", q("shared_product"))
    observations = {
        "y0": op("concat", q("rounded_left_r"), q("rounded_left_i")),
        "y1": op("concat", q("rounded_right_r"), q("rounded_right_i")),
        "y2": q("output_aux"),
    }
    return equations, observations


def parse_term(text: str):
    tokens = iter(re.findall(r"\(|\)|[^\s()]+", text))

    def read(first=None):
        token = next(tokens) if first is None else first
        if token != "(":
            return token
        result = []
        for token in tokens:
            if token == ")":
                return result
            result.append(read(token))
        raise ValueError("unclosed SMT term")

    result = read()
    if next(tokens, None) is not None:
        raise ValueError("trailing SMT token")
    return result


def occurrences(text: str, T: int, producers: dict):
    """Operand child-index paths (head excluded); generated terms contain no let."""
    result = []

    def walk(node, path):
        if isinstance(node, str):
            word = re.fullmatch(r"(w\d+)_o_Q_(\d+)", node)
            port = re.fullmatch(r"(x\d+)_(\d+)", node)
            if word:
                assert int(word[2]) == T
                result.append({"path": path, "symbol": node,
                               "producer": producers[word[1]][:], "width": WORD_WIDTH[word[1]]})
            elif port:
                result.append({"path": path, "symbol": node,
                               "producer": ["input", port[1], int(port[2])], "width": INPUT_WIDTH[port[1]]})
            return
        if node[0] == "_":
            return
        for i, child in enumerate(node[1:]):
            walk(child, path + [i])

    walk(parse_term(text), [])
    return result


def logical_name(role: str, T: int, kind: str) -> str:
    if kind == "initialization":
        return f"initial_{role}_slot{T % 3}"
    if role == "shared_product":
        return {2: "M1_coef_real_times_diff_real", 0: "M2_coef_imag_times_diff_imag",
                1: "M3_coef_sum_times_diff_sum"}[T % 3]
    return f"{role}_slot{T % 3}"


def output_values(state: dict):
    def pack(a, b):
        return None if state[a] is None or state[b] is None else (state[a] << 5) | state[b]
    return {"y0": pack("rounded_left_r", "rounded_left_i"),
            "y1": pack("rounded_right_r", "rounded_right_i"), "y2": state["output_aux"]}


def stimulus(transactions: int, seed: int):
    if transactions < 16:
        raise ValueError("at least 16 transactions required for startup and period checks")
    rng = random.Random(seed)
    directed = [
        (0, 0, 0, 0, 0, 0), (7, 7, -8, -8, 31, -32),
        (-8, -8, 7, 7, -32, 31), (7, -8, -8, 7, 31, 31),
        (-8, 7, 7, -8, -32, -32), (1, -1, -1, 1, 1, -1),
        (3, 5, -2, -4, 15, 17), (-5, -3, 4, 2, -17, -15),
    ]
    rows = []
    for T in range(3 + transactions * 3):
        # Drive fresh non-CE values as well.  The DUT must ignore them.
        row = {port: rng.randrange(1 << width) for port, width in INPUT_WIDTH.items()}
        if T >= 3 and T % 3 == 0:
            n = (T - 3) // 3
            if n < len(directed):
                row.update({f"x{i}": value & ((1 << INPUT_WIDTH[f"x{i}"]) - 1)
                            for i, value in enumerate(directed[n])})
            row["x6"] = n & 1
        rows.append(row)
    return rows


def make_testbench(rows: list[dict]) -> str:
    observations = ", ".join("dut." + source for _, _, source in WORD_SPECS)
    fmt = " ".join(["%h"] * len(WORD_SPECS))
    template = '''`timescale 1ns/1ps
`default_nettype none
module c2_hwbfly_tb;
  reg clk=0, reset=1, ce=0, aux=0;
  reg [7:0] left=0, right=0;
  reg [11:0] coef=0;
  wire [9:0] out_left, out_right;
  wire out_aux;
  hwbfly #(.IWIDTH(4),.CWIDTH(6),.OWIDTH(5),.CKPCE(3),.SHIFT(0))
    dut(.i_clk(clk),.i_reset(reset),.i_ce(ce),.i_coef(coef),
        .i_left(left),.i_right(right),.i_aux(aux),.o_left(out_left),
        .o_right(out_right),.o_aux(out_aux));
  task sample;
    input integer cycle;
    input integer stage;
    begin
      $display("C2 %0d %0d FMT %h %h %h %h", cycle, stage,
        OBSERVATIONS, dut.CKPCE_THREE.ce_phase, out_left, out_right, out_aux);
    end
  endtask
  initial begin
    #1;
ROWS
    $finish;
  end
endmodule
'''
    lines = []
    for T, row in enumerate(rows):
        left, right = row["x0"] * 16 + row["x1"], row["x2"] * 16 + row["x3"]
        coef = row["x4"] * 64 + row["x5"]
        lines.append(f"    reset=1'b{int(T < 3)}; ce=1'b{int(T >= 3 and T % 3 == 0)}; "
                     f"left=8'h{left:02x}; right=8'h{right:02x}; coef=12'h{coef:03x}; aux=1'b{row['x6']};")
        lines.append(f"    #4; sample({T},0); clk=1; #1; sample({T},1); #4; clk=0; #1;")
    return template.replace("FMT", fmt).replace("OBSERVATIONS", observations).replace("ROWS", "\n".join(lines))


def prepare(args):
    out = args.out.resolve()
    out.mkdir(parents=True, exist_ok=True)
    source = args.source.resolve()
    identity = source_identity(source)
    rows = stimulus(args.transactions, args.seed)
    origin = 27
    window = list(range(origin, origin + 18))
    if window[-1] >= len(rows):
        raise ValueError("stimulus too short")
    equations, events = [], []
    write_enable = {w: [] for w in WORD_WIDTH}
    before = {w: [] for w in WORD_WIDTH}
    after = {w: [] for w in WORD_WIDTH}
    outputs = {p: [] for p in OUTPUT_WIDTH}
    phase_before, phase_after = [], []
    state = {r: 0 if r in INITIAL_ZERO else None for r, _, _ in WORD_SPECS}
    producers = {w: ["init", w, 0] for w in WORD_WIDTH}
    phase = 3
    for T, inputs in enumerate(rows):
        reset, ce = T < 3, T >= 3 and T % 3 == 0
        updates, observations = source_equations(T, reset, ce, phase)
        new_producers = dict(producers)
        for role, _, _ in WORD_SPECS:
            word = ROLE_WORD[role]
            target = word + " - i_D"
            before[word].append(state[role])
            write_enable[word].append(int(role in updates))
            expression, kind = updates.get(role, (f"{word}_o_Q_{T}", "hold"))
            equations.append({"T": T, "target": target, "smt2": expression})
            if role in updates:
                logical, sample = logical_name(role, T, kind), (T - origin) // 3
                events.append({"T": T, "target": target, "logical": logical, "sample": sample,
                               "kind": kind, "width": WORD_WIDTH[word],
                               "operands": occurrences(expression, T, producers)})
                new_producers[word] = ["op", logical, sample]
        values = output_values(state)
        for port, expression in observations.items():
            target = "top_module - " + port
            equations.append({"T": T, "target": target, "smt2": expression})
            events.append({"T": T, "target": target, "logical": f"observe_{port}_slot{T % 3}",
                           "sample": (T - origin) // 3, "kind": "observation", "width": OUTPUT_WIDTH[port],
                           "operands": occurrences(expression, T, producers)})
            outputs[port].append(values[port])
        phase_before.append(phase)
        state, phase = reference_step(state, inputs, reset, ce, phase)
        phase_after.append(phase)
        for role, _, _ in WORD_SPECS:
            after[ROLE_WORD[role]].append(state[role])
        producers = new_producers
    compare_from = next(T for T in range(len(rows))
                        if all(before[w][T] is not None and after[w][T] is not None for w in WORD_WIDTH))
    assert compare_from <= origin
    window_set = set(window)
    recovery = {"equations": [equation for equation in equations if equation["T"] in window_set],
                "meta": {"window": window, "widths": WORD_WIDTH,
                "ports": {"in": INPUT_WIDTH, "out": OUTPUT_WIDTH}, "write_enable": write_enable}}
    oracle = {"period": 3, "origin": origin, "events": events,
              "source_identity": identity, "reference_scope": SCOPE,
              "word_roles": {ROLE_WORD[r]: {"role": r, "width": w, "source": source}
                             for r, w, source in WORD_SPECS},
              "input_roles": dict(zip(INPUT_WIDTH, ["left_real", "left_imag", "right_real", "right_imag",
                                                     "coefficient_real", "coefficient_imag", "aux"]))}
    reference = {"schema": "C2_TRACE_V1", "reference_scope": SCOPE,
                 "inputs": {p: [row[p] for row in rows] for p in INPUT_WIDTH},
                 "before": before, "after": after, "outputs": outputs, "compare_from": compare_from,
                 "controls": {"reset": [int(T < 3) for T in range(len(rows))],
                              "ce": [int(T >= 3 and T % 3 == 0) for T in range(len(rows))],
                              "phase_before": phase_before, "phase_after": phase_after},
                 "initial_state": {ROLE_WORD[r]: 0 if r in INITIAL_ZERO else None for r, _, _ in WORD_SPECS}}
    dump(out / "recovery.json", recovery)
    dump(out / "oracle.json", oracle)
    dump(out / "reference.json", reference)
    dump(out / "reference_equations.json", {"equations": equations})
    (out / "testbench.v").write_text(make_testbench(rows))
    manifest = {"schema": "C2_HWBFLY_ADAPTER_V1", "status": "PREPARED_NOT_VALIDATED",
                "source": str(source), "source_identity": identity, "adapter_sha256": sha(Path(__file__)),
                "reference_scope": SCOPE, "seed": args.seed, "transactions": args.transactions,
                "cycles": len(rows), "period": 3, "window": window, "compare_from": compare_from,
                "supplied_priors": ["source numeric component/lane boundaries", "unsigned bit order LSB=0",
                                     "fixed reset and i_ce protocol", "effective per-word write enables"],
                "recovery_hash": sha(out / "recovery.json"), "oracle_hash": sha(out / "oracle.json"),
                "reference_hash": sha(out / "reference.json"), "testbench_hash": sha(out / "testbench.v"),
                "reference_equations_hash": sha(out / "reference_equations.json"),
                "input_schema_revision": "Window-only recovery equations; all-cycle equations isolated for reference validation before first recovery execution.",
                "timing_prediction": {"input_cycle": "3+3*n", "sum_difference": 3,
                                      "operand_load": 4, "products_M1_M2_M3": [5, 6, 7],
                                      "aligned_products": 9, "real_imag": 12, "rounded_outputs": 15}}
    dump(out / "manifest.json", manifest)
    print(json.dumps({"status": manifest["status"], "out": str(out), "cycles": len(rows),
                      "words": len(WORD_SPECS), "compare_from": compare_from}))


def load_smt_compiler():
    path = Path(__file__).resolve().parents[1] / "invfold" / "smt2.py"
    spec = importlib.util.spec_from_file_location("c2_hwbfly_frozen_smt", path)
    module = importlib.util.module_from_spec(spec)
    assert spec.loader is not None
    spec.loader.exec_module(module)
    return module, sha(path)


def run_reference(args):
    out = args.out.resolve()
    manifest = json.loads((out / "manifest.json").read_text())
    source = Path(manifest["source"])
    if source_identity(source) != manifest["source_identity"]:
        raise ValueError("source identity changed since prepare")
    for name, key in (("recovery.json", "recovery_hash"), ("oracle.json", "oracle_hash"),
                      ("reference.json", "reference_hash"), ("testbench.v", "testbench_hash"),
                      ("reference_equations.json", "reference_equations_hash")):
        if sha(out / name) != manifest[key]:
            raise ValueError(f"prepared artifact changed: {name}")
    reference = json.loads((out / "reference.json").read_text())
    recovery = json.loads((out / "recovery.json").read_text())
    reference_equations = json.loads((out / "reference_equations.json").read_text())["equations"]
    window = set(recovery["meta"]["window"])
    if recovery["equations"] != [equation for equation in reference_equations if equation["T"] in window]:
        raise ValueError("recovery equations differ from reference equations restricted to the recovery window")
    command = ["iverilog", "-g2012", "-s", "c2_hwbfly_tb", "-o", str(out / "simulation.vvp"),
               str(out / "testbench.v"), str(source / "rtl/hwbfly.v"), str(source / "rtl/convround.v")]
    compile_run = subprocess.run(command, text=True, capture_output=True, timeout=120)
    (out / "compile.log").write_text(compile_run.stdout + compile_run.stderr)
    receipt = {"status": "FAIL", "kind": "finite_original_RTL_and_exact_equation_check",
               "compile_command": command, "FORMAL_defined": False, "source_identity": manifest["source_identity"],
               "recovery_hash": manifest["recovery_hash"], "oracle_hash": manifest["oracle_hash"],
               "reference_equations_hash": manifest["reference_equations_hash"],
               "failures": [], "reference_scope": SCOPE}
    if compile_run.returncode:
        receipt["failures"].append({"stage": "compile", "returncode": compile_run.returncode})
        dump(out / "reference_receipt.json", receipt)
        raise SystemExit("Icarus compilation failed; see compile.log and reference_receipt.json")
    simulation = subprocess.run(["vvp", str(out / "simulation.vvp")], text=True, capture_output=True, timeout=120)
    (out / "simulation.log").write_text(simulation.stdout + simulation.stderr)
    if simulation.returncode:
        receipt["failures"].append({"stage": "simulation", "returncode": simulation.returncode})
    ncycles = manifest["cycles"]
    actual = {"schema": "C2_TRACE_V1", "reference_scope": SCOPE, "inputs": reference["inputs"],
              "before": {w: [None] * ncycles for w in WORD_WIDTH},
              "after": {w: [None] * ncycles for w in WORD_WIDTH},
              "outputs": {p: [None] * ncycles for p in OUTPUT_WIDTH},
              "outputs_after": {p: [None] * ncycles for p in OUTPUT_WIDTH},
              "compare_from": reference["compare_from"], "initial_state": reference["initial_state"],
              "controls": {**reference["controls"], "phase_before": [None] * ncycles,
                           "phase_after": [None] * ncycles},
              "reference_before": reference["before"], "reference_after": reference["after"],
              "reference_outputs": reference["outputs"]}
    seen, mismatches, prefix_unknowns, word_checks, control_checks = set(), [], [], 0, 0
    raw_unknowns = []
    for line in simulation.stdout.splitlines():
        if not line.startswith("C2 "):
            continue
        fields = line.split()
        T, stage = int(fields[1]), int(fields[2])
        if (T, stage) in seen or not 0 <= T < ncycles or stage not in (0, 1):
            raise ValueError(f"invalid/duplicate trace row: {line}")
        seen.add((T, stage))
        tokens = fields[3:]
        if len(tokens) != len(WORD_SPECS) + 4:
            raise ValueError(f"unexpected trace width at {T}: {line}")
        values = []
        for index, token in enumerate(tokens):
            if re.search("[xz]", token.lower()):
                values.append(None)
                raw_unknowns.append({"T": T, "stage": stage, "column": index, "hex": token})
            else:
                values.append(int(token, 16))
        side = "before" if stage == 0 else "after"
        for index, word in enumerate(WORD_WIDTH):
            observed, expected = values[index], reference[side][word][T]
            actual[side][word][T] = observed
            if expected is None or observed is None:
                prefix_unknowns.append({"T": T, "side": side, "word": word, "actual": observed, "reference": expected})
            if observed != expected:
                mismatches.append({"T": T, "side": side, "word": word, "actual": observed, "reference": expected})
            elif observed is not None:
                word_checks += 1
        index = len(WORD_SPECS)
        actual["controls"]["phase_" + side][T] = values[index]
        expected_phase = reference["controls"]["phase_" + side][T]
        if values[index] != expected_phase:
            mismatches.append({"T": T, "side": side, "control": "ce_phase", "actual": values[index], "reference": expected_phase})
        control_checks += 1
        for i, port in enumerate(OUTPUT_WIDTH):
            actual["outputs" if stage == 0 else "outputs_after"][port][T] = values[index + 1 + i]
            expected = reference["outputs"][port][T] if stage == 0 else output_values(
                {r: reference["after"][ROLE_WORD[r]][T] for r, _, _ in WORD_SPECS})[port]
            if values[index + 1 + i] != expected:
                mismatches.append({"T": T, "side": side, "port": port, "actual": values[index + 1 + i], "reference": expected})
    if len(seen) != ncycles * 2:
        receipt["failures"].append({"stage": "trace", "expected_rows": ncycles * 2, "rows": len(seen)})
    actual["unknown_observations"] = raw_unknowns
    dump(out / "trace.json", actual)
    smt, smt_hash = load_smt_compiler()
    equation_checks, equation_skips, equation_failures = 0, 0, []
    for equation in reference_equations:
        T, target, text = equation["T"], equation["target"], equation["smt2"]
        widths = {f"{w}_o_Q_{T}": width for w, width in WORD_WIDTH.items()}
        widths.update({f"{p}_{T}": width for p, width in INPUT_WIDTH.items()})
        function, symbols, width = smt.compile_term(text, widths)
        env = {f"{w}_o_Q_{T}": actual["before"][w][T] for w in WORD_WIDTH}
        env.update({f"{p}_{T}": reference["inputs"][p][T] for p in INPUT_WIDTH})
        if any(env[symbol] is None for symbol in symbols):
            equation_skips += 1
            if T >= actual["compare_from"]:
                equation_failures.append({"T": T, "target": target, "reason": "unknown after initialized boundary"})
            continue
        value = function(env)
        if target.startswith("top_module"):
            port = target.split(" - ")[1]
            expected = actual["outputs"][port][T]
            expected_width = OUTPUT_WIDTH[port]
        else:
            word = target.split(" - ")[0]
            expected = actual["after"][word][T]
            expected_width = WORD_WIDTH[word]
        if value != expected or width != expected_width:
            equation_failures.append({"T": T, "target": target, "actual": expected, "equation": value,
                                      "width": width, "expected_width": expected_width})
        equation_checks += 1
    receipt["failures"].extend(mismatches)
    receipt["failures"].extend(equation_failures)
    receipt.update({"status": "PASS" if not receipt["failures"] else "FAIL",
                    "cycles": ncycles, "words": len(WORD_WIDTH), "compare_from": actual["compare_from"],
                    "known_word_comparisons": word_checks, "control_comparisons": control_checks,
                    "unknown_word_observations": len(prefix_unknowns), "equation_checks": equation_checks,
                    "equation_unknown_skips": equation_skips, "smt2_sha256": smt_hash,
                    "trace_sha256": sha(out / "trace.json"), "adapter_sha256": sha(Path(__file__)),
                    "timing_verified_by_all_register_comparison": not mismatches,
                    "timing_prediction": manifest["timing_prediction"],
                    "structural_recovery": "NOT_RUN; semantic adapter PASS is not structural recovery PASS"})
    dump(out / "reference_receipt.json", receipt)
    manifest["status"] = "REFERENCE_VALIDATED" if receipt["status"] == "PASS" else "REFERENCE_FAILED"
    dump(out / "manifest.json", manifest)
    print(json.dumps({key: receipt[key] for key in ("status", "cycles", "words", "compare_from",
                                                  "known_word_comparisons", "equation_checks", "equation_unknown_skips")}))
    if receipt["status"] != "PASS":
        raise SystemExit("reference mismatch; see reference_receipt.json")


def main():
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    commands = parser.add_subparsers(dest="command", required=True)
    prep = commands.add_parser("prepare", help="write anonymous equations, independent oracle/reference, and TB")
    prep.add_argument("--source", type=Path, default=DEFAULT_SOURCE)
    prep.add_argument("--out", type=Path, required=True)
    prep.add_argument("--transactions", type=int, default=64)
    prep.add_argument("--seed", type=int, default=20261101)
    prep.set_defaults(function=prepare)
    run = commands.add_parser("run-reference", help="serial-resource job: compile and simulate unchanged original RTL")
    run.add_argument("--out", type=Path, required=True)
    run.set_defaults(function=run_reference)
    args = parser.parse_args()
    args.function(args)


if __name__ == "__main__":
    main()
