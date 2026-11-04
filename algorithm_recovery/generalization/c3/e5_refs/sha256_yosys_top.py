#!/usr/bin/env python3
"""E7 evaluation-side reference for case sha256_yosys_top (paired Yosys netlist of the same top; same protocol as sha256_lattice).
FIPS 180-4 compression of the 16 written block words from H(0), as c3/e5_refs/sha256_sw.py (documented algorithm).
Protocol (e7/E7_VENDOR_PROTOCOL.txt): block word j is written in cycle j; the data port write_data carries bits 31..3
(write_data_ctl = bits 2..0 is a protocol port, 0 for block writes), so word j = write_data << 3; digest word i is read
on read_data in cycle 84 + i (observe.json), i.e. the i-th observation of read_data."""
import importlib.util, os
_s = importlib.util.spec_from_file_location("sha256_sw_ref", os.path.join(os.path.dirname(os.path.abspath(__file__)), "sha256_sw.py"))
_m = importlib.util.module_from_spec(_s); _s.loader.exec_module(_m)
M29 = (1 << 29) - 1


def trial(rng, contract):
    hi = [rng.getrandbits(29) for _ in range(16)]
    per_cycle = {j: {"write_data": hi[j]} for j in range(16)}
    digest = _m.sha256_compress([(h << 3) & _m.M32 for h in hi])
    return per_cycle, {("read_data", i): digest[i] for i in range(8)}
