"""Tests for the MoldUDP64 framing model and the feed decoder (ADR 0003, ADR 0004).

Why these matter: this layer decides which messages the book ever sees. A gap that goes
unflagged means a missing Add and a silently wrong book; a duplicate applied twice
double-counts shares; a wrong locate builds the book for another stock. Each test pins
one of those failure modes, and the decoder's output is checked against an independent
path (decode the raw stream directly, then filter), not against itself.
"""

from __future__ import annotations

import random
import struct
from dataclasses import replace

from model import generator
from model.itch import Add, Directory, decode
from model.moldudp64 import (
    COUNT_END,
    HEADER_LEN,
    MAX_UDP_PAYLOAD,
    FeedDecoder,
    Record,
    SeqTracker,
    end_of_session,
    heartbeat,
    pack,
    packetize,
)

S1 = b"SESSION001"
S2 = b"SESSION002"


def _msgs(n: int, seed: int = 1) -> list[bytes]:
    rng = random.Random(seed)
    return [bytes([0x41 + rng.randrange(26)]) + rng.randbytes(rng.randint(0, 60)) for _ in range(n)]


def _run(tracker: SeqTracker, pkts: list[bytes]) -> list[tuple[int, bytes]]:
    return [x for p in pkts for x in tracker.feed(p)]


def test_packetize_roundtrip_is_contiguous_and_fits() -> None:
    msgs = _msgs(3000)
    pkts = list(packetize(msgs, S1))
    assert all(len(p) <= MAX_UDP_PAYLOAD for p in pkts)
    t = SeqTracker()
    out = _run(t, pkts)
    assert out == list(enumerate(msgs, start=1))
    assert not t.stale and t.c.gaps == t.c.duplicates == t.c.malformed_packets == 0


def test_dropped_packet_is_a_counted_gap_and_marks_stale() -> None:
    pkts = list(packetize(_msgs(500), S1, max_payload=200))
    lost = pkts.pop(3)
    t = SeqTracker()
    out = _run(t, pkts)
    n_lost = struct.unpack_from(">H", lost, 18)[0]
    assert t.c.gaps == 1 and t.c.gap_messages == n_lost and t.stale
    assert len(out) == 500 - n_lost
    assert [s for s, _ in out] == sorted(s for s, _ in out)  # later messages keep their seq


def test_duplicate_is_dropped_not_applied_twice() -> None:
    pkts = list(packetize(_msgs(100), S1, max_payload=200))
    t = SeqTracker()
    out = _run(t, pkts[:5] + [pkts[2]] + pkts[5:])
    assert t.c.duplicates == 1 and not t.stale
    assert len(out) == 100 and len({s for s, _ in out}) == 100


def test_reordered_packets_flag_a_gap_then_a_duplicate() -> None:
    pkts = list(packetize(_msgs(100), S1, max_payload=200))
    pkts[3], pkts[4] = pkts[4], pkts[3]
    t = SeqTracker()
    _run(t, pkts)
    assert t.c.gaps == 1 and t.c.duplicates == 1 and t.stale


def test_partial_overlap_is_dropped_and_its_new_tail_surfaces_as_a_gap() -> None:
    """A packet that repeats old messages and carries new ones is dropped whole (simple
    RTL). The new messages are not lost silently: the next packet is a gap."""
    msgs = _msgs(30)
    t = SeqTracker()
    t.feed(pack(S1, 1, msgs[:10]))
    assert t.feed(pack(S1, 6, msgs[5:20])) == []  # 6..10 seen, 11..20 new
    t.feed(pack(S1, 21, msgs[20:]))
    assert t.c.duplicates == 1 and t.c.gaps == 1 and t.c.gap_messages == 10 and t.stale


def test_heartbeat_in_sequence_is_harmless_and_ahead_of_sequence_is_a_gap() -> None:
    msgs = _msgs(10)
    t = SeqTracker()
    t.feed(pack(S1, 1, msgs))
    assert t.feed(heartbeat(S1, 11)) == [] and not t.stale and t.c.heartbeats == 1
    t.feed(heartbeat(S1, 14))
    assert t.c.gaps == 1 and t.c.gap_messages == 3 and t.stale


def test_end_of_session_is_counted_and_carries_no_messages() -> None:
    t = SeqTracker()
    t.feed(pack(S1, 1, _msgs(5)))
    assert t.feed(end_of_session(S1, 6)) == []
    assert t.c.end_of_session == 1 and not t.stale
    assert struct.unpack_from(">H", end_of_session(S1, 6), 18)[0] == COUNT_END


def test_session_change_resets_tracking_and_marks_the_old_book_stale() -> None:
    t = SeqTracker()
    t.feed(pack(S1, 1, _msgs(5)))
    assert not t.stale and t.c.session_changes == 0  # the first session is not a change
    out = t.feed(pack(S2, 1, _msgs(3, seed=2)))
    assert [s for s, _ in out] == [1, 2, 3] and t.c.session_changes == 1 and t.stale


def test_missing_start_of_day_is_a_gap() -> None:
    """Replay must start at sequence 1 or the R messages that teach the locate are lost."""
    t = SeqTracker()
    t.feed(pack(S1, 50, _msgs(5)))
    assert t.c.gaps == 1 and t.c.gap_messages == 49 and t.stale


def test_truncated_packet_delivers_what_it_can_and_is_flagged() -> None:
    msgs = _msgs(12)
    good = pack(S1, 1, msgs)
    t = SeqTracker()
    out = t.feed(good[: len(good) - 3])  # last message overruns the packet
    assert [m for _, m in out] == msgs[:11]
    assert t.c.malformed_packets == 1 and t.stale
    t.feed(pack(S1, 13, _msgs(2, seed=3)))  # expected advanced by the header count
    assert t.c.gaps == 0


