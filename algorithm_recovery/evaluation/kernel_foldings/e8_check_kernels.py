#!/usr/bin/env python3
"""E8: every kernel's DFG evaluation must equal its C code (gcc) on random sample streams; prints sizes.
usage: e8_check_kernels.py OUT_DIR"""
import json, os, random, subprocess, sys
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import kernels as K

out = sys.argv[1]
os.makedirs(out, exist_ok=True)
res = {}
for name, f in K.KERNELS.items():
    kern = f()
    rng = random.Random(1)
    S = 300
    samples = [{i: rng.getrandbits(K.W) for i in kern["inputs"]} for _ in range(S)]
    ref = K.evaluate(kern, samples)
    c = K.to_c(name, kern)
    main = ["#include <stdio.h>", c, "int main(void) {", f"  unsigned v[{len(kern['inputs'])}];",
            "  " + " ".join(f"uint16_t o_{o};" for o in kern["outputs"]),
            f"  while (scanf(\"" + " ".join(["%u"] * len(kern["inputs"])) + "\", " + ", ".join(f"&v[{j}]" for j in range(len(kern["inputs"]))) + f") == {len(kern['inputs'])}) {{",
            f"    {name}(" + ", ".join([f"(uint16_t)v[{j}]" for j in range(len(kern["inputs"]))] + [f"&o_{o}" for o in kern["outputs"]]) + ");",
            "    printf(\"" + " ".join(["%u"] * len(kern["outputs"])) + "\\n\", " + ", ".join(f"o_{o}" for o in kern["outputs"]) + ");",
            "  }", "  return 0;", "}"]
    open(f"{out}/{name}.c", "w").write(c)
    open(f"{out}/{name}_main.c", "w").write("\n".join(main) + "\n")
    subprocess.run(["gcc", "-O1", "-o", f"{out}/{name}_main", f"{out}/{name}_main.c"], check=True)
    inp = "\n".join(" ".join(str(s[i]) for i in kern["inputs"]) for s in samples) + "\n"
    got = subprocess.run([f"{out}/{name}_main"], input=inp, capture_output=True, text=True, check=True).stdout.split("\n")
    exp = [" ".join(str(ref[n][v]) for v in kern["outputs"].values()) for n in range(S)]
    bad = sum(1 for a, b in zip(got, exp) if a != b)
    ops = {}
    for nd in kern["nodes"].values():
        ops[nd["op"]] = ops.get(nd["op"], 0) + 1
    delayed = sum(1 for nd in kern["nodes"].values() for s, d in nd["operands"] if d > 0)
    res[name] = {"nodes": len(kern["nodes"]), "ops": ops, "inputs": len(kern["inputs"]), "outputs": len(kern["outputs"]),
                 "delayed_operands": delayed, "samples": S, "c_mismatches": bad}
    print(name, json.dumps(res[name]))
json.dump(res, open(f"{out}/kernels_check.json", "w"), indent=1)
sys.exit(1 if any(r["c_mismatches"] for r in res.values()) else 0)
