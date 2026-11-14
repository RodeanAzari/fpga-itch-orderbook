"""cocotb tests for rtl/feed (mold_deframer -> [msg_aligner] -> itch_decoder), scored against
the golden model, at DATA_W 8 and 64 (ADR 0015; the records must not depend on the width).

Why these tests matter: this front end decides which messages the order book applies.
Every packet sent to the DUT is also fed to model.moldudp64.FeedDecoder, and the DUT must
produce the same records (in order, field by field) and the same counters, including
the stale flag. The stimulus is adversarial at the MoldUDP64 level (lost, duplicated,
reordered and overlapping packets, heartbeats, end of session, session changes,
truncated packets, trailing bytes, empty messages) and at the ITCH level (unknown types
skipped by length, wrong lengths, bad sides, other stocks, a locate that changes). A
desynchronized parser, an unflagged gap or a duplicate applied twice all show up as a
record or counter mismatch.
"""

from __future__ import annotations

import os
import struct
from dataclasses import astuple

import cocotb
from cocotb.clock import Clock
from cocotb.triggers import ClockCycles, FallingEdge, RisingEdge
from feed_stim import adversarial_packets
from tb_common import InOrderScoreboard, get_rng
from tb_common.axis import AxisSource, bytes_to_beats

from model import generator
from model.itch import LENGTHS, Add, Delete, Reduce, Replace
from model.moldudp64 import FeedDecoder, heartbeat, pack, packetize

SYMBOL = generator.DEFAULT.stock
LANES = int(os.environ.get("DATA_W", "8")) // 8
STATUS = {  # DUT pulse -> model counter
    "pkt_o": "packets",
    "heartbeat_o": "heartbeats",
    "end_o": "end_of_session",
    "gap_o": "gaps",
    "dup_o": "duplicates",
    "session_change_o": "session_changes",
    "malformed_pkt_o": "malformed_packets",
    "unknown_o": "unknown_types",
    "other_locate_o": "other_locate",
    "dir_match_o": "directory_matches",
    "locate_change_o": "locate_changes",
}
TYPE_CLASS = {
    ord("A"): Add,
    ord("F"): Add,
    ord("E"): Reduce,
    ord("C"): Reduce,
    ord("X"): Reduce,
    ord("D"): Delete,
    ord("U"): Replace,
}


def model_fields(rec) -> tuple:
    """Model record -> the DUT's output fields (unused fields 0)."""
    m = rec.msg
    if isinstance(m, Add):
        return (Add, rec.seq, m.ts, m.ref, 0, int(m.side == "S"), m.shares, m.price)
    if isinstance(m, Reduce):
        return (Reduce, rec.seq, m.ts, m.ref, 0, 0, m.shares, 0)
    if isinstance(m, Delete):
        return (Delete, rec.seq, m.ts, m.ref, 0, 0, 0, 0)
    assert isinstance(m, Replace)
    return (Replace, rec.seq, m.ts, m.ref, m.new_ref, 0, m.shares, m.price)


