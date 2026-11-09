#!/usr/bin/env python3
"""E7: prepare a vendor iCE40 netlist (Synplify Pro output, flattened by Yosys against port-only cell stubs) for the
unchanged bench2 chain. Only buffers and constants are removed; no logic cell is touched.

usage: e7_lower.py FLAT_RAW.json TOP OUT.json [--split PORT:LO:HI:NEWNAME ...]
  SB_IO with PIN_TYPE 000001 (simple input, no register): D_IN_0 := PACKAGE_PIN
  SB_IO with PIN_TYPE 011001 (simple output, always enabled, no register): PACKAGE_PIN := D_OUT_0
  SB_GB_IO with PIN_TYPE 000001: GLOBAL_BUFFER_OUTPUT := PACKAGE_PIN (D_IN_0 must be unused)
  SB_GB: GLOBAL_BUFFER_OUTPUT := USER_SIGNAL_TO_GLOBAL_BUFFER
  GND / VCC: output := constant 0 / 1
--split renames bits LO..HI of an input port into a separate port NEWNAME (pads are bits; the netlist is unchanged).
Any other cell type, IO register mode or used tristate is rejected.
"""
import collections, json, sys

raw, top, out = sys.argv[1:4]
splits = [a.split(":") for a in sys.argv[sys.argv.index("--split") + 1:]] if "--split" in sys.argv else []
J = json.load(open(raw))
m = J["modules"][top]
cells = m["cells"]
sub = {}            # net bit -> replacement (net bit or "0"/"1")
out_port_sub = {}   # output port bit (PACKAGE_PIN) -> driving net bit
drop = set()
uses = collections.Counter()
for cn, c in cells.items():
    for p, bits in c["connections"].items():
        if c.get("port_directions", {}).get(p) == "input":
            uses.update(b for b in bits if isinstance(b, int))
for cn, c in cells.items():
    t, cc, par = c["type"], c["connections"], c.get("parameters", {})
    if t == "SB_IO":
        pt = par.get("PIN_TYPE")
        if pt == "000001":
            sub[cc["D_IN_0"][0]] = cc["PACKAGE_PIN"][0]
            assert uses[cc["D_IN_1"][0]] == 0, f"{cn}: D_IN_1 used"
        elif pt == "011001":
            out_port_sub[cc["PACKAGE_PIN"][0]] = cc["D_OUT_0"][0]
        else:
            raise SystemExit(f"{cn}: unsupported PIN_TYPE {pt}")
        drop.add(cn)
    elif t == "SB_GB_IO":
        assert par.get("PIN_TYPE") == "000001", cn
        assert uses[cc["D_IN_0"][0]] == 0 and uses[cc["D_IN_1"][0]] == 0, f"{cn}: D_IN used"
        sub[cc["GLOBAL_BUFFER_OUTPUT"][0]] = cc["PACKAGE_PIN"][0]
        drop.add(cn)
    elif t == "SB_GB":
        sub[cc["GLOBAL_BUFFER_OUTPUT"][0]] = cc["USER_SIGNAL_TO_GLOBAL_BUFFER"][0]
        drop.add(cn)
    elif t in ("GND", "VCC"):
        (y,) = [b for p, bs in cc.items() for b in bs]
        sub[y] = "0" if t == "GND" else "1"
        drop.add(cn)
    else:
        assert t in ("SB_LUT4", "SB_CARRY", "SB_DFFE", "SB_DFFER", "SB_DFFES", "SB_DFFR", "SB_DFF", "SB_DFFS",
                     "SB_DFFESR", "SB_DFFSR", "SB_DFFESS", "SB_DFFSS", "SB_DFFN"), f"{cn}: unsupported cell {t}"


def r(b):
    seen = 0
    while isinstance(b, int) and b in sub:
        b = sub[b]; seen += 1
        assert seen < 64, "substitution loop"
    return b


new_cells = {}
for cn, c in cells.items():
    if cn in drop:
        continue
    c = dict(c)
    c["connections"] = {p: [r(b) for b in bits] for p, bits in c["connections"].items()}
    new_cells[cn] = c
ports = {}
for pn, p in m["ports"].items():
    bits = p["bits"]
    if p["direction"] == "output":
        bits = [r(out_port_sub.get(b, b)) for b in bits]
    ports[pn] = {"direction": p["direction"], "bits": bits}
for pn, lo, hi, new in splits:
    lo, hi = int(lo), int(hi)
    b = ports[pn]["bits"]
    assert ports[pn]["direction"] == "input"
    ports[new] = {"direction": "input", "bits": b[lo:hi + 1]}
    ports[pn] = {"direction": "input", "bits": b[:lo] + b[hi + 1:]}
netnames = {}
for nn, n in m["netnames"].items():
    netnames[nn] = dict(n, bits=[r(b) for b in n["bits"]])
for pn, p in ports.items():   # a port's wire must carry exactly the port's bits
    netnames[pn] = dict(netnames.get(pn, {}), bits=p["bits"])
m2 = dict(m, cells=new_cells, ports=ports, netnames=netnames)
m2.setdefault("attributes", {})["top"] = "00000000000000000000000000000001"
json.dump({"creator": J.get("creator"), "modules": {top: m2}}, open(out, "w"))
print(json.dumps({"cells_in": len(cells), "cells_out": len(new_cells), "removed": dict(collections.Counter(cells[c]["type"] for c in drop)),
                  "types": dict(collections.Counter(c["type"] for c in new_cells.values())),
                  "ports": {k: (v["direction"], len(v["bits"])) for k, v in ports.items()}}))
