#!/usr/bin/env python3
"""Replace concatenations in the port connections of legup_mult_1 instances by named wires (circuit unchanged).

Input port `.p({a, b, ...})` -> `.p(inst__p)` with `assign inst__p = {a, b, ...};`.
Output port `.result({a, b, ...})` -> `.result(inst__result)` with per-bit `assign a = inst__result[k];`.
usage: name_instance_args.py IN.v OUT.v"""
import re, sys

OUTPUTS = {"result"}
src = open(sys.argv[1]).read()
decls, assigns = [], []


def fix_instance(m):
    inst, body = m.group(1), m.group(2)

    def fix_conn(c):
        port, items = c.group(1), [x.strip() for x in c.group(2).split(",")]
        w, name = len(items), f"{inst}__{port}"
        decls.append(f"  wire [{w-1}:0] {name};")
        if port in OUTPUTS:
            for k, bit in enumerate(reversed(items)):
                assigns.append(f"  assign {bit} = {name}[{k}];")
        else:
            assigns.append(f"  assign {name} = {{{', '.join(items)}}};")
        return f".{port}({name})"

    return m.group(0).replace(body, re.sub(r"\.(\w+)\(\{([^}]*)\}\)", fix_conn, body))


src = re.sub(r"\)\s+(\w+)\s*\((\s*\.[^;]*?)\);", fix_instance, src, flags=re.S)
src = re.sub(r"(\);\n)", r"\1" + "\n".join(decls) + "\n", src, count=1)
src = src.replace("endmodule", "\n".join(assigns) + "\nendmodule", 1)
open(sys.argv[2], "w").write(src)
print(len(decls), "connections named")
