"""cocotb tests for rtl/feed/mold_deframer at DATA_W 8 and 64, scored against the golden
model (model.moldudp64.SeqTracker).

Why these tests matter: the deframer decides where every message starts. At 64 bits the
header, each 2-byte length prefix and each message can sit at any byte lane, and a
prefix can straddle two beats; one lane off and every later message in the packet is
garbage. Every packet is also fed to the model, and the DUT must deliver the same
messages (sequence number and bytes, in order) with the packet's T0, the same sequence
counters (gaps, duplicates, heartbeats, end of session, session changes, malformed
packets, empty messages) and the same stale flag.

At 64 bits two more properties are checked because the next stage (the message aligner,
ADR 0015) relies on them: the output shape (one message per tlast, contiguous tkeep,
only a message's first beat starts above lane 0, only its last ends below lane 7) and
the throughput the header promises (cycles per packet <= beats + messages + 1). The
every-lane stimulus uses raw messages of every length so starts, ends, prefix straddles
and truncation points land on every lane; coverage of those is asserted, not assumed.
"""

from __future__ import annotations

import os
import struct
from collections import Counter
from dataclasses import asdict

import cocotb
from cocotb.clock import Clock
from cocotb.triggers import ClockCycles, FallingEdge, RisingEdge
from cov import sample_feed
from feed_stim import adversarial_packets
from tb_common import InOrderScoreboard, get_rng
from tb_common.axis import AxisBeat, AxisSource, bytes_to_beats

from model import generator
from model.moldudp64 import HEADER_LEN, SeqTracker, end_of_session, heartbeat, pack, packetize

DATA_W = int(os.environ.get("DATA_W", "8"))
LANES = DATA_W // 8
FULL = (1 << LANES) - 1
STATUS = {  # DUT pulse -> model counter
    "pkt_o": "packets",
    "heartbeat_o": "heartbeats",
    "end_o": "end_of_session",
    "gap_o": "gaps",
    "dup_o": "duplicates",
    "session_change_o": "session_changes",
    "malformed_o": "malformed_packets",
}


def lanes_of(keep: int) -> list[int]:
    return [i for i in range(LANES) if keep >> i & 1]


