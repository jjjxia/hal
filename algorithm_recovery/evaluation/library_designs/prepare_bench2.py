#!/usr/bin/env python3
"""Prepare one frozen benchmark for the native pipeline (evaluation side only).

Anonymises the synthesized iCE40 netlist (ports p_i per bit, cells g_i, nets n_<id>),
writes the physical-pad stimulus for the frozen periodic protocol, the PAD_PROTOCOL_V1
input contract, and an evaluation contract (port names, transaction data) that never
enters recovery. Finally checks that the anonymous netlist reproduces the synthesized
netlist's outputs under the stimulus (Icarus with the Yosys iCE40 primitive models).

usage: prepare_bench2.py SPEC.json EMPTY_CASE_DIR
"""
import csv, hashlib, json, os, random, re, subprocess, sys

CELLS_SIM = "/usr/share/yosys/ice40/cells_sim.v"


def sha256(data):
    return hashlib.sha256(data if isinstance(data, bytes) else data.encode()).hexdigest()


def bit_expr(bit):
    if isinstance(bit, int):
        return f"n_{bit}"
    assert bit in ("0", "1", "x", "z"), bit
    return f"1'b{bit}"


def bus(bits):
    return bit_expr(bits[0]) if len(bits) == 1 else "{" + ",".join(bit_expr(b) for b in reversed(bits)) + "}"


