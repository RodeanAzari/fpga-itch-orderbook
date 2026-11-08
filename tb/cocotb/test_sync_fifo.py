"""cocotb tests for rtl/lib/sync_fifo (FWFT, block RAM, one clock).

Why these tests matter: out_builder's record bytes pass through this FIFO, and a FIFO
bug (a stale word read from a just-written address, an off-by-one in full, a lost
pointer wrap) corrupts or drops records silently. out_builder never reads within two
cycles of a write, so it cannot exercise the read-after-write corner: this test does.

A reference queue is checked every cycle: rd_data_o equals the oldest entry whenever
empty_o is low; empty_o is low exactly when an entry written >= 2 cycles ago is present
(the module header's visibility latency); full_o is high exactly at DEPTH accepted,
unpopped entries; flush_i empties it (dropping a write in the same cycle). Inputs are
driven on the falling edge from settled outputs; random rates include push and pop on
every cycle.
"""

from __future__ import annotations

import os
from collections import deque

import cocotb
from cocotb.clock import Clock
from cocotb.triggers import ClockCycles, FallingEdge
from tb_common import get_rng

ADDR_W = int(os.environ.get("ADDR_W", "4"))
DEPTH = 1 << ADDR_W


async def run(
    dut, name: str, cycles: int, p_wr: float, p_rd: float, p_flush: float, need: tuple[str, ...]
) -> None:
    rng = get_rng(name)
    cocotb.start_soon(Clock(dut.clk_i, 10, unit="ns").start())
    dut.rst_i.value = 1
    dut.flush_i.value = 0
    dut.wr_en_i.value = 0
    dut.rd_en_i.value = 0
    dut.wr_data_i.value = 0
    await ClockCycles(dut.clk_i, 3)
    dut.rst_i.value = 0
    q: deque[tuple[int, int]] = deque()  # (cycle written, data)
    stats = {"full": 0, "pops": 0, "pop_at_vis": 0, "flush": 0}
    for cyc in range(cycles):
        await FallingEdge(dut.clk_i)
        full, empty = int(dut.full_o.value), int(dut.empty_o.value)
        visible = bool(q) and q[0][0] <= cyc - 2
        assert empty == (not visible), f"cycle {cyc}: empty_o {empty}, model {list(q)[:2]}"
        assert full == (len(q) == DEPTH), f"cycle {cyc}: full_o {full}, {len(q)} entries"
        if not empty:
            assert int(dut.rd_data_o.value) == q[0][1], f"cycle {cyc}: data"
        stats["full"] += full
        flush = rng.random() < p_flush
        wr = not full and rng.random() < p_wr
        rd = not empty and rng.random() < p_rd
        data = rng.getrandbits(8)
        dut.flush_i.value, dut.wr_en_i.value, dut.rd_en_i.value = int(flush), int(wr), int(rd)
        dut.wr_data_i.value = data
        if flush:
            q.clear()
            stats["flush"] += 1
            continue
        if rd:
            stats["pop_at_vis"] += q[0][0] == cyc - 2
            q.popleft()
            stats["pops"] += 1
        if wr:
            q.append((cyc, data))
    for k in need:
        assert stats[k] > 0, f"{name}: never hit {k}"
    cocotb.log.info("%s: %s", name, stats)


@cocotb.test()
async def streaming(dut):
    """Push and pop on every cycle: pops right as entries become visible."""
    await run(dut, "streaming", 4000, 1.0, 1.0, 0.0, ("pop_at_vis",))


@cocotb.test()
async def random_rates(dut):
    """Random push/pop rates around balance: fills, drains and wraps repeatedly."""
    await run(dut, "random_rates", 20000, 0.5, 0.45, 0.0, ("full", "pop_at_vis"))


@cocotb.test()
async def fill_bias_with_flush(dut):
    """Mostly full, with occasional flushes (one may coincide with a write)."""
    await run(dut, "fill_bias_with_flush", 20000, 0.8, 0.3, 0.002, ("full", "flush"))