def test_trailing_bytes_and_short_header_are_malformed() -> None:
    t = SeqTracker()
    t.feed(pack(S1, 1, _msgs(3)) + b"\x00")
    assert t.c.malformed_packets == 1 and t.stale
    t2 = SeqTracker()
    assert t2.feed(b"\x00" * (HEADER_LEN - 1)) == [] and t2.c.malformed_packets == 1


def _reference(msgs: list[bytes], symbol: str) -> list[Record]:
    """Independent path: decode the plain message list, learn the locate, filter."""
    locate, out = None, []
    for seq, raw in enumerate(msgs, start=1):
        try:
            m = decode(raw)
        except ValueError:
            continue
        if isinstance(m, Directory) and m.stock == symbol:
            locate = m.locate
        elif m is not None and raw[:1] in b"AFECXDU" and m.locate == locate:
            out.append(Record(seq, m, raw[0]))
    return out


def test_decoder_matches_independent_reference_on_adversarial_stream() -> None:
    cfg = generator.DEFAULT
    msgs = generator.generate(seed=11, count=20_000, cfg=cfg)
    dec = FeedDecoder(cfg.stock)
    for p in packetize(msgs, S1):
        dec.feed(p)
    assert dec.records == _reference(msgs, cfg.stock)
    assert dec.locate == cfg.locate and not dec.stale
    assert dec.c.unknown_types > 0, "generator noise should exercise skip-by-length"


def test_locate_is_learned_from_R_and_other_stocks_are_filtered() -> None:
    def r(locate: int, stock: str) -> bytes:
        return (
            b"R"
            + struct.pack(">HH", locate, 0)
            + bytes(6)
            + stock.ljust(8).encode()
            + bytes(39 - 19)
        )

    def a(locate: int, ref: int) -> bytes:
        return (
            b"A"
            + struct.pack(">HH", locate, 0)
            + bytes(6)
            + struct.pack(">QcI8sI", ref, b"B", 100, b"AAPL    ", 1_000_000)
        )

    msgs = [a(7, 1), r(3, "MSFT"), r(7, "AAPL"), a(3, 2), a(7, 3), r(9, "AAPL"), a(9, 4)]
    dec = FeedDecoder("AAPL")
    dec.feed(pack(S1, 1, msgs))
    assert [r_.seq for r_ in dec.records] == [5, 7]  # before the R, and MSFT, filtered
    assert all(isinstance(r_.msg, Add) for r_ in dec.records)
    assert dec.c.other_locate == 2 and dec.c.directory_matches == 2 and dec.c.locate_changes == 1

    fixed = FeedDecoder("AAPL", locate=7)  # host-configured fallback: no R needed
    fixed.feed(pack(S1, 1, [a(7, 1)]))
    assert len(fixed.records) == 1


def test_malformed_message_is_skipped_by_length_and_marks_stale() -> None:
    cfg = generator.DEFAULT
    msgs = generator.generate(seed=5, count=200, cfg=cfg)
    k = next(i for i, m in enumerate(msgs) if m[:1] == b"A" and i > 10)
    msgs[k] = msgs[k] + b"\x00"  # an Add one byte too long: framing intact, content bad
    dec = FeedDecoder(cfg.stock)
    for p in packetize(msgs, S1):
        dec.feed(p)
    assert dec.c.malformed_messages == 1 and dec.stale
    want = [x for x in _reference(msgs, cfg.stock) if x.seq != k + 1]
    assert [replace(x, stale=False) for x in dec.records] == want
    # Stale rides with each record: set from the first record after the bad one, not before.
    assert [x.stale for x in dec.records] == [x.seq > k + 1 for x in want]
    assert any(x.stale for x in dec.records) and not all(x.stale for x in dec.records)


def test_stale_is_in_stream_order_around_a_malformed_tail() -> None:
    """ADR 0016: a record's stale is the state when its message was delivered. A packet's
    malformed tail (truncated, or bytes after `count` messages) is found after its intact
    messages, so it flags the next packet's records, not its own; a gap is found at the
    header, so it flags its own packet's records."""
    cfg = generator.DEFAULT
    msgs = generator.generate(seed=11, count=400, cfg=cfg)
    adds = [i for i, m in enumerate(msgs) if m[:1] == b"A"]
    a1, a2, a3 = adds[0], adds[1], adds[2]
    for tail in (b"\x00", b"\x00\x05\x41"):  # trailing byte; a prefix overrunning the end
        dec = FeedDecoder(cfg.stock)
        bad = pack(S1, 1, msgs[: a1 + 1]) + tail
        nxt = pack(S1, a1 + 2, msgs[a1 + 1 : a2 + 1])
        recs = dec.feed(bad) + dec.feed(nxt)
        assert dec.tracker.c.malformed_packets == 1 and dec.stale
        own = [r for r in recs if r.seq <= a1 + 1]
        later = [r for r in recs if r.seq > a1 + 1]
        assert own and later
        assert not any(r.stale for r in own), tail
        assert all(r.stale for r in later), tail
    dec = FeedDecoder(cfg.stock)
    dec.feed(pack(S1, 1, msgs[: a1 + 1]))
    gap = dec.feed(pack(S1, a2 + 1, msgs[a2 : a3 + 1]))  # messages a1+2 .. a2 lost
    assert dec.tracker.c.gaps == 1 and gap and all(r.stale for r in gap)
