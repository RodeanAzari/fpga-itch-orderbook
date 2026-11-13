"""injector (ADR 0014) against model/injector.py.

Why these matter: the capacity measurement is "messages in the image / cycles", so the
injector must deliver exactly the model's packets (every byte, every packet end) and
never be the bottleneck itself: one beat per cycle when the feed is ready. Backpressure
must hold data (the decoder stalls on the book), and a reset must replay from the start
(each mode-2 config is a fresh run).
"""

from __future__ import annotations

import sys
from pathlib import Path

import cocotb
from cocotb.clock import Clock
from cocotb.triggers import ClockCycles, FallingEdge, RisingEdge
from tb_common import get_rng

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))
from analysis.injector_image import image_payloads  # noqa: E402


async def collect(dut, rng, p_ready: float, limit: int) -> tuple[list[bytes], int, int]:
    """Drive m_tready randomly; return (payloads, cycle of first beat, of last beat)."""
    pkts: list[bytes] = []
    cur = bytearray()
    first = last = -1
    for cyc in range(limit):
        await FallingEdge(dut.clk_i)
        dut.m_tready_i.value = int(rng.random() < p_ready)
        await RisingEdge(dut.clk_i)
        if int(dut.m_tvalid_o.value) and int(dut.m_tready_i.value):
            first = cyc if first < 0 else first
            last = cyc
            cur.append(int(dut.m_tdata_o.value))
            if int(dut.m_tlast_o.value):
                pkts.append(bytes(cur))
                cur.clear()
        if int(dut.done_o.value):
            assert not cur, "done with a payload unfinished"
            return pkts, first, last
    raise AssertionError(f"not done after {limit} cycles, {len(pkts)} payloads")


async def reset(dut) -> None:
    dut.rst_i.value = 1
    dut.m_tready_i.value = 0
    await ClockCycles(dut.clk_i, 3)
    dut.rst_i.value = 0


@cocotb.test()
async def full_rate_then_backpressure_then_replay(dut):
    rng = get_rng("injector")
    want = image_payloads()
    n = sum(map(len, want))
    cocotb.start_soon(Clock(dut.clk_i, 8, unit="ns").start())
    await reset(dut)
    assert not int(dut.done_o.value)
    got, first, last = await collect(dut, rng, 1.0, n + 100)
    assert got == want
    assert last - first + 1 == n, f"{n} bytes in {last - first + 1} cycles at full ready"
    # done stays; nothing more comes out
    for _ in range(20):
        await RisingEdge(dut.clk_i)
        assert int(dut.done_o.value) and not int(dut.m_tvalid_o.value)
    # a reset mid-stream restarts from the first byte
    await reset(dut)
    dut.m_tready_i.value = 1
    await ClockCycles(dut.clk_i, 700)
    for p_ready in (0.5, 0.05):  # replay after reset, under random backpressure
        await reset(dut)
        got, _, _ = await collect(dut, rng, p_ready, int(n / p_ready * 2) + 1000)
        assert got == want, f"p_ready {p_ready}"
    cocotb.log.info("%d payloads, %d bytes: exact at full rate and backpressured", len(want), n)
