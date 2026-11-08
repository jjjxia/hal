#!/usr/bin/env python3
"""Evaluation-only C2 adapter for pinned original picorv32_pcpi_mul RTL.

This supplies source word boundaries/order, exact CSA equations and effective
writes under C2_PICORV32_PROTOCOL.txt. It is not recovery code. Source roles
and forward producer tags are confined to the separate scoring oracle.
prepare writes an immutable test case; run-reference compiles/simulates the
unchanged RTL and checks every supplied word equation, control and MUL result.
Neither command invokes InvFold. Heavy runs are coordinated by the parent.
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


PIN = "ef203c2b0a3fb793280f5114941416c425c5b461"
DEFAULT_SOURCE = Path("/home/jiahao/hal/build/c2_sources/picorv32")
PROTOCOL = Path(__file__).with_name("C2_PICORV32_PROTOCOL.txt")
PARAMETERS = {"STEPS_AT_ONCE": 1, "CARRY_CHAIN": 0}
PERIOD, RESET_CYCLES, ORIGIN, WINDOW_LENGTH = 48, 4, 100, 288
INSN = 0x02000033
MASK64 = (1 << 64) - 1
WORD_SPECS = [("shifted_multiplier", 64, "rs1"),
              ("shifted_multiplicand", 64, "rs2"),
              ("partial_sum", 64, "rd"),
              ("partial_carry", 64, "rdx"),
              ("registered_result", 32, "pcpi_rd")]
ROLE_WORD = {role: f"w{i}" for i, (role, _, _) in enumerate(WORD_SPECS)}
WORD_WIDTH = {ROLE_WORD[role]: width for role, width, _ in WORD_SPECS}
INPUT_WIDTH = {"x0": 32, "x1": 32}
OUTPUT_WIDTH = {"y0": 32}
CONTROL_REGS = {"instr_mul": 1, "instr_mulh": 1, "instr_mulhsu": 1,
                "instr_mulhu": 1, "pcpi_wait": 1, "pcpi_wait_q": 1,
                "mul_counter": 7, "mul_waiting": 1, "mul_finish": 1,
                "pcpi_wr": 1, "pcpi_ready": 1}
CONTROL_WIDTH = {**CONTROL_REGS, "instr_any_mul": 1, "instr_any_mulh": 1,
                 "instr_rs1_signed": 1, "instr_rs2_signed": 1, "mul_start": 1}
FROZEN_CORE = {
    "invfold.py": "a9ace86cd1c1c3cb4a85e1d59e749a3c68b92da46029bc2d1643f41901f64d5e",
    "smt2.py": "306e201804ec2d7c8c688f5521a370a49daf13b2014b338a16d4a50b42e60a65",
    "identify.py": "bb31a1d076c0db3fea1c2e4bb873c9524874bf40fcc355326e87a69310622b6f",
}
SCOPE = (
    "Unmodified non-FORMAL PicoRV32 PCPI MUL RTL, STEPS_AT_ONCE=1 and "
    "non-default CARRY_CHAIN=0. Supplied five numeric-word boundaries/order, "
    "48-clock input/control protocol and effective writes. All four 64-bit "
    "storage boundaries and the 32-bit output register retained; every "
    "iteration and repeated waiting assignment retained. Control is checked "
    "separately. Original-RTL, word-equation and integer-product checks are "
    "finite validation, not anonymous recovery or a universal certificate."
)


def dump(path: Path, value: Any) -> None:
    path.write_text(json.dumps(value, indent=2, sort_keys=True) + "\n")


def sha(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def source_identity(source: Path) -> dict:
    commit = subprocess.check_output(
        ["git", "-C", str(source), "rev-parse", "HEAD"], text=True).strip()
    if commit != PIN:
        raise ValueError(f"source HEAD {commit} differs from {PIN}")
    original = subprocess.check_output(
        ["git", "-C", str(source), "show", f"{PIN}:picorv32.v"])
    rtl = source / "picorv32.v"
    if original != rtl.read_bytes():
        raise ValueError("pinned picorv32.v has been modified")
    return {"repository": "https://github.com/YosysHQ/picorv32", "commit": PIN,
            "license": "ISC", "module": "picorv32_pcpi_mul",
            "source_lines": [2197, 2316], "parameters": PARAMETERS,
            "non_default_parameters": {"CARRY_CHAIN": 0},
            "files": {"picorv32.v": sha(rtl)}}


def core_identity() -> dict:
    folder = Path(__file__).resolve().parents[1] / "invfold"
    actual = {name: sha(folder / name) for name in FROZEN_CORE}
    if actual != FROZEN_CORE:
        raise ValueError("existing frozen recovery core changed")
    return actual


def land(a, b):
    return 0 if a == 0 or b == 0 else None if a is None or b is None else 1


def lnot(a):
    return None if a is None else int(not a)


def lor(*args):
    return 1 if 1 in args else None if None in args else 0


def controls(registers: dict) -> dict:
    value = dict(registers)
    value["instr_any_mul"] = lor(*(registers[n] for n in
                                    ("instr_mul", "instr_mulh", "instr_mulhsu", "instr_mulhu")))
    value["instr_any_mulh"] = lor(*(registers[n] for n in
                                     ("instr_mulh", "instr_mulhsu", "instr_mulhu")))
    value["instr_rs1_signed"] = lor(registers["instr_mulh"], registers["instr_mulhsu"])
    value["instr_rs2_signed"] = registers["instr_mulh"]
    value["mul_start"] = land(registers["pcpi_wait"], lnot(registers["pcpi_wait_q"]))
    return value


def protocol_at(T: int) -> tuple[int, int, int]:
    slot = (T - RESET_CYCLES) % PERIOD
    return int(T >= RESET_CYCLES), int(T >= RESET_CYCLES and slot <= 35), slot


def reference_step(state: dict, registers: dict, inputs: dict, resetn: int, valid: int):
    """Independent source/NBA integer model; no SMT evaluation or history search."""
    old, new, nxt = controls(registers), dict(state), dict(registers)
    for name in ("instr_mul", "instr_mulh", "instr_mulhsu", "instr_mulhu"):
        nxt[name] = int(name == "instr_mul" and resetn and valid)
    nxt["pcpi_wait"], nxt["pcpi_wait_q"] = old["instr_any_mul"], old["pcpi_wait"]
    nxt["mul_finish"] = 0
    if not resetn:
        nxt["mul_waiting"] = 1
    elif old["mul_waiting"] == 1:
        # This fixed MUL protocol never sign-extends either operand.
        if old["instr_rs1_signed"] != 0 or old["instr_rs2_signed"] != 0:
            raise ValueError("unexpected signed mode under fixed MUL protocol")
        new["shifted_multiplier"], new["shifted_multiplicand"] = inputs["x0"], inputs["x1"]
        new["partial_sum"], new["partial_carry"] = 0, 0
        nxt["mul_counter"], nxt["mul_waiting"] = 30, lnot(old["mul_start"])
    else:
        a, b = state["shifted_multiplier"], state["shifted_multiplicand"]
        total, carry = state["partial_sum"], state["partial_carry"]
        term = None if a is None else b if a & 1 else 0
        if None in (total, carry, term):
            new["partial_sum"] = new["partial_carry"] = None
        else:
            new["partial_sum"] = (total ^ carry ^ term) & MASK64
            new["partial_carry"] = (((total & carry) | (total & term) | (carry & term)) << 1) & MASK64
        new["shifted_multiplier"] = None if a is None else a >> 1
        new["shifted_multiplicand"] = None if b is None else (b << 1) & MASK64
        counter = old["mul_counter"]
        nxt["mul_counter"] = None if counter is None else (counter - 1) & 127
        if counter is not None and counter & 64:
            nxt["mul_finish"], nxt["mul_waiting"] = 1, 1
    nxt["pcpi_wr"], nxt["pcpi_ready"] = 0, 0
    if resetn and old["mul_finish"] == 1:
        nxt["pcpi_wr"], nxt["pcpi_ready"] = 1, 1
        value = state["partial_sum"]
        new["registered_result"] = None if value is None else value & 0xffffffff
    return new, nxt


def bv(value: int, width: int) -> str:
    return f"(_ bv{value & ((1 << width) - 1)} {width})"


def op(name: str, *args: str) -> str:
    return "(" + " ".join([name, *args]) + ")"


def extract(term: str, hi: int, lo: int) -> str:
    return f"((_ extract {hi} {lo}) {term})"


def source_equations(T: int, old: dict, resetn: int) -> dict:
    """Source arithmetic syntax; never replace CSA by an integer product."""
    q = lambda role: f"{ROLE_WORD[role]}_o_Q_{T}"
    updates = {}
    if resetn:
        if old["mul_waiting"] == 1:
            updates = {"shifted_multiplier": f"((_ zero_extend 32) x0_{T})",
                       "shifted_multiplicand": f"((_ zero_extend 32) x1_{T})",
                       "partial_sum": bv(0, 64), "partial_carry": bv(0, 64)}
        else:
            a, b, total, carry = (q(r) for r in
                                 ("shifted_multiplier", "shifted_multiplicand", "partial_sum", "partial_carry"))
            term = op("ite", op("=", extract(a, 0, 0), bv(1, 1)), b, bv(0, 64))
            updates = {
                "shifted_multiplier": op("bvlshr", a, bv(1, 64)),
                "shifted_multiplicand": op("bvshl", b, bv(1, 64)),
                "partial_sum": op("bvxor", op("bvxor", total, carry), term),
                "partial_carry": op("bvshl", op("bvor", op("bvor", op("bvand", total, carry),
                                    op("bvand", total, term)), op("bvand", carry, term)), bv(1, 64)),
            }
        if old["mul_finish"] == 1:
            updates["registered_result"] = extract(q("partial_sum"), 31, 0)
    return updates


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
        raise ValueError("unclosed SMT expression")

    value = read()
    if next(tokens, None) is not None:
        raise ValueError("trailing SMT token")
    return value


def occurrences(text: str, T: int, producers: dict) -> list:
    """Syntax paths only; producer values are source-forward old tags."""
    result = []

    def walk(term, path):
        if isinstance(term, str):
            word = re.fullmatch(r"(w\d+)_o_Q_(\d+)", term)
            port = re.fullmatch(r"(x\d+)_(\d+)", term)
            if word:
                if int(word[2]) != T:
                    raise ValueError("source oracle reads a non-current physical state")
                result.append({"path": path, "symbol": term, "width": WORD_WIDTH[word[1]],
                               "producer": producers[word[1]][:]})
            elif port:
                result.append({"path": path, "symbol": term, "width": INPUT_WIDTH[port[1]],
                               "producer": ["input", port[1], int(port[2])]})
            else:
                raise ValueError("unknown free source symbol: " + term)
            return
        if term[0] == "_":
            return
        for index, child in enumerate(term[1:]):
            walk(child, path + [index])

    walk(parse_term(text), [])
    return result


def logical_name(role: str, slot: int) -> str:
    if role == "registered_result":
        return "register_low_product_from_old_sum"
    if 3 <= slot <= 34:
        return f"{role}_iteration_{slot - 3:02d}"
    return f"{role}_waiting_assignment_slot_{slot:02d}"


def stimulus(transactions: int, seed: int) -> tuple[list, list]:
    if transactions < 8:
        raise ValueError("at least eight transactions required by the frozen window")
    rng = random.Random(seed)
    directed = [(0, 0), (1, 1), (0xffffffff, 0xffffffff), (0x80000000, 0xffffffff),
                (0x7fffffff, 0x7fffffff), (0x80000000, 2), (0xffffffff, 2),
                (0xaaaaaaaa, 0x55555555), (0x12345678, 0x9abcdef0),
                (0xffffffff, 0), (0, 0xffffffff), (0x80000001, 0x80000001),
                (0x10001, 0x10001), (0x55555555, 0x55555555),
                (0xffff, 0x10001), (0xdeadbeef, 0x10203041)]
    rows = [{"x0": 0, "x1": 0} for _ in range(RESET_CYCLES)]
    oracle_transactions = []
    for n in range(transactions):
        a, b = directed[n] if n < len(directed) else (rng.getrandbits(32), rng.getrandbits(32))
        rows.extend({"x0": a, "x1": b} for _ in range(PERIOD))
        oracle_transactions.append({"transaction": n, "start": RESET_CYCLES + n * PERIOD,
                                    "ready_edge": RESET_CYCLES + n * PERIOD + 35,
                                    "rs1": a, "rs2": b, "expected_low_product": (a * b) & 0xffffffff})
    return rows, oracle_transactions


def make_testbench(rows: list) -> str:
    names = ["dut." + name for _, _, name in WORD_SPECS]
    names += ["dut." + name for name in CONTROL_WIDTH]
    names += ["pcpi_rd"]
    fmt = " ".join(["%h"] * len(names))
    lines = []
    for T, row in enumerate(rows):
        resetn, valid, _ = protocol_at(T)
        lines.append(f"    resetn=1'b{resetn}; pcpi_valid=1'b{valid}; "
                     f"pcpi_rs1=32'h{row['x0']:08x}; pcpi_rs2=32'h{row['x1']:08x};")
        lines.append(f"    #4; sample({T},0); clk=1; #1; sample({T},1); #4; clk=0; #1;")
    return f'''`timescale 1ns/1ps
`default_nettype none
module c2_picorv32_mul_tb;
  reg clk=0, resetn=0, pcpi_valid=0;
  reg [31:0] pcpi_rs1=0, pcpi_rs2=0;
  wire pcpi_wr, pcpi_wait, pcpi_ready;
  wire [31:0] pcpi_rd;
  picorv32_pcpi_mul #(.STEPS_AT_ONCE(1),.CARRY_CHAIN(0)) dut(
    .clk(clk),.resetn(resetn),.pcpi_valid(pcpi_valid),.pcpi_insn(32'h{INSN:08x}),
    .pcpi_rs1(pcpi_rs1),.pcpi_rs2(pcpi_rs2),.pcpi_wr(pcpi_wr),.pcpi_rd(pcpi_rd),
    .pcpi_wait(pcpi_wait),.pcpi_ready(pcpi_ready));
  task sample;
    input integer cycle;
    input integer stage;
    begin
      $display("C2 %0d %0d {fmt}", cycle, stage,
        {', '.join(names)});
    end
  endtask
  initial begin
    #1;
{chr(10).join(lines)}
    $finish;
  end
endmodule
`default_nettype wire
'''


def prepare(args):
    source, out = args.source.resolve(), args.out.resolve()
    if out.exists() and any(out.iterdir()):
        raise ValueError("prepare refuses a nonempty output directory")
    identity, frozen = source_identity(source), core_identity()
    rows, transactions = stimulus(args.transactions, args.seed)
    out.mkdir(parents=True, exist_ok=True)
    window = list(range(ORIGIN, ORIGIN + WINDOW_LENGTH))
    equations, events = [], []
    write_enable = {word: [] for word in WORD_WIDTH}
    before, after = ({word: [] for word in WORD_WIDTH} for _ in range(2))
    cb, ca = ({name: [] for name in CONTROL_WIDTH} for _ in range(2))
    output = []
    state = {role: None for role, _, _ in WORD_SPECS}
    registers = {name: None for name in CONTROL_REGS}
    producers = {word: ["init", word, 0] for word in WORD_WIDTH}
    for T, inputs in enumerate(rows):
        resetn, valid, slot = protocol_at(T)
        old = controls(registers)
        updates = source_equations(T, old, resetn)
        pending_tags = dict(producers)
        sample = (T - ORIGIN) // PERIOD
        for role, _, _ in WORD_SPECS:
            word = ROLE_WORD[role]
            before[word].append(state[role])
            write_enable[word].append(int(role in updates))
            expression = updates.get(role, f"{word}_o_Q_{T}")
            target = word + " - i_D"
            equations.append({"T": T, "target": target, "smt2": expression})
            if role in updates:
                logical = logical_name(role, slot)
                events.append({"T": T, "target": target, "logical": logical, "sample": sample,
                               "kind": "compute", "width": WORD_WIDTH[word],
                               "operands": occurrences(expression, T, producers)})
                pending_tags[word] = ["op", logical, sample]
        expression = f"{ROLE_WORD['registered_result']}_o_Q_{T}"
        target = "top_module - y0"
        equations.append({"T": T, "target": target, "smt2": expression})
        events.append({"T": T, "target": target, "logical": f"observe_result_slot_{slot:02d}",
                       "sample": sample, "kind": "observation", "width": 32,
                       "operands": occurrences(expression, T, producers)})
        output.append(state["registered_result"])
        state, registers = reference_step(state, registers, inputs, resetn, valid)
        new_control = controls(registers)
        for name in CONTROL_WIDTH:
            cb[name].append(old[name])
            ca[name].append(new_control[name])
        for role, _, _ in WORD_SPECS:
            after[ROLE_WORD[role]].append(state[role])
        producers = pending_tags
        # Verify the protocol's source-model timing before any recovery call.
        expected_ready = int(resetn and slot == 35)
        if registers["pcpi_ready"] != expected_ready or registers["pcpi_wr"] != expected_ready:
            raise ValueError(f"48-clock timing prediction disagrees with source control at T={T}")
    compare_from = next(T for T in range(len(rows)) if all(
        before[word][T] is not None and after[word][T] is not None for word in WORD_WIDTH))
    if compare_from != 40:
        raise ValueError(f"initialization prediction changed: {compare_from}")
    window_set = set(window)
    recovery = {"equations": [row for row in equations if row["T"] in window_set],
                "meta": {"window": window, "widths": WORD_WIDTH,
                         "ports": {"in": INPUT_WIDTH, "out": OUTPUT_WIDTH},
                         "write_enable": write_enable}}
    oracle = {"period": PERIOD, "origin": ORIGIN, "events": events,
              "source_identity": identity, "reference_scope": SCOPE,
              "word_roles": {ROLE_WORD[r]: {"role": r, "width": w, "source": name}
                             for r, w, name in WORD_SPECS},
              "input_roles": {"x0": "pcpi_rs1", "x1": "pcpi_rs2"}}
    reference = {"schema": "C2_TRACE_V1", "reference_scope": SCOPE,
                 "inputs": {p: [row[p] for row in rows] for p in INPUT_WIDTH},
                 "before": before, "after": after, "outputs": {"y0": output},
                 "compare_from": compare_from, "initial_state": {w: None for w in WORD_WIDTH},
                 "controls": {"before": cb, "after": ca,
                              "resetn": [protocol_at(T)[0] for T in range(len(rows))],
                              "pcpi_valid": [protocol_at(T)[1] for T in range(len(rows))],
                              "pcpi_insn": INSN},
                 "functional_transactions": transactions}
    dump(out / "recovery.json", recovery)
    dump(out / "oracle.json", oracle)
    dump(out / "reference.json", reference)
    dump(out / "reference_equations.json", {"equations": equations})
    (out / "testbench.v").write_text(make_testbench(rows))
    artifacts = {name: sha(out / name) for name in
                 ("recovery.json", "oracle.json", "reference.json", "reference_equations.json", "testbench.v")}
    manifest = {"schema": "C2_PICORV32_MUL_ADAPTER_V1", "status": "PREPARED_NOT_VALIDATED",
                "source": str(source), "source_identity": identity,
                "adapter_sha256": sha(Path(__file__)), "protocol_sha256": sha(PROTOCOL),
                "recovery_code_sha256": frozen, "reference_scope": SCOPE,
                "seed": args.seed, "transactions": args.transactions, "cycles": len(rows),
                "period": PERIOD, "origin": ORIGIN, "window": window,
                "compare_from": compare_from, "artifacts": artifacts,
                "recovery_hash": artifacts["recovery.json"], "oracle_hash": artifacts["oracle.json"],
                "supplied_priors": ["five source numeric word boundaries and LSB-first order",
                                    "exact source word equations under fixed MUL protocol",
                                    "fixed reset/valid/insn/48-clock transaction protocol",
                                    "effective per-word writes including prefix and waiting assignments"],
                "timing_prediction": {"start": "4+48*n", "launch_slot": 2,
                                      "iteration_slots": [3, 34], "ready_after_slot": 35,
                                      "valid_slots": [0, 35], "first_known_before_cycle": 40},
                "input_schema_revision": "Window-only recovery equations; separate hashed all-cycle reference equations."}
    dump(out / "manifest.json", manifest)
    print(json.dumps({"status": manifest["status"], "out": str(out), "cycles": len(rows),
                      "words": len(WORD_WIDTH), "period": PERIOD, "compare_from": compare_from}))


def load_smt_compiler():
    path = Path(__file__).resolve().parents[1] / "invfold" / "smt2.py"
    spec = importlib.util.spec_from_file_location("c2_picorv32_frozen_smt", path)
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
    if sha(Path(__file__)) != manifest["adapter_sha256"] or sha(PROTOCOL) != manifest["protocol_sha256"]:
        raise ValueError("adapter/protocol changed since prepare")
    if core_identity() != manifest["recovery_code_sha256"]:
        raise ValueError("recovery core changed since prepare")
    for name, digest in manifest["artifacts"].items():
        if sha(out / name) != digest:
            raise ValueError("prepared artifact changed: " + name)
    reference = json.loads((out / "reference.json").read_text())
    recovery = json.loads((out / "recovery.json").read_text())
    equations = json.loads((out / "reference_equations.json").read_text())["equations"]
    window = set(recovery["meta"]["window"])
    if recovery["equations"] != [row for row in equations if row["T"] in window]:
        raise ValueError("recovery equations are not the exact reference window restriction")
    command = ["iverilog", "-g2012", "-s", "c2_picorv32_mul_tb", "-o", str(out / "simulation.vvp"),
               str(out / "testbench.v"), str(source / "picorv32.v")]
    receipt = {"status": "FAIL", "kind": "finite_original_RTL_exact_equation_and_integer_product_check",
               "compile_command": command, "FORMAL_defined": False,
               "source_identity": manifest["source_identity"], "reference_scope": SCOPE,
               "adapter_sha256": manifest["adapter_sha256"], "protocol_sha256": manifest["protocol_sha256"],
               "recovery_code_sha256": manifest["recovery_code_sha256"],
               "prepared_artifacts": manifest["artifacts"], "failures": []}
    try:
        compiled = subprocess.run(command, text=True, capture_output=True, timeout=120)
        (out / "compile.log").write_text(compiled.stdout + compiled.stderr)
        if compiled.returncode:
            raise RuntimeError(f"Icarus compilation returned {compiled.returncode}")
        simulated = subprocess.run(["vvp", str(out / "simulation.vvp")], text=True,
                                   capture_output=True, timeout=120)
        (out / "simulation.log").write_text(simulated.stdout + simulated.stderr)
        if simulated.returncode:
            raise RuntimeError(f"Icarus simulation returned {simulated.returncode}")
    except (subprocess.TimeoutExpired, RuntimeError) as error:
        receipt["failures"].append({"stage": "original_RTL", "error": str(error)})
        dump(out / "reference_receipt.json", receipt)
        raise
    ncycles = manifest["cycles"]
    actual = {"schema": "C2_TRACE_V1", "reference_scope": SCOPE,
              "inputs": reference["inputs"], "compare_from": reference["compare_from"],
              "initial_state": reference["initial_state"],
              "before": {w: [None] * ncycles for w in WORD_WIDTH},
              "after": {w: [None] * ncycles for w in WORD_WIDTH},
              "outputs": {"y0": [None] * ncycles}, "outputs_after": {"y0": [None] * ncycles},
              "controls": {"before": {c: [None] * ncycles for c in CONTROL_WIDTH},
                           "after": {c: [None] * ncycles for c in CONTROL_WIDTH},
                           "resetn": reference["controls"]["resetn"],
                           "pcpi_valid": reference["controls"]["pcpi_valid"], "pcpi_insn": INSN},
              "unknown_observations": []}
    seen, known_words, known_controls, unknown_words = set(), 0, 0, 0
    for line in simulated.stdout.splitlines():
        if not line.startswith("C2 "):
            continue
        fields = line.split()
        T, stage = int(fields[1]), int(fields[2])
        if not 0 <= T < ncycles or stage not in (0, 1) or (T, stage) in seen:
            raise ValueError("invalid/duplicate original trace row")
        seen.add((T, stage))
        tokens = fields[3:]
        if len(tokens) != len(WORD_SPECS) + len(CONTROL_WIDTH) + 1:
            raise ValueError("original trace row width mismatch")
        values = []
        for index, token in enumerate(tokens):
            if re.search("[xz]", token.lower()):
                values.append(None)
                actual["unknown_observations"].append({"T": T, "stage": stage, "column": index, "hex": token})
            else:
                values.append(int(token, 16))
        side = "before" if stage == 0 else "after"
        for index, word in enumerate(WORD_WIDTH):
            observed, expected = values[index], reference[side][word][T]
            actual[side][word][T] = observed
            unknown_words += int(observed is None)
            if observed != expected or (T >= actual["compare_from"] and observed is None):
                receipt["failures"].append({"T": T, "side": side, "word": word,
                                            "actual": observed, "reference": expected})
            elif observed is not None:
                known_words += 1
        for index, name in enumerate(CONTROL_WIDTH, len(WORD_SPECS)):
            observed, expected = values[index], reference["controls"][side][name][T]
            actual["controls"][side][name][T] = observed
            if observed != expected:
                receipt["failures"].append({"T": T, "side": side, "control": name,
                                            "actual": observed, "reference": expected})
            elif observed is not None:
                known_controls += 1
        observed = values[-1]
        expected = reference["before" if stage == 0 else "after"][ROLE_WORD["registered_result"]][T]
        actual["outputs" if stage == 0 else "outputs_after"]["y0"][T] = observed
        if observed != expected:
            receipt["failures"].append({"T": T, "side": side, "output": "y0",
                                        "actual": observed, "reference": expected})
    if len(seen) != ncycles * 2:
        receipt["failures"].append({"stage": "trace", "rows": len(seen), "expected": ncycles * 2})
    dump(out / "trace.json", actual)
    compiler, compiler_hash = load_smt_compiler()
    equation_checks, equation_skips = 0, 0
    for row in equations:
        T, target, text = row["T"], row["target"], row["smt2"]
        widths = {f"{w}_o_Q_{T}": width for w, width in WORD_WIDTH.items()}
        widths.update({f"{p}_{T}": width for p, width in INPUT_WIDTH.items()})
        function, symbols, width = compiler.compile_term(text, widths)
        env = {f"{w}_o_Q_{T}": actual["before"][w][T] for w in WORD_WIDTH}
        env.update({f"{p}_{T}": actual["inputs"][p][T] for p in INPUT_WIDTH})
        if any(env[s] is None for s in symbols):
            equation_skips += 1
            if T >= actual["compare_from"]:
                receipt["failures"].append({"T": T, "target": target, "reason": "unknown equation operand after initialization"})
            continue
        if target.startswith("top_module"):
            expected, expected_width = actual["outputs"]["y0"][T], 32
        else:
            word = target.split(" - ")[0]
            expected, expected_width = actual["after"][word][T], WORD_WIDTH[word]
        value = function(env)
        if value != expected or width != expected_width:
            receipt["failures"].append({"T": T, "target": target, "equation": value,
                                        "actual": expected, "width": width, "expected_width": expected_width})
        equation_checks += 1
    functional_checks = 0
    ready_cycles = [T for T, value in enumerate(actual["controls"]["after"]["pcpi_ready"]) if value == 1]
    expected_ready = [x["ready_edge"] for x in reference["functional_transactions"]]
    if ready_cycles != expected_ready:
        receipt["failures"].append({"stage": "ready_schedule", "actual": ready_cycles, "expected": expected_ready})
    for transaction in reference["functional_transactions"]:
        T = transaction["ready_edge"]
        observed = actual["after"][ROLE_WORD["registered_result"]][T]
        expected = (transaction["rs1"] * transaction["rs2"]) & 0xffffffff
        if observed != expected or actual["controls"]["after"]["pcpi_wr"][T] != 1:
            receipt["failures"].append({"stage": "independent_integer_product", "transaction": transaction["transaction"],
                                        "T": T, "actual": observed, "expected": expected})
        functional_checks += 1
    receipt.update({"status": "PASS" if not receipt["failures"] else "FAIL", "cycles": ncycles,
                    "words": len(WORD_WIDTH), "data_FF_bits": sum(WORD_WIDTH.values()),
                    "control_FF_bits_checked_separately": sum(CONTROL_REGS.values()),
                    "compare_from": actual["compare_from"], "known_word_comparisons": known_words,
                    "known_control_comparisons": known_controls, "unknown_word_observations": unknown_words,
                    "equation_checks": equation_checks, "equation_unknown_skips": equation_skips,
                    "integer_product_checks": functional_checks, "smt2_sha256": compiler_hash,
                    "trace_sha256": sha(out / "trace.json"), "timing_prediction": manifest["timing_prediction"],
                    "structural_recovery": "NOT_RUN; adapter agreement is not structural recovery PASS"})
    # Inputs and source must still be the exact frozen bytes after simulation.
    if source_identity(source) != manifest["source_identity"] or any(
            sha(out / name) != digest for name, digest in manifest["artifacts"].items()):
        raise ValueError("source/prepared inputs changed during original-RTL validation")
    dump(out / "reference_receipt.json", receipt)
    manifest["status"] = "REFERENCE_VALIDATED" if receipt["status"] == "PASS" else "REFERENCE_FAILED"
    dump(out / "manifest.json", manifest)
    print(json.dumps({k: receipt[k] for k in ("status", "cycles", "words", "compare_from",
                                             "equation_checks", "integer_product_checks")}))
    if receipt["status"] != "PASS":
        raise SystemExit("original-RTL/reference mismatch; see reference_receipt.json")


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    commands = parser.add_subparsers(dest="command", required=True)
    prep = commands.add_parser("prepare")
    prep.add_argument("--source", type=Path, default=DEFAULT_SOURCE)
    prep.add_argument("--out", type=Path, required=True)
    prep.add_argument("--transactions", type=int, default=64)
    prep.add_argument("--seed", type=int, default=20261102)
    prep.set_defaults(function=prepare)
    run = commands.add_parser("run-reference")
    run.add_argument("--out", type=Path, required=True)
    run.set_defaults(function=run_reference)
    args = parser.parse_args()
    args.function(args)


if __name__ == "__main__":
    main()
