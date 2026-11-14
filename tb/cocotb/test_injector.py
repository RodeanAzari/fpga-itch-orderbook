"""injector (ADR 0014) against model/injector.py.

Why these matter: the capacity measurement is "messages in the image / cycles", so the
injector must deliver exactly the model's packets (every byte, every packet end) and
never be the bottleneck itself: one beat per cycle when the feed is ready. Backpressure
must hold data (the decoder stalls on the book), and a reset must replay from the start
(each mode-2 config is a fresh run). At DATA_W 64 (ADR 0015) the image is the same
packets, 8 bytes per beat: every beat but a packet's last must be full (the deframer's
input contract) and the stream must take one cycle per ROM word.
"""

from __future__ import annotations

import os
import sys
from pathlib import Path

import cocotb
from cocotb.clock import Clock
from cocotb.triggers import ClockCycles, FallingEdge, ReadOnly, RisingEdge
from tb_common import get_rng

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))
from analysis.injector_image import image_payloads  # noqa: E402
from model.injector import rom_words  # noqa: E402

LANES = int(os.environ.get("DATA_W", "8")) // 8


async def collect(dut, rng, p_ready: float, limit: int) -> tuple[list[bytes], int, int]:
    """Drive m_tready randomly; return (payloads, cycle of first beat, of last beat).
    Checks each beat's tkeep: contiguous from lane 0, all lanes unless tlast."""
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
            data, keep = int(dut.m_tdata_o.value), int(dut.m_tkeep_o.value)
            tlast = int(dut.m_tlast_o.value)
            assert keep and keep & (keep + 1) == 0, f"tkeep {keep:#x}"
            assert tlast or keep == (1 << LANES) - 1, f"partial beat {keep:#x} without tlast"
            cur += data.to_bytes(LANES, "little")[: keep.bit_length()]
            if tlast:
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
    words = len(rom_words(want, lanes=LANES)) - (2 if LANES == 1 else 1)  # data words
    cocotb.start_soon(Clock(dut.clk_i, 8, unit="ns").start())
    await reset(dut)
    assert not int(dut.done_o.value)
    lat = 0  # header read: documented latency to the first valid beat
    while lat < 10:
        lat += 1
        await RisingEdge(dut.clk_i)
        await ReadOnly()  # the values this edge registered
        if int(dut.m_tvalid_o.value):
            break
    cocotb.log.info("first beat valid at rising edge %d with rst_i low", lat)
    assert lat == (5 if LANES == 1 else 4), f"first beat at edge {lat} after reset"
    got, first, last = await collect(dut, rng, 1.0, words + 100)
    assert got == want
    assert last - first + 1 == words, f"{words} words in {last - first + 1} cycles at full ready"
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
        got, _, _ = await collect(dut, rng, p_ready, int(words / p_ready * 2) + 1000)
        assert got == want, f"p_ready {p_ready}"
    cocotb.log.info(
        "%d payloads, %d bytes, %d beats: exact at full rate and backpressured", len(want), n, words
    )
