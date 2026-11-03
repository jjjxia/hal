#!/usr/bin/env python3
"""Rewrite bit-select left-hand sides on multi-bit output ports (Yosys write_verilog output) into per-bit signals.

`assign P[i] = e;` -> `assign P__b_i = e;`, `P[i] <= e;` -> `P__b_i <= e;`, plus `assign P = {P__b_(w-1), ..., P__b_0};`.
Per-bit signals are `reg` if assigned in an always block, else `wire`; a port-level `reg [..] P;` becomes a wire.
Bits that no statement drives keep a constant 1'bx in the concatenation (none expected). The circuit is unchanged.
usage: split_port_bits.py IN.v OUT.v"""
import re, sys

src = open(sys.argv[1]).read()
outs = {m.group(2): int(m.group(1)) + 1 for m in re.finditer(r"^\s*output\s*\[(\d+):0\]\s*(\w+)\s*;", src, re.M)}
lhs_assign = re.compile(r"(\bassign\s+)(\w+)\[(\d+)\](\s*=)")
lhs_nb = re.compile(r"(?<![\w.])(\w+)\[(\d+)\](\s*<=)")
driven, regbits = {}, set()


def sub_assign(m):
    p, i = m.group(2), int(m.group(3))
    if p not in outs:
        return m.group(0)
    driven.setdefault(p, set()).add(i)
    return f"{m.group(1)}{p}__b_{i}{m.group(4)}"


def sub_nb(m):
    p, i = m.group(1), int(m.group(2))
    if p not in outs:
        return m.group(0)
    driven.setdefault(p, set()).add(i)
    regbits.add((p, i))
    return f"{p}__b_{i}{m.group(3)}"


src = lhs_assign.sub(sub_assign, src)
src = lhs_nb.sub(sub_nb, src)
decls, tail = [], []
for p, bits in sorted(driven.items()):
    w = outs[p]
    src = re.sub(rf"^\s*reg\s*\[{w-1}:0\]\s*{p}\s*;\s*$", "", src, flags=re.M)
    for i in range(w):
        if i in bits:
            decls.append(f"  {'reg' if (p, i) in regbits else 'wire'} {p}__b_{i};")
    parts = [f"{p}__b_{i}" if i in bits else "1'bx" for i in reversed(range(w))]
    tail.append(f"  assign {p} = {{{', '.join(parts)}}};")
src = re.sub(r"(\);\n)", r"\1" + "\n".join(decls).replace("\\", "\\\\") + "\n", src, count=1)
src = src.replace("endmodule", "\n".join(tail) + "\nendmodule", 1)
open(sys.argv[2], "w").write(src)
print({p: (outs[p], len(b)) for p, b in driven.items()})