async def run(
    dut,
    name: str,
    pkts: list[tuple[bytes, int]],
    p_valid: float,
    p_ready: float,
    p_ext: float = 0.2,
) -> tuple[SeqTracker, Counter]:
    """Send (packet, tuser) pairs; returns the model and the DUT-side coverage."""
    rng = get_rng(name)
    pkts = [(p, u) for p, u in pkts if p]  # AXI-Stream cannot carry an empty packet
    cocotb.start_soon(Clock(dut.clk_i, 8, unit="ns").start())
    dut.m_tready_i.value = 0
    dut.s_t0_i.value = 0
    dut.s_stale_i.value = 0
    dut.rst_i.value = 1
    await ClockCycles(dut.clk_i, 3)
    dut.rst_i.value = 0

    # Model. A packet flagged truncated (tuser) is malformed unless it is a duplicate or
    # already malformed: the model sees only bytes, so that verdict is added here.
    model = SeqTracker()
    sb = InOrderScoreboard(name, fmt=lambda x: f"seq={x[0]} len={len(x[1])}")
    exp_pkt: list[int] = []  # packet index of each expected message (for its T0)
    # ADR 0016: each message's in-band stale, in stream order: the tracker's state as the
    # packet's messages are delivered, or an empty message before it (the packet's
    # sampled s_stale_i is ORed in below)
    exp_stale: list[bool] = []
    n_empty = 0
    for k, (p, user) in enumerate(pkts):
        c = model.c
        before = (c.duplicates, c.malformed_packets)
        for seq, msg in model.feed(p):
            if msg:
                sb.expect((seq, msg))
                exp_pkt.append(k)
                exp_stale.append(model.msg_stale or n_empty > 0)
            else:
                n_empty += 1
        if user and before == (c.duplicates, c.malformed_packets):
            model._malformed()

    pkt_t0: list[int] = []
    got_t0: list[int] = []
    pkt_stale: list[int] = []
    got_stale: list[int] = []
    counts = Counter()
    cov: Counter = Counter()
    cycles = {"first": None, "last": 0, "n": 0}

    async def drive_t0():
        while True:
            await FallingEdge(dut.clk_i)
            dut.s_t0_i.value = rng.getrandbits(64)
            dut.s_stale_i.value = int(rng.random() < p_ext)  # right only if sampled right

    cocotb.start_soon(drive_t0())

    async def monitor():
        first_in = True
        cur = bytearray()
        cur_meta: tuple[int, int, int] | None = None
        while True:
            await RisingEdge(dut.clk_i)
            cycles["n"] += 1
            if int(dut.s_tvalid_i.value) and int(dut.s_tready_o.value):
                if cycles["first"] is None:
                    cycles["first"] = cycles["n"]
                if first_in:
                    pkt_t0.append(int(dut.s_t0_i.value))
                    pkt_stale.append(int(dut.s_stale_i.value))
                first_in = bool(int(dut.s_tlast_i.value))
            for s in STATUS:
                counts[s] += int(getattr(dut, s).value)
            if int(dut.pkt_o.value):
                cycles["last"] = cycles["n"]
            if int(dut.gap_o.value):
                counts["gap_msgs"] += int(dut.gap_msgs_o.value)
            counts["empty"] += int(dut.empty_msg_o.value)
            if int(dut.m_tvalid_o.value) and int(dut.m_tready_i.value):
                keep = int(dut.m_tkeep_o.value)
                last = bool(int(dut.m_tlast_o.value))
                ln = lanes_of(keep)
                assert ln and ln == list(range(ln[0], ln[-1] + 1)), f"tkeep {keep:#x}"
                if cur:
                    assert ln[0] == 0, f"{name}: middle/last beat starts at lane {ln[0]}"
                if not last:
                    assert ln[-1] == LANES - 1, f"{name}: non-last beat ends at lane {ln[-1]}"
                meta = (int(dut.m_seq_o.value), int(dut.m_t0_o.value), int(dut.m_stale_o.value))
                assert cur_meta is None or meta == cur_meta, f"{name}: seq/t0/stale changed"
                if not cur:
                    cov[f"start{ln[0]}"] += 1
                cur_meta = meta
                data = int(dut.m_tdata_o.value).to_bytes(LANES, "little")
                cur += bytes(data[i] for i in ln)
                if last:
                    cov[f"end{ln[-1]}"] += 1
                    if int(dut.m_tuser_o.value):
                        counts["partial"] += 1
                    else:
                        sb.observe((meta[0], bytes(cur)))
                        got_t0.append(meta[1])
                        got_stale.append(meta[2])
                    cur = bytearray()
                    cur_meta = None
                else:
                    assert not int(dut.m_tuser_o.value)
            dut.m_tready_i.value = int(rng.random() < p_ready)

    cocotb.start_soon(monitor())
    beats = [
        AxisBeat(b.data, last=b.last, keep=b.keep, user=user)
        for p, user in pkts
        for b in bytes_to_beats(p, LANES)
    ]
    for p, _ in pkts:  # stimulus-side coverage
        cov[f"pktend{(len(p) - 1) % LANES}"] += 1
        if len(p) < HEADER_LEN:
            cov["short_hdr"] += 1
        off = HEADER_LEN
        while off + 2 <= len(p):
            if off % LANES == LANES - 1:
                cov["prefix_straddle"] += 1
            off += 2 + struct.unpack_from(">H", p, off)[0]
    src = AxisSource(dut, dut.clk_i, "s_", rng=rng, p_valid=p_valid)
    await src.send(beats)
    for _ in range(500):  # drained: judged by the work, bounded so a loss fails
        if not sb.pending and counts["pkt_o"] >= len(pkts):
            break
        await ClockCycles(dut.clk_i, 10)
    await ClockCycles(dut.clk_i, 10)
    sb.check_done()

    c = model.c
    want = {s: getattr(c, m) for s, m in STATUS.items()}
    got = {s: counts[s] for s in STATUS}
    assert got == want, f"{name}: counters\n DUT   {got}\n model {want}"
    assert counts["gap_msgs"] == c.gap_messages, (counts["gap_msgs"], c.gap_messages)
    assert counts["empty"] == n_empty, (counts["empty"], n_empty)
    assert int(dut.stale_o.value) == int(model.stale)
    for i, (t0, k) in enumerate(zip(got_t0, exp_pkt, strict=True)):
        assert t0 == pkt_t0[k], f"{name}: message {i}: t0 {t0:#x} != packet {k}'s"
    for i, (st, k) in enumerate(zip(got_stale, exp_pkt, strict=True)):
        want_st = int(exp_stale[i] or pkt_stale[k])
        assert st == want_st, f"{name}: message {i} (packet {k}): stale {st}, want {want_st}"
        cov[f"stale_model{int(exp_stale[i])}_ext{pkt_stale[k]}"] += 1
    cov["cycles"] = cycles["last"] - (cycles["first"] or 0) + 1
    cov["beats"] = len(beats)
    cov["msgs"] = sb.matched + counts["partial"] + n_empty
    cocotb.log.info(
        "%s DATA_W %d: %d packets, %d messages, %d cycles for %d beats; counters %s; cov %s",
        name,
        DATA_W,
        len(pkts),
        sb.matched,
        cov["cycles"],
        len(beats),
        asdict(c),
        dict(sorted(cov.items())),
    )
    sample_feed([p for p, _ in pkts], DATA_W)
    return model, cov


