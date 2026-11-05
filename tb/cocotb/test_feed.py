"""cocotb tests for rtl/feed (mold_deframer -> itch_decoder), scored against the golden model.

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

import struct
from dataclasses import astuple

import cocotb
from cocotb.clock import Clock
from cocotb.triggers import ClockCycles, RisingEdge
from tb_common import InOrderScoreboard, get_rng
from tb_common.axis import AxisBeat, AxisSource

from model import generator
from model.itch import Add, Delete, Reduce, Replace
from model.moldudp64 import FeedDecoder, end_of_session, heartbeat, pack, packetize

SYMBOL = generator.DEFAULT.stock
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


def adversarial_packets(rng, n_msgs: int) -> list[bytes]:
    """Generator messages (already ITCH-adversarial), packetized, then perturbed."""
    msgs = generator.generate(seed=rng.randrange(1 << 30), count=n_msgs)
    for _ in range(max(1, n_msgs // 400)):  # ITCH-level damage
        k = rng.randrange(20, len(msgs))
        how = rng.choice(["long", "short", "side", "empty"])
        m = msgs[k]
        if how == "long":
            msgs[k] = m + b"\x00"
        elif how == "short" and len(m) > 1:
            msgs[k] = m[:-1]
        elif how == "side" and m[:1] in (b"A", b"F"):
            msgs[k] = m[:19] + b"Q" + m[20:]
        elif how == "empty":
            msgs[k] = b""
    # Other stocks: copies of book messages with another locate, and their R.
    other = 900
    msgs.insert(5, b"R" + struct.pack(">HH", other, 0) + bytes(6) + b"OTHER   " + bytes(20))
    for _ in range(max(1, n_msgs // 100)):
        k = rng.randrange(10, len(msgs))
        m = msgs[k]
        if m[:1] in b"AFECXDU" and len(m) > 3:
            msgs.insert(k, m[:1] + struct.pack(">H", other) + m[3:])
    # Near the end, our symbol's locate changes: later messages use the new one.
    new_loc = generator.DEFAULT.locate + 1
    cut = len(msgs) - 200
    msgs.insert(
        cut, b"R" + struct.pack(">HH", new_loc, 0) + bytes(6) + SYMBOL.ljust(8).encode() + bytes(20)
    )
    msgs[cut + 1 :] = [
        m[:1] + struct.pack(">H", new_loc) + m[3:] if len(m) > 3 else m for m in msgs[cut + 1 :]
    ]
    sess = [b"SESSIONAAA", b"SESSIONBBB"]
    pkts = list(packetize(msgs, sess[0], max_payload=rng.choice([84, 200, 1472])))
    out: list[bytes] = []
    i = 0
    while i < len(pkts):
        p = pkts[i]
        seq, count = struct.unpack_from(">QH", p, 10)
        r = rng.random()
        if r < 0.02:
            pass  # lost
        elif r < 0.04:
            out += [p, p]  # duplicate
        elif r < 0.06 and i + 1 < len(pkts):
            out += [pkts[i + 1], p]  # reordered
            i += 1
        elif r < 0.08:
            out += [p, heartbeat(sess[0], seq + count)]
        elif r < 0.09:
            out.append(p[: rng.randrange(1, len(p))])  # truncated
        elif r < 0.10:
            out.append(p + bytes(rng.randrange(1, 5)))  # trailing bytes
        elif r < 0.11 and count > 2:
            out.append(pack(sess[0], seq + 1, []) + p[22:])  # overlap-ish / malformed mix
        else:
            out.append(p)
        i += 1
    out.append(end_of_session(sess[0], 1 << 40))
    out.append(pack(sess[1], 1, msgs[:50]))  # a new session: tracking restarts
    return out


async def run(
    dut, name: str, pkts: list[bytes], p_valid: float, p_ready: float, locate: int | None = None
) -> FeedDecoder:
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
    for p in pkts:
        if p:
            for rec in model.feed(p):
                sb.expect(model_fields(rec))
    counts = dict.fromkeys(STATUS, 0) | {"gap_msgs": 0, "t1": 0, "empty": 0, "mal_msg": 0}

    async def monitor():
        while True:
            await RisingEdge(dut.clk_i)
            for s in STATUS:
                counts[s] += int(getattr(dut, s).value)
            if int(dut.gap_o.value):
                counts["gap_msgs"] += int(dut.gap_msgs_o.value)
            counts["t1"] += int(dut.t1_o.value)
            counts["empty"] += int(dut.empty_msg_o.value)
            counts["mal_msg"] += int(dut.malformed_msg_o.value)
            if int(dut.m_valid_o.value) and int(dut.m_ready_i.value):
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
    await src.send(
        AxisBeat(b, last=(k == len(p) - 1), user=0) for p in pkts if p for k, b in enumerate(p)
    )
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
    model = await run(dut, "feed_adv", adversarial_packets(rng, 8000), 0.7, 0.5)
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
