"""make wave-feed: one real MoldUDP64 packet through feed_frontend at DATA_W 64, cycle by
cycle, for the Phase C waveform (buildplan §9: one packet splitting into messages across
bus words). Not part of `make sim`.

The packets are the first four of the 2019-10-18 AAPL day as `host/replay` sends them
(session 20191018AA, 1,472-byte payloads; the same bytes as the board's ILA capture,
data/results/2026-11-21_ila_frame_board.md), from the committed fixture
data/samples/2019-10-18_AAPL_head50k.itch. All four go in back to back with no stalls;
the R message in the first teaches the locate. Every record of all four must equal
model.moldudp64.FeedDecoder's (type, sequence, fields) and the counters must agree, so the
waveform is of a correct run.

$FEED_WAVE_CSV gets one row per cycle from the fourth packet's first beat to its last
record: the input word, the deframer's output (messages start in any lane), the aligner's
(every message starts in lane 0), and the decoder's record handshake.
"""

from __future__ import annotations

import csv
import itertools
import os
from pathlib import Path

import cocotb
from cocotb.clock import Clock
from cocotb.triggers import ClockCycles, RisingEdge
from tb_common.axis import AxisSource, bytes_to_beats
from test_feed import TYPE_CLASS, model_fields

from model.itch import iter_raw
from model.moldudp64 import FeedDecoder, packetize

ROOT = Path(__file__).resolve().parents[2]
FIXTURE = ROOT / "data/samples/2019-10-18_AAPL_head50k.itch"
SESSION = b"20191018AA"
SHOWN = 3  # packet index in the CSV: MoldUDP64 sequence 143, 47 messages
LANES = 8
COLS = (
    "cycle s_tvalid s_tready s_tkeep s_tdata s_tlast "
    "g_tvalid g_tready g_tkeep g_tdata g_tlast g_tuser g_seq "
    "a_tvalid a_tready a_tkeep a_tdata a_tlast a_tuser a_seq "
    "m_valid m_ready m_type m_seq"
).split()


def packets() -> list[bytes]:
    msgs = itertools.islice(iter_raw(FIXTURE.open("rb")), 400)
    return list(itertools.islice(packetize(msgs, SESSION), SHOWN + 1))


@cocotb.test()
async def one_packet(dut):
    assert int(os.environ.get("DATA_W", "8")) == 8 * LANES
    pkts = packets()
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
    shown_seq = int.from_bytes(pkts[SHOWN][10:18])
    shown_n = int.from_bytes(pkts[SHOWN][18:20])

    got: list[tuple] = []
    rows: list[dict[str, int | str]] = []
    pkt_i = 0
    first_beat = True
    g, a = dut.u_deframer, dut.g_align.u_aligner

    def sig(h) -> int:
        return int(h.value)

    async def monitor():
        nonlocal pkt_i, first_beat
        cycle = 0
        recording = False
        while True:
            await RisingEdge(dut.clk_i)
            cycle += 1
            s_fire = sig(dut.s_tvalid_i) and sig(dut.s_tready_o)
            if s_fire and first_beat and pkt_i == SHOWN:
                recording = True
            if recording:
                rows.append(
                    {
                        "cycle": cycle,
                        "s_tvalid": sig(dut.s_tvalid_i),
                        "s_tready": sig(dut.s_tready_o),
                        "s_tkeep": f"{sig(dut.s_tkeep_i):02x}",
                        "s_tdata": f"{sig(dut.s_tdata_i):016x}",
                        "s_tlast": sig(dut.s_tlast_i),
                        "g_tvalid": sig(g.m_tvalid_o),
                        "g_tready": sig(g.m_tready_i),
                        "g_tkeep": f"{sig(g.m_tkeep_o):02x}",
                        "g_tdata": f"{sig(g.m_tdata_o):016x}",
                        "g_tlast": sig(g.m_tlast_o),
                        "g_tuser": sig(g.m_tuser_o),
                        "g_seq": sig(g.m_seq_o),
                        "a_tvalid": sig(a.m_tvalid_o),
                        "a_tready": sig(a.m_tready_i),
                        "a_tkeep": f"{sig(a.m_tkeep_o):02x}",
                        "a_tdata": f"{sig(a.m_tdata_o):016x}",
                        "a_tlast": sig(a.m_tlast_o),
                        "a_tuser": sig(a.m_tuser_o),
                        "a_seq": sig(a.m_seq_o),
                        "m_valid": sig(dut.m_valid_o),
                        "m_ready": sig(dut.m_ready_i),
                        "m_type": f"{sig(dut.m_type_o):c}" if sig(dut.m_valid_o) else "",
                        "m_seq": sig(dut.m_seq_o) if sig(dut.m_valid_o) else "",
                    }
                )
            if s_fire:
                first_beat = bool(sig(dut.s_tlast_i))
                pkt_i += first_beat
            if sig(dut.m_valid_o) and sig(dut.m_ready_i):
                got.append(
                    (
                        TYPE_CLASS[sig(dut.m_type_o)],
                        sig(dut.m_seq_o),
                        sig(dut.m_ts_o),
                        sig(dut.m_ref_o),
                        sig(dut.m_new_ref_o),
                        sig(dut.m_sell_o),
                        sig(dut.m_shares_o),
                        sig(dut.m_price_o),
                    )
                )
                if recording and sig(dut.m_seq_o) == shown_seq + shown_n - 1:
                    recording = False  # the packet's last message: the window ends here

    cocotb.start_soon(monitor())
    src = AxisSource(dut, dut.clk_i, "s_", p_valid=1.0)
    await src.send(b for p in pkts for b in bytes_to_beats(p, LANES, user=0))
    for _ in range(100):
        if len(got) >= len(want):
            break
        await ClockCycles(dut.clk_i, 10)
    await ClockCycles(dut.clk_i, 10)
    assert got == want, f"{len(got)} records, model {len(want)}"
    assert int(dut.locate_valid_o.value) and int(dut.locate_o.value) == model.locate
    assert not int(dut.stale_o.value) and not model.stale
    # Back to back, the previous packet's last records can leave after this one's first
    # beat: the window holds them too; this packet's own must all be in it.
    in_window = [r["m_seq"] for r in rows if r["m_valid"] and r["m_ready"]]
    shown = [r[1] for r in got if shown_seq <= r[1] < shown_seq + shown_n]
    assert [q for q in in_window if q >= shown_seq] == shown and shown, (in_window, shown)
    out = os.environ.get("FEED_WAVE_CSV")
    if out:
        with open(out, "w", newline="") as f:
            w = csv.DictWriter(f, COLS, lineterminator="\n")
            w.writeheader()
            w.writerows(rows)
    cocotb.log.info(
        "%d packets, %d records = model; packet %d: %d cycles, %d records",
        len(pkts),
        len(got),
        SHOWN,
        len(rows),
        len(shown),
    )