def lane_packets(rng, n_pkts: int) -> list[tuple[bytes, int]]:
    """Raw messages (the deframer does not parse ITCH) of every length from 0, so starts,
    ends and prefix straddles hit every lane; then packet-level damage and every header
    truncation length."""
    sess = b"SESSIONAAA"
    seq = 1
    out: list[tuple[bytes, int]] = []
    for _ in range(n_pkts):
        n = rng.randrange(1, 12)
        msgs = [
            bytes(rng.getrandbits(8) for _ in range(rng.choice([0, 1, 2, 3, 7, 8, 9, 15, 16])))
            if rng.random() < 0.3
            else bytes(rng.getrandbits(8) for _ in range(rng.randrange(1, 70)))
            for _ in range(n)
        ]
        p = pack(sess, seq, msgs)
        r = rng.random()
        if r < 0.08:
            out.append((p[: rng.randrange(1, len(p))], 0))  # truncated, any lane
        elif r < 0.12:
            out.append((p + bytes(rng.randrange(1, 2 * LANES + 2)), 0))  # trailing bytes
        elif r < 0.15:
            out.append((p, 1))  # flagged truncated by the MAC
        elif r < 0.18:
            out += [(p, 0), (p, 0)]  # duplicate
        elif r < 0.20:
            out.append((heartbeat(sess, seq), 0))
            out.append((p, 0))
        elif r < 0.22:
            out.append((heartbeat(sess, seq) + b"\x00", 0))  # heartbeat with a body
            out.append((p, 0))
        elif r < 0.24:
            seq += n  # this packet is lost: a gap
            continue
        else:
            out.append((p, 0))
        seq += n
    for k in range(1, HEADER_LEN + 1):  # every header truncation point (20 = no body)
        out.append((pack(sess, seq, [b"x" * 5])[:k], 0))
    out.append((end_of_session(sess, seq + (1 << 20)), 0))
    # A new session whose first packet is a duplicate (seq 0 < 1): expected must restart
    # at 1 for it, so the next packet is accepted, not dropped.
    out.append((pack(b"SESSIONBBB", 0, [b"zz"]), 0))
    out.append((pack(b"SESSIONBBB", 1, [b"abc", b"", b"defghij"]), 0))
    return out


