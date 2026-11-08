"""cocotb tests for rtl/out/out_stats: the 40 stats counters and the 1 s tick.

Why these tests matter: the stats packet is how every capacity limit and error is made
visible on hardware ("never silently produce a wrong book"). A counter wired to the wrong
slot, an amount input added as +1, a config clear that also wipes the control counters
(the host could no longer tell its configs were accepted), or swapped stale bits would
all mislead the host. A reference model built from docs/protocol.md's table
(model.protocol.STATS_NAMES order) is compared with all 40 counters on every cycle under
random events on every input at once, random clears, and gap amounts up to 2^64 (wrap).
The tick must fire exactly every TICK_CYCLES cycles after a clear.
"""

from __future__ import annotations

import os

import cocotb
from cocotb.clock import Clock
from cocotb.triggers import ClockCycles, FallingEdge
from tb_common import get_rng

from model.protocol import N_STATS, STATS_NAMES

TICK = int(os.environ.get("TICK_CYCLES", "50"))
MASK = (1 << 64) - 1
VALUES = {"locate", "stale"}
KEEP = {"ctrl_ok", "ctrl_bad"}  # not cleared by a config
AMOUNT = {"mold_gap_messages": 64, "itch_malformed_messages": 2, "out_records": 6}
PULSES = [n for n in STATS_NAMES if n not in VALUES | {"uptime_cycles"} | set(AMOUNT)]


def unpack(v: int) -> list[int]:
    return [(v >> (64 * (N_STATS - 1 - k))) & MASK for k in range(N_STATS)]


async def run(dut, name: str, cycles: int, p_event: float, p_clear: float) -> None:
    rng = get_rng(name)
    cocotb.start_soon(Clock(dut.clk_i, 10, unit="ns").start())
    ins = {n: getattr(dut, f"{n}_i") for n in PULSES + list(AMOUNT)}
    ins |= {n: getattr(dut, n) for n in ("locate_valid_i", "locate_i", "feed_stale_i",
                                          "book_stale_i", "clear_i")}  # fmt: skip
    for s in ins.values():
        s.value = 0
    dut.rst_i.value = 1
    await ClockCycles(dut.clk_i, 3)
    await FallingEdge(dut.clk_i)
    dut.rst_i.value = 0
    model = dict.fromkeys(STATS_NAMES, 0)
    # one rising edge with reset low has passed before the first check
    model["uptime_cycles"] = 1
    since_clear = 1
    ticks = 0
    clears = 0
    for cyc in range(cycles):
        await FallingEdge(dut.clk_i)
        got = unpack(int(dut.stats_o.value))
        want = [model[n] & MASK for n in STATS_NAMES]
        if got != want:
            bad = [
                f"{n}: {g} want {w}"
                for n, g, w in zip(STATS_NAMES, got, want, strict=True)
                if g != w
            ]
            raise AssertionError(f"{name} cycle {cyc}: " + "; ".join(bad))
        tick = int(dut.tick_o.value)
        assert tick == (since_clear > 0 and since_clear % TICK == 0), f"cycle {cyc}: tick {tick}"
        ticks += tick
        # drive this cycle's inputs; the model applies them at the next rising edge
        clear = rng.random() < p_clear
        ev = {n: int(rng.random() < p_event) for n in PULSES}
        gap = rng.choice([1, rng.getrandbits(16), rng.getrandbits(64), MASK])
        mal = rng.randrange(4) if rng.random() < p_event else 0
        recs = rng.randrange(64) if rng.random() < p_event else 0
        lv, loc = rng.getrandbits(1), rng.getrandbits(16)
        fs, bs = rng.getrandbits(1), rng.getrandbits(1)
        for n, v in ev.items():
            ins[n].value = v
        ins["mold_gap_messages"].value = gap
        ins["itch_malformed_messages"].value = mal
        ins["out_records"].value = recs
        ins["locate_valid_i"].value, ins["locate_i"].value = lv, loc
        ins["feed_stale_i"].value, ins["book_stale_i"].value = fs, bs
        ins["clear_i"].value = int(clear)

        for n in STATS_NAMES:
            if n in VALUES:
                continue
            if clear and n not in KEEP:
                model[n] = 0
                continue
            if n == "uptime_cycles":
                model[n] += 1
            elif n == "mold_gap_messages":
                model[n] += gap * ev["mold_gaps"]
            elif n == "itch_malformed_messages":
                model[n] += mal
            elif n == "out_records":
                model[n] += recs
            else:
                model[n] += ev[n]
            model[n] &= MASK
        model["locate"] = lv << 16 | loc
        model["stale"] = bs << 1 | fs
        since_clear = 0 if clear else since_clear + 1
        clears += clear
    assert ticks > 0 and clears > 0, f"{name}: ticks {ticks}, clears {clears}"
    cocotb.log.info("%s: %d cycles, %d ticks, %d clears", name, cycles, ticks, clears)


@cocotb.test()
async def busy_with_clears(dut):
    """Events on every input at once, frequent clears."""
    await run(dut, "busy_with_clears", 6000, 0.5, 0.004)


@cocotb.test()
async def sparse(dut):
    """Rare events and rare clears: long stretches where the tick must keep its period."""
    await run(dut, "sparse", 6000, 0.02, 0.0005)
