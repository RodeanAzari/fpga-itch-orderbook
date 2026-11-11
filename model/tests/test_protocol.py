"""model/protocol.py against docs/protocol.md: offsets come from the doc's tables."""

from __future__ import annotations

import pytest

from model import generator
from model.book import Bbo
from model.bounded import BoundedBook
from model.itch import Add, Delete, Reduce, Replace
from model.moldudp64 import Record, packetize
from model.protocol import (
    BBO,
    CONFIG,
    CONFIG_ACK,
    MAX_COUNT,
    N_STATS,
    STATS,
    STATS_NAMES,
    TRACE,
    Config,
    bbo_record,
    expected,
    packet,
    parse,
    parse_config,
    stats_records,
    trace_record,
)


def be(b: bytes) -> int:
    return int.from_bytes(b, "big")


def test_header_layout_and_max_counts() -> None:
    p = packet(BBO, 0xA1B2C3D4, [bytes(64)] * 3)
    assert p[:2] == b"OB" and p[2] == 1 and p[3] == 0x01
    assert be(p[4:8]) == 0xA1B2C3D4 and be(p[8:10]) == 3 and be(p[10:12]) == 64
    assert len(p) == 12 + 3 * 64
    # docs/protocol.md: 22 BBO and 36 trace records fit in a 1,472-byte payload.
    assert MAX_COUNT[BBO] == 22 and MAX_COUNT[TRACE] == 36
    assert 12 + 22 * 64 <= 1472 < 12 + 23 * 64


def test_bbo_record_offsets() -> None:
    bbo = Bbo(0x0102030405, 2_345_500, 0x123456789, 2_345_600, 300, out_of_window=True)
    r = bbo_record(bbo, 0x1122334455667788, stale=True, t=(7, 8, 9))
    assert len(r) == 64
    assert be(r[0:2]) == 0
    assert be(r[2:4]) == 0b01001  # stale | out_of_window
    assert be(r[4:8]) == 2_345_500 and be(r[8:12]) == 2_345_600
    assert be(r[12:18]) == 0x123456789 and be(r[18:24]) == 300
    assert be(r[24:30]) == 0x0102030405 and r[30:32] == b"\0\0"
    assert be(r[32:40]) == 0x1122334455667788
    assert (be(r[40:48]), be(r[48:56]), be(r[56:64])) == (7, 8, 9)


@pytest.mark.parametrize(
    ("bid", "bsz", "ask", "asz", "flags"),
    [
        (100, 1, 100, 1, 0b100),  # locked
        (101, 1, 100, 1, 0b010),  # crossed
        (101, 1, 0, 0, 0),  # empty ask is neither
        (0, 0, 100, 1, 0),
        (99, 1, 100, 1, 0),
    ],
)
def test_bbo_crossed_locked_flags(bid: int, bsz: int, ask: int, asz: int, flags: int) -> None:
    assert be(bbo_record(Bbo(1, bid, bsz, ask, asz), 1, stale=False)[2:4]) == flags


def test_trace_record_offsets_per_type() -> None:
    a = trace_record(Record(5, Add(1, 0xAABBCCDDEEFF, 77, "S", 300, "AAPL", 2_345_500), ord("F")))
    assert len(a) == 40 and a[0:1] == b"F" and a[1] == 1
    assert be(a[2:8]) == 0xAABBCCDDEEFF and be(a[8:16]) == 5 and be(a[16:24]) == 77
    assert be(a[24:32]) == 0 and be(a[32:36]) == 300 and be(a[36:40]) == 2_345_500
    u = trace_record(Record(6, Replace(1, 2, 77, 78, 200, 2_345_600), ord("U")))
    assert u[0:1] == b"U" and u[1] == 0 and be(u[16:24]) == 77 and be(u[24:32]) == 78
    assert be(u[32:36]) == 200 and be(u[36:40]) == 2_345_600
    c = trace_record(Record(7, Reduce(1, 3, 77, 50), ord("C")))
    assert be(c[32:36]) == 50 and be(c[36:40]) == 0  # C's execution price is not carried
    d = trace_record(Record(8, Delete(1, 4, 77), ord("D")))
    assert be(d[16:24]) == 77 and be(d[32:40]) == 0