@cocotb.test()
async def clean_stream(dut):
    """A clean, in-order ITCH replay at full rate: every message, never stale, and the
    promised throughput (cycles <= beats + messages + packets, plus pipeline fill)."""
    rng = get_rng("dfr_clean_gen")
    msgs = generator.generate(seed=rng.randrange(1 << 30), count=6000)
    pkts = [(p, 0) for p in packetize(msgs, b"SESSIONAAA")]
    model, cov = await run(dut, "dfr_clean", pkts, 1.0, 1.0)
    assert not model.stale and model.c.gaps == 0 and model.c.malformed_packets == 0
    # in-band stale (ADR 0016): never from the model here, from s_stale_i on some packets
    assert cov["stale_model0_ext0"] and cov["stale_model0_ext1"], cov
    bound = cov["beats"] + cov["msgs"] + len(pkts) + 4
    assert cov["cycles"] <= bound, f"{cov['cycles']} cycles > bound {bound}"


@cocotb.test()
async def adversarial_backpressure(dut):
    """Every MoldUDP64 failure mode (feed_stim), with input gaps and output stalls."""
    rng = get_rng("dfr_adv_gen")
    pkts = [(p, 0) for p in adversarial_packets(rng, 8000)]
    model, cov = await run(dut, "dfr_adv", pkts, 0.7, 0.5)
    c = model.c
    # in-band stale from the model, with and without s_stale_i (model-0 bins: clean_stream)
    assert cov["stale_model1_ext0"] and cov["stale_model1_ext1"], cov
    for k in ("gaps", "duplicates", "heartbeats", "end_of_session", "session_changes"):
        assert getattr(c, k) > 0, f"stimulus never produced {k}"
    assert c.malformed_packets > 0


async def _tail_case(dut, name: str, tail: bytes, p: float) -> None:
    rng = get_rng(f"{name}_gen")
    msgs = generator.generate(seed=rng.randrange(1 << 30), count=30)
    sess = b"SESSIONTAL"
    pkts = [
        (pack(sess, 1, msgs[0:10]), 0),
        (pack(sess, 11, msgs[10:20]) + tail, 0),
        (pack(sess, 21, msgs[20:30]), 0),  # the header count sets expected: no gap
    ]
    model, cov = await run(dut, name, pkts, p, p, p_ext=0.0)
    assert model.c.malformed_packets == 1 and model.c.gaps == 0
    assert cov["stale_model0_ext0"] == 20 and cov["stale_model1_ext0"] == 10, cov


@cocotb.test()
async def malformed_tail_trailing_byte(dut):
    """ADR 0016: a packet's malformed tail is found after its intact messages, so they
    stay unflagged and the next packet's are stale. External stale held at 0, so only
    the in-band order decides. Here: one byte after the last message."""
    await _tail_case(dut, "dfr_tail1", b"\x00", 0.8)


@cocotb.test()
async def malformed_tail_overrun(dut):
    """As malformed_tail_trailing_byte, with a length prefix that overruns the packet
    (the truncated message is discarded), at full rate."""
    await _tail_case(dut, "dfr_tail2", b"\x00\x30\x41", 1.0)


async def _every_lane(dut, name: str, p_valid: float, p_ready: float) -> None:
    rng = get_rng(f"{name}_gen")
    model, cov = await run(dut, name, lane_packets(rng, 1500), p_valid, p_ready)
    c = model.c
    for k in ("gaps", "duplicates", "heartbeats", "end_of_session", "session_changes"):
        assert getattr(c, k) > 0, f"stimulus never produced {k}"
    want = [f"{e}{i}" for e in ("start", "end", "pktend") for i in range(LANES)]
    want += ["short_hdr"] + (["prefix_straddle"] if LANES > 1 else [])
    missing = [k for k in want if cov[k] == 0]
    assert not missing, f"coverage holes: {missing}"


@cocotb.test()
async def every_lane(dut):
    """Messages, prefixes and packet ends at every lane, at full rate."""
    await _every_lane(dut, "dfr_lane", 1.0, 1.0)


@cocotb.test()
async def every_lane_backpressure(dut):
    """The same with input gaps and output stalls (state held across stalls)."""
    await _every_lane(dut, "dfr_lane_bp", 0.6, 0.4)
