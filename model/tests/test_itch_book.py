"""Each test pins a failure mode from CLAUDE.md §12 that would silently produce a wrong book."""

import io
import struct

import pytest

from model.book import OrderBook
from model.itch import (
    Add,
    Delete,
    MalformedMessage,
    Reduce,
    Replace,
    TruncatedStream,
    decode,
    iter_raw,
)

HEADER = b"\x00\x07" + b"\x00\x00" + (1234).to_bytes(6)  # locate 7, tracking 0, ts 1234


def frame(*msgs: bytes) -> io.BytesIO:
    return io.BytesIO(b"".join(struct.pack(">H", len(m)) + m for m in msgs))


def add(ref: int, side: str, shares: int, price: int) -> Add:
    return Add(7, 1, ref, side, shares, "AAPL", price)


def test_add_fields_come_from_spec_offsets() -> None:
    # Built byte by byte, independently of the parser's struct formats.
    msg = (
        b"A" + HEADER + (0x0102030405060708).to_bytes(8) + b"S"
        + (300).to_bytes(4) + b"AAPL    " + (2_901_500).to_bytes(4)
    )  # fmt: skip
    assert decode(msg) == Add(7, 1234, 0x0102030405060708, "S", 300, "AAPL", 2_901_500)


def test_unknown_type_is_skipped_by_length_without_desync() -> None:
    # One unskipped unknown type would desynchronize every later message in the day.
    delete = b"D" + HEADER + (9).to_bytes(8)
    msgs = list(iter_raw(frame(b"Q" + b"\xff" * 39, delete)))
    assert decode(msgs[0]) is None
    assert decode(msgs[1]) == Delete(7, 1234, 9)


def test_wrong_length_on_known_type_is_rejected_not_misparsed() -> None:
    with pytest.raises(MalformedMessage):
        decode(b"D" + HEADER + (9).to_bytes(7))


def test_truncated_file_fails_loud() -> None:
    with pytest.raises(TruncatedStream):
        list(iter_raw(io.BytesIO(b"\x00\x10abc")))


def test_executed_with_price_keeps_level_at_resting_price() -> None:
    # 'C' may print away from the resting price; the level to reduce is still the resting one.
    msg = b"C" + HEADER + (5).to_bytes(8) + (40).to_bytes(4) + bytes(8) + b"Y" + (999).to_bytes(4)
    book = OrderBook()
    book.apply(add(5, "B", 100, 1000))
    decoded = decode(msg)
    assert decoded == Reduce(7, 1234, 5, 40)
    bbo = book.apply(decoded)
    assert bbo is not None and (bbo.bid_price, bbo.bid_size) == (1000, 60)


def test_replace_inherits_side_from_original() -> None:
    book = OrderBook()
    book.apply(add(1, "S", 100, 2000))
    bbo = book.apply(Replace(7, 2, 1, 2, 50, 1990))
    assert bbo is not None
    assert (bbo.ask_price, bbo.ask_size, bbo.bid_size) == (1990, 50, 0)
    assert 1 not in book.orders


def test_emptying_best_level_reveals_next_best() -> None:
    book = OrderBook()
    book.apply(add(1, "B", 100, 1000))
    book.apply(add(2, "B", 200, 990))
    bbo = book.apply(Reduce(7, 3, 1, 100))
    assert bbo is not None and (bbo.bid_price, bbo.bid_size) == (990, 200)


def test_level_recreated_after_emptying_is_still_found() -> None:
    # Guards the lazy heap: a price leaving and returning must not be lost or double counted.
    book = OrderBook()
    book.apply(add(1, "B", 100, 1000))
    book.apply(Delete(7, 2, 1))
    bbo = book.apply(add(2, "B", 70, 1000))
    assert bbo is not None and (bbo.bid_price, bbo.bid_size) == (1000, 70)


def test_bbo_emitted_only_on_change() -> None:
    book = OrderBook()
    assert book.apply(add(1, "B", 100, 1000)) is not None
    assert book.apply(add(2, "B", 100, 900)) is None  # behind the top


def test_crossed_and_locked_are_flagged() -> None:
    book = OrderBook()
    book.apply(add(1, "B", 100, 1000))
    locked = book.apply(add(2, "S", 100, 1000))
    assert locked is not None and locked.locked and not locked.crossed
    crossed = book.apply(add(3, "S", 100, 900))
    assert crossed is not None and crossed.crossed


def test_anomalies_are_counted_not_hidden() -> None:
    book = OrderBook()
    book.apply(Delete(7, 1, 42))
    book.apply(add(1, "B", 100, 1000))
    book.apply(add(1, "B", 100, 1000))
    book.apply(Reduce(7, 2, 1, 500))
    assert (book.counters.unknown_ref, book.counters.duplicate_ref) == (1, 1)
    assert book.counters.over_reduce == 1
    assert not book.bids  # over-reduce removes the order; no negative level survives
