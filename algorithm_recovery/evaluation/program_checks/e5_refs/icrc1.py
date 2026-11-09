"""Evaluation-side reference for case `icrc1` (Python stdlib only).

Documented algorithm: icrc1 from PandA-bambu examples/crc/spec.c (PowerStone crc benchmark; Numerical Recipes in C
"icrc1"): one byte of the bitwise MSB-first CRC with the CCITT polynomial x^16 + x^12 + x^5 + 1 (0x1021):
    ans = crc ^ (onech << 8)           (16-bit unsigned short)
    repeat 8 times: ans = (ans << 1) ^ 0x1021 if ans & 0x8000 else ans << 1   (truncated to 16 bits)
Self test: applied byte by byte from crc = 0 it gives CRC-16/XMODEM("123456789") = 0x31C3.
No deviation from the C function is modelled. Bambu's minimal interface: crc and onech are held for the whole
transaction (data_mode "hold"); the generated FSM samples them only in the start state (icrc1.v S_0).
Timing (measured): start_port in cycle 0; 8 loop states in cycles 1..8; done_port = 1 and return_port = result in
cycle 9 (FSM state S_2); a new start is accepted again in cycle 10 -> period 10.
"""
PERIOD = 10


def icrc1(crc, onech):
    ans = (crc ^ (onech << 8)) & 0xFFFF
    for _ in range(8):
        ans = ((ans << 1) ^ 4129) & 0xFFFF if ans & 0x8000 else (ans << 1) & 0xFFFF
    return ans


def _self_test():
    c = 0
    for ch in b"123456789":
        c = icrc1(c, ch)
    assert c == 0x31C3, hex(c)


_self_test()


def expect(per_cycle):
    d = per_cycle[0]
    return {"return_port": icrc1(d["crc"], d["onech"])}


def trial(rng, contract):
    crc, onech = rng.getrandbits(16), rng.getrandbits(8)
    period = contract.get("period_cycles", PERIOD)
    per_cycle = {c: {"crc": crc, "onech": onech} for c in range(period)}  # data_mode "hold"
    return per_cycle, expect(per_cycle)