def main(spec_path, out):
    spec = json.load(open(spec_path))
    os.makedirs(out, exist_ok=False)
    os.makedirs(f"{out}/input")
    raw_bytes = open(spec["synth"], "rb").read()
    raw = json.loads(raw_bytes)
    top = raw["modules"][spec["top"]]
    module = {"ports": {}, "cells": {}, "netnames": {}}
    ports, nets, inputs = [], set(), []
    for name, port in top["ports"].items():
        assert port["direction"] in ("input", "output"), name
        for offset, bit in enumerate(port["bits"]):
            anon = f"p_{len(ports)}"
            if isinstance(bit, int):
                nets.add(bit)
            if port["direction"] == "input":
                assert isinstance(bit, int)
                inputs.append(bit)
            module["ports"][anon] = {"direction": port["direction"], "bits": [bit]}
            ports.append({"name": name, "offset": offset, "direction": port["direction"], "physical_bit": bit, "anonymous_port": anon})
    assert len(set(inputs)) == len(inputs), "aliased input bits"
    cell_map = {}
    for i, (cname, cell) in enumerate(top["cells"].items()):
        assert cell["type"] in ("SB_LUT4", "SB_CARRY") or re.fullmatch(r"SB_DFF(N)?(E)?(R|S|SR|SS)?", cell["type"]), cell["type"]
        cell = {k: v for k, v in cell.items() if k not in ("attributes", "hide_name")}
        cell.setdefault("parameters", {})
        for bits in cell["connections"].values():
            nets.update(b for b in bits if isinstance(b, int))
        anon = f"g_{i}"
        cell_map[anon] = cname
        module["cells"][anon] = cell
    for net in top["netnames"].values():
        nets.update(b for b in net["bits"] if isinstance(b, int))
    for n in sorted(nets):
        module["netnames"][f"n_{n}"] = {"bits": [n]}
    for net in top["netnames"].values():
        init = net.get("attributes", {}).get("init")
        if init is None:
            continue
        assert len(init) == len(net["bits"])
        for j, b in enumerate(net["bits"]):
            if isinstance(b, int):
                module["netnames"][f"n_{b}"]["attributes"] = {"init": init[len(init) - 1 - j]}
    normalized = json.dumps({"modules": {"recovered": module}}, indent=2) + "\n"
    open(f"{out}/input/recovered.json", "w").write(normalized)
    source_sha = sha256(normalized)
    v = ["module recovered(" + ",".join(p["anonymous_port"] for p in ports) + ");"]
    v += [f"{p['direction']} wire {p['anonymous_port']};" for p in ports]
    v += [f"wire n_{n};" for n in sorted(nets)]
    for p in ports:
        v.append(f"assign {bit_expr(p['physical_bit'])}={p['anonymous_port']};" if p["direction"] == "input"
                 else f"assign {p['anonymous_port']}={bit_expr(p['physical_bit'])};")
    for name, cell in module["cells"].items():
        params = ",".join(f".{k}({len(val)}'b{val})" for k, val in cell["parameters"].items())
        conns = ",".join(f".{k}({bus(b)})" for k, b in cell["connections"].items())
        v.append(f"{cell['type']}{' #(' + params + ')' if params else ''} {name} ({conns});")
    v.append("endmodule")
    open(f"{out}/input/recovered.v", "w").write("\n".join(v) + "\n")

    # Stimulus: two events per cycle (clock low with new inputs, then clock high).
    rng = random.Random(spec["seed"])
    widths = {name: len(port["bits"]) for name, port in top["ports"].items() if port["direction"] == "input"}
    clock = spec["clock"]
    non_data = [clock] + spec["non_data"]
    data_ports = [n for n in widths if n not in non_data]
    schedule = {}
    if "schedule_csv" in spec:
        with open(spec["schedule_csv"]) as f:
            for row in csv.DictReader(f):
                schedule[int(row["cycle"])] = {k: int(row[k], 16) for k in spec["non_data"] if k in row}
    else:
        schedule = {int(k): v for k, v in spec["schedule"].items()}
    order = sorted(inputs)
    position = {b: i for i, b in enumerate(order)}
    events, cycles_meta = [], []

    def emit(values):
        for high in (0, 1):
            values[clock] = high
            row = [0] * len(order)
            for p in ports:
                if p["direction"] == "input":
                    row[position[p["physical_bit"]]] = (values[p["name"]] >> p["offset"]) & 1
            events.append(row)

    values = {n: 0 for n in widths}
    for block in spec["prefix"]:
        for _ in range(block["cycles"]):
            values.update(spec["defaults"]); values.update(block["values"])
            for d in data_ports:
                values[d] = 0
            emit(values)
    first_event = len(events)
    transactions = []
    for n in range(spec["transactions"]):
        held = {d: rng.getrandbits(widths[d]) for d in data_ports}
        txn = {"index": n, "first_event": len(events), "split": "training" if n < spec["training_transactions"] else "heldout", "data": []}
        for c in range(spec["period"]):
            values.update(spec["defaults"]); values.update(schedule.get(c, {}))
            for d in data_ports:
                values[d] = held[d] if spec["data_mode"] == "hold" else rng.getrandbits(widths[d])
            txn["data"].append({d: values[d] for d in data_ports})
            emit(values)
        transactions.append(txn)
    training_end = first_event + 2 * spec["period"] * spec["training_transactions"]
    stimulus = {"schema": "PHYSICAL_PAD_STIMULUS_V1", "source_sha256": source_sha, "input_bits": order, "events": events,
                "windows": {"training": list(range(first_event, training_end))},
                "description": f"{spec['case']}: frozen periodic protocol (period {spec['period']} cycles), random free data; "
                               f"first {spec['training_transactions']} transactions train, the rest are held out."}
    stim_text = json.dumps(stimulus) + "\n"
    open(f"{out}/input/stimulus.json", "w").write(stim_text)
    non_data_bits = sorted(p["physical_bit"] for p in ports if p["direction"] == "input" and p["name"] in non_data)
    protocol = {"schema": "PAD_PROTOCOL_V1", "source_sha256": source_sha, "non_data_inputs": non_data_bits,
                "description": f"{spec['case']}: non-data ports {non_data}; free data ports {data_ports}."}
    open(f"{out}/input/protocol.json", "w").write(json.dumps(protocol, indent=1) + "\n")
    contract = {"case": spec["case"], "spec_sha256": sha256(open(spec_path, "rb").read()), "synth_json_sha256": sha256(raw_bytes),
                "source_sha256": source_sha, "stimulus_sha256": sha256(stim_text), "port_bits": ports, "cell_map": cell_map,
                "period_cycles": spec["period"], "first_event": first_event, "training_end_exclusive": training_end,
                "events": len(events), "non_data_ports": non_data, "data_ports": data_ports, "transactions": transactions}
    open(f"{out}/input/evaluation_contract.json", "w").write(json.dumps(contract) + "\n")

    # Anonymous netlist must reproduce the synthesized netlist's outputs.
    subprocess.run(["yosys", "-q", "-p", f"read_json {spec['synth']}; hierarchy -top {spec['top']}; write_verilog -noattr -selected {out}/input/synth_reference.v"],
                   check=True)
    outs = [p for p in ports if p["direction"] == "output"]
    tb = ["`timescale 1ns/1ps", "module test;", f"reg [{len(order)-1}:0] stim[0:{len(events)-1}];", "integer i, known=0, mism=0, fd;"]
    for name, w in widths.items():
        tb.append(f"reg [{w-1}:0] r_{name};")
    for name, port in top["ports"].items():
        if port["direction"] == "output":
            tb.append(f"wire [{len(port['bits'])-1}:0] o_{name};")
    tb.append(f"wire [{len(outs)-1}:0] a, b;")
    tb.append(f"{spec["top"]} reference_inst(" + ",".join(f".{n}({'r_' if top['ports'][n]['direction']=='input' else 'o_'}{n})" for n in top["ports"]) + ");")
    tb.append("recovered dut(" + ",".join(f".{p['anonymous_port']}({'r_'+p['name']+'['+str(p['offset'])+']' if p['direction']=='input' else 'b['+str(outs.index(p))+']'})" for p in ports) + ");")
    for k, p in enumerate(outs):
        tb.append(f"assign a[{k}] = o_{p['name']}[{p['offset']}];")
    tb.append("initial begin")
    tb.append(f'$readmemb("{out}/input/stim.mem", stim);')
    tb.append(f"for (i=0;i<{len(events)};i=i+1) begin")
    for p in ports:
        if p["direction"] == "input":
            tb.append(f"r_{p['name']}[{p['offset']}] = stim[i][{position[p['physical_bit']]}];")
    tb.append("#1; for (fd=0; fd<" + str(len(outs)) + "; fd=fd+1) if (a[fd]===1'b0 || a[fd]===1'b1) begin known=known+1; if (a[fd]!==b[fd]) mism=mism+1; end #1; end")
    tb.append('$display("EQUIV known=%0d mismatches=%0d", known, mism); $finish; end endmodule')
    open(f"{out}/input/equiv_tb.v", "w").write("\n".join(tb) + "\n")
    with open(f"{out}/input/stim.mem", "w") as f:
        for row in events:
            f.write("".join(str(x) for x in reversed(row)) + "\n")
    subprocess.run(["iverilog", "-g2012", "-s", "test", "-o", f"{out}/input/equiv.vvp", f"{out}/input/equiv_tb.v",
                    f"{out}/input/synth_reference.v", f"{out}/input/recovered.v", CELLS_SIM], check=True,
                   stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    result = subprocess.run(["vvp", f"{out}/input/equiv.vvp"], check=True, capture_output=True, text=True).stdout
    m = re.search(r"EQUIV known=(\d+) mismatches=(\d+)", result)
    receipt = {"status": "ANONYMOUS_NETLIST_EQUIVALENT" if m and int(m.group(2)) == 0 and int(m.group(1)) > 0 else "FAILED",
               "known_output_observations": int(m.group(1)) if m else None, "mismatches": int(m.group(2)) if m else None,
               "events": len(events), "first_event": first_event, "training_end_exclusive": training_end}
    open(f"{out}/input/anonymization_equivalence.json", "w").write(json.dumps(receipt, indent=1) + "\n")
    print(json.dumps({"case": spec["case"], **receipt, "inputs": len(order), "non_data_bits": len(non_data_bits)}))
    if receipt["status"] != "ANONYMOUS_NETLIST_EQUIVALENT":
        sys.exit(1)


if __name__ == "__main__":
    main(sys.argv[1], sys.argv[2])
