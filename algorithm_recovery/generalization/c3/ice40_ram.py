"""iCE40 SB_RAM40_4K contents per trace event, reconstructed from the original netlist and an original-gate trace.

Every RAM port pin of the Index images is either observed in the trace or constant, so no logic is evaluated:
contents start from the INIT parameters and are stepped at every event with the observed pre-event pins
(read before write, MASK bit 1 preserves the stored bit), exactly as hal-recover's step_ram. Every
reconstructed read is compared with the observed RDATA after the edge; unknown (x/z) values make the affected
words unknown instead of guessing."""
import json

PORTS = ("RADDR", "WADDR", "WDATA", "MASK", "RE", "RCLKE", "WE", "WCLKE", "RCLK", "WCLK", "RDATA")


def load(recovered_json):
    """Return {cell_name: {"init": [256 words], "pins": {pin: [bit-id or '0'/'1']}}} (mode 0: 256 x 16)."""
    module = next(iter(json.load(open(recovered_json))["modules"].values()))
    rams = {}
    for name, cell in module["cells"].items():
        if cell["type"] != "SB_RAM40_4K":
            continue
        params = cell["parameters"]
        for mode in ("READ_MODE", "WRITE_MODE"):
            assert int(params.get(mode, "0"), 2) == 0, f"{name}: only 256x16 mode is supported"
        init = []
        for i in range(16):
            bits = params[f"INIT_{i:X}"]
            assert len(bits) == 256 and set(bits) <= {"0", "1"}, f"{name}: INIT_{i:X} is not a 256-bit binary string"
            value = int(bits, 2)
            init += [(value >> (16 * j)) & 0xFFFF for j in range(16)]
        rams[name] = {"init": init, "pins": {p: list(cell["connections"][p]) for p in PORTS}}
    return rams


class Trace:
    def __init__(self, directory):
        meta = json.load(open(f"{directory}/native_trace.json"))
        self.pos = {b: i for i, b in enumerate(meta["observed"])}
        self.rows = [line.split() for line in open(f"{directory}/native_trace.txt")]

    def char(self, bit, t, post=False):
        row = self.rows[t][1 if post else 0]
        return row[len(row) - 1 - self.pos[bit]]

    def bit(self, bit, t, post=False):
        c = self.char(bit, t, post)
        return int(c) if c in "01" else None


def _pin_value(trace, bits, t):
    value = 0
    for i, b in enumerate(bits):
        if isinstance(b, str):
            x = int(b) if b in "01" else None
        else:
            x = trace.bit(b, t)
        if x is None:
            return None
        value |= x << i
    return value


def replay(rams, trace, snapshot_events):
    """Step every RAM over the trace. Returns ({event: {cell: (contents list, read)}} before each requested
    event, statistics). Contents entries are None where unknown."""
    snapshots, wanted = {}, set(snapshot_events)
    state = {name: [list(r["init"]), None] for name, r in rams.items()}
    stats = {"read_edges": 0, "reads_checked": 0, "read_mismatches": 0, "unknown_reads": 0, "writes": 0, "unknown_writes": 0}
    for t in range(len(trace.rows)):
        if t in wanted:
            snapshots[t] = {n: (list(s[0]), s[1]) for n, s in state.items()}
        for name, r in rams.items():
            p, (data, read) = r["pins"], state[name]
            clk = {c: (_pin_value(trace, p[c], t), trace.bit(p[c][0], t, True) if not isinstance(p[c][0], str) else int(p[c][0]))
                   for c in ("RCLK", "WCLK")}
            redge = clk["RCLK"] == (0, 1)
            wedge = clk["WCLK"] == (0, 1)
            if redge and _pin_value(trace, p["RE"], t) == 1 and _pin_value(trace, p["RCLKE"], t) == 1:
                stats["read_edges"] += 1
                addr = _pin_value(trace, p["RADDR"], t)
                state[name][1] = None if addr is None else data[addr & 255]
                observed = _pin_value_post(trace, p["RDATA"], t)
                if state[name][1] is None or observed is None:
                    stats["unknown_reads"] += 1
                else:
                    stats["reads_checked"] += 1
                    stats["read_mismatches"] += state[name][1] != observed
            if wedge:
                we, wclke = _pin_value(trace, p["WE"], t), _pin_value(trace, p["WCLKE"], t)
                if we == 0 or wclke == 0:
                    continue
                addr, wdata, mask = (_pin_value(trace, p[x], t) for x in ("WADDR", "WDATA", "MASK"))
                if we is None or wclke is None or addr is None:
                    stats["unknown_writes"] += 1
                    if addr is None:
                        state[name][0] = [None] * 256
                    else:
                        data[addr & 255] = None
                    continue
                stats["writes"] += 1
                old = data[addr & 255]
                data[addr & 255] = None if old is None or wdata is None or mask is None else (old & mask) | (wdata & ~mask & 0xFFFF)
    return snapshots, stats


def _pin_value_post(trace, bits, t):
    value = 0
    for i, b in enumerate(bits):
        x = int(b) if isinstance(b, str) else trace.bit(b, t, True)
        if x is None:
            return None
        value |= x << i
    return value
