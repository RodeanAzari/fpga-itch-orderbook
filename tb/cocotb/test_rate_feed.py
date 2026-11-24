"""make capacity-sim (feed half): feed_frontend alone at DATA_W 64 (deframer, aligner,
decoder; ADR 0015), the cycles it takes to turn packets into book records. Not part of
`make sim`.

Packets go in back to back (a beat offered every cycle) and every record is taken at once,
so the span from the first input beat to the last record is the front end's own time.
Every record must equal model.moldudp64.FeedDecoder's (type, sequence, fields), so a rate
is only written for a correct run.

Inputs: the injector image's packets (the board chain's 1,043 messages) and, when the
off-repo extract exists (ITCH_EXTRACT), the real AAPL day packetized as host/replay sends
it (session 20191018AA, 1,472-byte payloads).

$RATE_CSV gets one row per input, in test_rate_book's columns: stage "feed", type "all",
records, cycles = last record's cycle - first input beat's cycle + 1. The log also gives
the cycles the input waited (tready low).
"""

from __future__ import annotations

import os

import cocotb
from cocotb.clock import Clock
from cocotb.triggers import ClockCycles, RisingEdge
from tb_common.axis import AxisSource, bytes_to_beats
from test_feed import TYPE_CLASS, model_fields
from test_rate_book import EXTRACT, append_rows

from analysis.injector_image import SESSION, image
from model.itch import iter_raw
from model.moldudp64 import FeedDecoder, packetize

LANES = 8


async def run(dut, name: str, pkts: list[bytes]) -> None:
    assert int(os.environ.get("DATA_W", "8")) == 8 * LANES
    cocotb.start_soon(Clock(dut.clk_i, 8, unit="ns").start())
    dut.cfg_symbol_i.value = int.from_bytes(b"AAPL    ", "big")
    dut.cfg_locate_valid_i.value = 0
    dut.cfg_locate_i.value = 0
    dut.s_t0_i.value = 0
    dut.s_stale_i.value = 0
    dut.now_i.value = 0
    dut.m_ready_i.value = 1
    dut.rst_i.value = 1
    await ClockCycles(dut.clk_i, 3)
    dut.rst_i.value = 0

    model = FeedDecoder("AAPL")
    want = [model_fields(r) for p in pkts for r in model.feed(p)]
    got: list[tuple] = []
    t = {"first": -1, "last": -1, "waits": 0}

    async def monitor() -> None:
        cycle = 0
        while True:
            await RisingEdge(dut.clk_i)
            cycle += 1
            if int(dut.s_tvalid_i.value):
                if int(dut.s_tready_o.value):
                    t["first"] = cycle if t["first"] < 0 else t["first"]
                else:
                    t["waits"] += 1
            if int(dut.m_valid_o.value) and int(dut.m_ready_i.value):
                t["last"] = cycle
                got.append(
                    (
                        TYPE_CLASS[int(dut.m_type_o.value)],
                        int(dut.m_seq_o.value),
                        int(dut.m_ts_o.value),
                        int(dut.m_ref_o.value),
                        int(dut.m_new_ref_o.value),
                        int(dut.m_sell_o.value),
                        int(dut.m_shares_o.value),
                        int(dut.m_price_o.value),
                    )
                )

    cocotb.start_soon(monitor())
    src = AxisSource(dut, dut.clk_i, "s_", p_valid=1.0)
    await src.send(b for p in pkts for b in bytes_to_beats(p, LANES, user=0))
    for _ in range(1000):
        if len(got) >= len(want):
            break
        await ClockCycles(dut.clk_i, 10)
    await ClockCycles(dut.clk_i, 100)  # an extra record would show up here
    assert got == want, f"{name}: {len(got)} records, model {len(want)}"
    assert not int(dut.stale_o.value) and not model.stale
    cycles = t["last"] - t["first"] + 1
    cocotb.log.info(
        "%s: %d packets, %d payload bytes, input waited %d cycles",
        name, len(pkts), sum(map(len, pkts)), t["waits"],
    )  # fmt: skip
    append_rows([{"stage": "feed", "input": name, "type": "all", "records": len(got),
                  "cycles": cycles}])  # fmt: skip


@cocotb.test()
async def injector_image(dut):
    """The injector image's packets (the board chain's 1,043 messages)."""
    await run(dut, "injector_image", image().pkts)


@cocotb.test(skip=not EXTRACT.exists())
async def full_day(dut):
    """The real AAPL day, packetized as host/replay sends it."""
    with EXTRACT.open("rb") as f:
        pkts = list(packetize(iter_raw(f), SESSION))
    await run(dut, f"full_day {EXTRACT.stem}", pkts)