async def run(
    dut,
    name: str,
    pkts: list[bytes],
    p_valid: float,
    p_ready: float,
    locate: int | None = None,
    starts: set[tuple[int, int]] | None = None,
    p_ext: float = 0.1,
) -> FeedDecoder:
    """`starts` collects (type byte, start lane) of each message leaving the deframer.
    p_ext: probability that s_stale_i is 1 on a cycle (sampled per packet)."""
    rng = get_rng(name)
    cocotb.start_soon(Clock(dut.clk_i, 8, unit="ns").start())
    dut.cfg_symbol_i.value = int.from_bytes(SYMBOL.ljust(8).encode(), "big")
    dut.cfg_locate_valid_i.value = int(locate is not None)
    dut.cfg_locate_i.value = locate or 0
    dut.m_ready_i.value = 0
    dut.rst_i.value = 1
    await ClockCycles(dut.clk_i, 3)
    dut.rst_i.value = 0

    model = FeedDecoder(SYMBOL, locate=locate)
    sb = InOrderScoreboard(name, fmt=lambda x: f"{x[0].__name__} seq={x[1]} ref={x[3]}")
    rec_pkt: list[int] = []  # packet index of each expected record (for its T0)
    rec_stale: list[bool] = []  # model Record.stale, in stream order (ADR 0016)
    for k, p in enumerate(q for q in pkts if q):
        for rec in model.feed(p):
            sb.expect(model_fields(rec))
            rec_pkt.append(k)
            rec_stale.append(rec.stale)
    # ADR 0011: s_t0_i changes every cycle, so T0 is right only if sampled at the
    # packet's first byte; T1 is now_i on the cycle the decoder took the message's last
    # byte (seen on its input, by sequence number).
    pkt_t0: list[int] = []
    last_bytes: list[tuple[int, int]] = []  # (seq, now) per message, in order
    got_t: list[tuple[int, int, int, int]] = []  # (t0, t1, seq, stale) per record

    async def clocks():
        n = 0
        while True:
            await FallingEdge(dut.clk_i)
            n += 1
            dut.now_i.value = n
            dut.s_t0_i.value = rng.getrandbits(64)
            dut.s_stale_i.value = int(rng.random() < p_ext)  # per packet, at its first beat

    dut.now_i.value = 0
    dut.s_t0_i.value = 0
    dut.s_stale_i.value = 0
    pkt_stale: list[int] = []
    cocotb.start_soon(clocks())
    first = True
    dec = dut.u_decoder
    counts = dict.fromkeys(STATUS, 0) | {"gap_msgs": 0, "t1": 0, "empty": 0, "mal_msg": 0}

    dfr = dut.u_deframer

    async def monitor():
        msg_first = True
        while True:
            await RisingEdge(dut.clk_i)
            nonlocal first
            if int(dfr.m_tvalid_o.value) and int(dfr.m_tready_i.value):
                if msg_first and starts is not None:
                    keep = int(dfr.m_tkeep_o.value)
                    lo = (keep & -keep).bit_length() - 1
                    starts.add((int(dfr.m_tdata_o.value) >> (8 * lo) & 0xFF, lo))
                msg_first = bool(int(dfr.m_tlast_o.value))
            if int(dut.s_tvalid_i.value) and int(dut.s_tready_o.value):
                if first:
                    pkt_t0.append(int(dut.s_t0_i.value))
                    pkt_stale.append(int(dut.s_stale_i.value))
                first = bool(int(dut.s_tlast_i.value))
            if (
                int(dec.s_tvalid_i.value)
                and int(dec.s_tready_o.value)
                and int(dec.s_tlast_i.value)
                and not int(dec.s_tuser_i.value)
            ):
                last_bytes.append((int(dec.s_seq_i.value), int(dut.now_i.value)))
            for s in STATUS:
                counts[s] += int(getattr(dut, s).value)
            if int(dut.gap_o.value):
                counts["gap_msgs"] += int(dut.gap_msgs_o.value)
            counts["t1"] += int(dut.t1_o.value)
            counts["empty"] += int(dut.empty_msg_o.value)
            counts["mal_msg"] += int(dut.malformed_msg_o.value)
            if int(dut.m_valid_o.value) and int(dut.m_ready_i.value):
                got_t.append(
                    (
                        int(dut.m_t0_o.value),
                        int(dut.m_t1_o.value),
                        int(dut.m_seq_o.value),
                        int(dut.m_stale_o.value),
                    )
                )
                sb.observe(
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
            dut.m_ready_i.value = int(rng.random() < p_ready)

    cocotb.start_soon(monitor())
    src = AxisSource(dut, dut.clk_i, "s_", rng=rng, p_valid=p_valid)
    await src.send(b for p in pkts if p for b in bytes_to_beats(p, LANES, user=0))
    # Drained = every packet has been counted by the DUT and every expected record seen.
    # Judged by the work, not a fixed delay: the last packet's pulses can trail the
    # source's return. Bounded, so a missing record or packet fails (check_done dumps
    # context) instead of hanging the run.
    n_pkts = sum(1 for p in pkts if p)
    for _ in range(500):
        if not sb.pending and counts["pkt_o"] >= n_pkts:
            break
        await ClockCycles(dut.clk_i, 10)
    await ClockCycles(dut.clk_i, 10)  # stage-2 status pulses of the last message
    sb.check_done()

    t, f = model.tracker.c, model.c
    want = {s: getattr(t, m, None) if hasattr(t, m) else getattr(f, m) for s, m in STATUS.items()}
    got = {s: counts[s] for s in STATUS}
    assert got == want, f"{name}: counters\n DUT   {got}\n model {want}"
    assert counts["gap_msgs"] == t.gap_messages, (counts["gap_msgs"], t.gap_messages)
    assert counts["mal_msg"] + counts["empty"] == f.malformed_messages
    assert counts["t1"] + counts["empty"] == f.messages
    assert int(dut.stale_o.value) == int(model.stale)
    assert len(got_t) == len(rec_pkt)
    j = 0  # records are in message order: each takes the next message with its seq
    for i, ((t0, t1, seq, st), k) in enumerate(zip(got_t, rec_pkt, strict=True)):
        assert t0 == pkt_t0[k], f"record {i} (seq {seq}): t0 {t0:#x} != packet {k}'s"
        want_st = int(rec_stale[i] or pkt_stale[k])
        assert st == want_st, f"record {i} (seq {seq}, packet {k}): stale {st}, want {want_st}"
        while last_bytes[j][0] != seq:
            j += 1
        assert t1 == last_bytes[j][1], f"record {i} (seq {seq}): t1 {t1} != {last_bytes[j][1]}"
        j += 1
    assert int(dut.locate_valid_o.value) == int(model.locate is not None)
    if model.locate is not None:
        assert int(dut.locate_o.value) == model.locate
    cocotb.log.info(
        "%s: %d packets, %d records, counters %s, stale %d",
        name,
        len(pkts),
        sb.matched,
        astuple(t),
        int(model.stale),
    )
    return model


@cocotb.test()
async def clean_stream(dut):
    """A clean, in-order replay: every book record decoded, never stale."""
    rng = get_rng("feed_clean_gen")
    msgs = generator.generate(seed=rng.randrange(1 << 30), count=6000)
    pkts = list(packetize(msgs, b"SESSIONAAA"))
    model = await run(dut, "feed_clean", pkts, 1.0, 1.0)
    assert not model.stale and model.tracker.c.gaps == 0


@cocotb.test()
async def adversarial_backpressure(dut):
    """Every MoldUDP64 and ITCH failure mode, with input gaps and output stalls."""
    rng = get_rng("feed_adv_gen")
    starts: set[tuple[int, int]] = set()  # collected, not asserted: see every_alignment
    model = await run(dut, "feed_adv", adversarial_packets(rng, 8000), 0.7, 0.5, starts=starts)
    c, f = model.tracker.c, model.c
    for k in (
        "gaps",
        "duplicates",
        "heartbeats",
        "end_of_session",
        "session_changes",
        "malformed_packets",
    ):
        assert getattr(c, k) > 0, f"stimulus never produced {k}"
    assert f.unknown_types > 0 and f.malformed_messages > 0 and f.other_locate > 0


@cocotb.test()
async def stale_per_packet(dut):
    """ADR 0016: the external stale is per packet and must stay with that packet's
    records through the deframer, aligner and decoder. Small packets (1-3 messages,
    many boundaries), s_stale_i 1 half the time, full input rate, heavy output stalls
    (the pipeline backs up across packet boundaries)."""
    rng = get_rng("feed_ext_gen")
    msgs = generator.generate(seed=rng.randrange(1 << 30), count=3000)
    # 81: the longest message (59) + framing
    pkts = list(packetize(msgs, b"SESSIONEXT", max_payload=81))
    model = await run(dut, "feed_ext", pkts, 1.0, 0.2, p_ext=0.5)
    assert not model.stale and len(pkts) > 1500


FILLER = b"z"  # not an ITCH 5.0 type: skipped by its length prefix (unknown_types)


@cocotb.test()
async def every_alignment(dut):
    """Every book type (A F E C X D U) enters the aligner at every start lane (buildplan
    §8.1), by construction rather than by seed: each packet carries an unknown-type
    filler of length L, then one book message, which starts at byte 24 + L, lane L mod 8.
    L cycles 1..8 per type, so a type seen 8 times has covered every lane. Generator
    messages keep the book's references valid; records and counters vs the model."""
    rng = get_rng("feed_align_gen")
    msgs = generator.generate(seed=rng.randrange(1 << 30), count=4000)
    seen: dict[int, int] = {}
    pkts, seq = [], 1
    for m in msgs:
        n = seen.get(m[0], 0)
        seen[m[0]] = n + 1
        filler = FILLER + bytes(n % 8)  # length 1..8: the message starts at lane L mod 8
        pkts.append(pack(b"SESSIONALN", seq, [filler, m]))
        seq += 2
    assert all(seen.get(t, 0) >= 8 for t in b"AFECXDU"), f"type counts {seen}"
    starts: set[tuple[int, int]] = set()
    model = await run(dut, "feed_align", pkts, 0.8, 0.6, starts=starts)
    unknown = sum(m[:1] not in LENGTHS for m in msgs)  # the generator's own
    assert not model.stale and model.c.unknown_types == len(pkts) + unknown
    holes = [(chr(t), lane) for t in b"AFECXDU" for lane in range(LANES) if (t, lane) not in starts]
    assert not holes, f"(type, start lane) never seen: {holes}"


@cocotb.test()
async def configured_locate(dut):
    """Host fallback: locate preloaded, the R messages are never sent."""
    rng = get_rng("feed_cfg_gen")
    cfg = generator.DEFAULT
    msgs = [m for m in generator.generate(seed=rng.randrange(1 << 30), count=2000) if m[:1] != b"R"]
    model = await run(
        dut, "feed_cfg", list(packetize(msgs, b"SESSIONAAA")), 1.0, 0.7, locate=cfg.locate
    )
    assert model.c.directory_matches == 0 and len(model.records) > 0


def _clean_pkts(seed: int, n: int = 1500, max_payload: int = 300) -> list[bytes]:
    msgs = generator.generate(seed=seed, count=n)
    return list(packetize(msgs, b"SESSIONAAA", max_payload=max_payload))


def _scenario(name: str, stale: bool):
    """A directed test: one cause only, so the expected stale flag is exact. The sticky
    stale flag hides a missing cause in the mixed adversarial stream; here it cannot."""

    def deco(build):
        @cocotb.test(name=f"directed_{name}")
        async def t(dut):
            pkts = build(get_rng(f"feed_{name}_gen"))
            model = await run(dut, f"feed_{name}", pkts, 1.0, 1.0)
            assert model.stale == stale, f"scenario {name}: model stale {model.stale}"

        return t

    return deco


@_scenario("gap", stale=True)
def _gap(rng):
    pkts = _clean_pkts(rng.randrange(1 << 30))
    del pkts[len(pkts) // 2]
    return pkts


@_scenario("duplicate", stale=False)
def _dup(rng):
    pkts = _clean_pkts(rng.randrange(1 << 30))
    k = len(pkts) // 2
    return pkts[: k + 1] + [pkts[k]] + pkts[k + 1 :]


@_scenario("heartbeat", stale=False)
def _hb(rng):
    pkts = _clean_pkts(rng.randrange(1 << 30))
    k = len(pkts) // 2
    seq, count = struct.unpack_from(">QH", pkts[k], 10)
    return pkts[: k + 1] + [heartbeat(b"SESSIONAAA", seq + count)] + pkts[k + 1 :]


@_scenario("session_change", stale=True)
def _sess(rng):
    msgs = generator.generate(seed=rng.randrange(1 << 30), count=600)
    return list(packetize(msgs, b"SESSIONAAA")) + list(packetize(msgs, b"SESSIONBBB"))


@_scenario("malformed_message", stale=True)
def _malmsg(rng):
    msgs = generator.generate(seed=rng.randrange(1 << 30), count=600)
    k = next(i for i, m in enumerate(msgs) if m[:1] == b"A" and i > 50)
    msgs[k] = msgs[k] + b"\x00"
    return list(packetize(msgs, b"SESSIONAAA"))


@_scenario("locate_change", stale=False)
def _loc(rng):
    """Our symbol's R arrives again with a new locate: later messages use it, and are
    records only if the locate was relearned."""
    msgs = generator.generate(seed=rng.randrange(1 << 30), count=1200)
    new_loc = generator.DEFAULT.locate + 7
    cut = 600
    r = b"R" + struct.pack(">HH", new_loc, 0) + bytes(6) + SYMBOL.ljust(8).encode() + bytes(20)
    tail = [m[:1] + struct.pack(">H", new_loc) + m[3:] for m in msgs[cut:]]
    return list(packetize(msgs[:cut] + [r] + tail, b"SESSIONAAA"))