def test_parse_round_trip_and_rejects() -> None:
    recs = [bytes([i]) * 40 for i in range(36)]
    d = parse(packet(TRACE, 9, recs))
    assert (d.kind, d.pkt_seq, list(d.records)) == (TRACE, 9, recs)
    good = packet(BBO, 1, [bytes(64)])
    bad = [
        b"OA" + good[2:],  # magic
        good[:2] + b"\x02" + good[3:],  # version
        good[:3] + b"\x07" + good[4:],  # unknown type
        good[:10] + b"\x00\x28" + good[12:],  # record_len != 64 for BBO
        good[:8] + b"\x00\x00" + good[10:],  # count 0
        good[:8] + b"\x00\x02" + good[10:],  # count says 2, one present
        good + b"\0",  # trailing byte
        good[:11],  # short header
    ]
    for b in bad:
        with pytest.raises(ValueError):
            parse(b)
    with pytest.raises(ValueError):
        packet(BBO, 0, [bytes(64)] * 23)


def test_stats() -> None:
    assert N_STATS == 41 and len(set(STATS_NAMES)) == 41  # t0_lost added (ADR 0011)
    assert STATS_NAMES[0] == "uptime_cycles" and STATS_NAMES[39] == "ctrl_bad"
    assert STATS_NAMES[40] == "t0_lost"
    p = packet(STATS, 3, stats_records(list(range(N_STATS))))
    assert len(p) == 12 + 328 and be(p[12 + 8 * 39 : 12 + 8 * 40]) == 39
    assert be(p[12 + 8 * 40 : 12 + 8 * 41]) == 40


def test_config_round_trip_offsets_and_rejects() -> None:
    cfg = Config(0xEF010101, 26400, 1, True, "AAPL", (234_55 - 2048) * 100, 14)
    r = cfg.record()
    assert len(r) == 24 and be(r[0:4]) == 0xEF010101 and be(r[4:6]) == 26400
    assert r[6] == 1 and r[7] == 1 and r[8:16] == b"AAPL    "
    assert be(r[16:20]) == (234_55 - 2048) * 100 and be(r[20:22]) == 14 and r[22:24] == b"\0\0"
    assert parse_config(packet(CONFIG, 0, [r])) == cfg
    assert parse_config(packet(CONFIG_ACK, 0, [r])) is None  # an ack is not a config
    assert parse_config(packet(CONFIG, 0, [r[:6] + b"\x02" + r[7:]])) is None  # mode 2
    assert parse_config(packet(CONFIG, 0, [r[:7] + b"\x02" + r[8:]])) is None  # locate_valid 2
    assert parse_config(packet(CONFIG, 0, [r]) + b"\0") is None


def test_bbo_stale_is_feed_stale_of_the_cause_or_book_stale() -> None:
    base = generator.DEFAULT.base_price // 100
    # Feed-only: a malformed D (one byte long) is skipped by length and makes the decoder
    # stale; its order just stays live, so the book never goes stale. BBOs are stale exactly
    # when their cause comes after the bad message.
    cfg = generator.Config(p_unknown_ref=0.0, p_collide=0.0)
    msgs = generator.generate(seed=4, count=3000, cfg=cfg)
    k = next(i for i, m in enumerate(msgs) if m[:1] == b"D" and i > len(msgs) // 2)
    msgs[k] += b"\0"
    pkts = list(packetize(msgs, b"SESSION001", max_payload=200))
    book = BoundedBook(base_ticks=base, sets_per_bank=1 << 16)
    out, dec = expected(pkts, Config(symbol=cfg.stock), book)
    assert dec.stale and not book.stale
    flags = [be(r[2:4]) & 1 for r in out]
    assert flags == [int(be(r[32:40]) > k + 1) for r in out]  # MoldUDP64 seq = index + 1
    assert 0 in flags and 1 in flags
    # Book-only: the default stream's unknown refs make the book stale; the feed is clean.
    msgs = generator.generate(seed=4, count=3000)
    book = BoundedBook(base_ticks=base, sets_per_bank=1 << 16)
    out, dec = expected(packetize(msgs, b"SESSION001"), Config(symbol="TEST"), book)
    assert not dec.stale and book.stale
    flags = [be(r[2:4]) & 1 for r in out]
    first = flags.index(1)
    assert 0 < first and all(flags[first:])  # sticky


def test_trace_mode_sends_every_record() -> None:
    msgs = generator.generate(seed=4, count=3000)
    base = generator.DEFAULT.base_price // 100
    book = BoundedBook(base_ticks=base)
    trace, dec = expected(packetize(msgs, b"SESSION001"), Config(symbol="TEST", mode=1), book)
    assert trace == [trace_record(r) for r in dec.records] and len(trace) > 1000
    assert not book.orders  # the book is not fed in trace mode
