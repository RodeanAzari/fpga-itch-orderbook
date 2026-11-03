"""The bounded model is what the RTL is scored against; these pin its limit semantics."""

from model.book import Bbo, OrderBook
from model.bounded import BoundedBook, in_window
from model.generator import Config, generate
from model.itch import Add, Delete, Message, Reduce, Replace, decode


def add(ref: int, side: str, shares: int, price: int) -> Add:
    return Add(1, 1, ref, side, shares, "T", price)


def replay(book: OrderBook, msgs: list[bytes]) -> list[Bbo]:
    decoded: list[Message | None] = [decode(m) for m in msgs]
    return [b for m in decoded if m is not None and (b := book.apply(m)) is not None]


def test_window_matches_array_bounds() -> None:
    # Index = ticks - base + half must fit in 0..2*half-1: an off-by-one here is an RTL overrun.
    assert in_window(100 * (1000 - 2048), 1000, 2048)
    assert not in_window(100 * (1000 - 2049), 1000, 2048)
    assert in_window(100 * (1000 + 2047), 1000, 2048)
    assert not in_window(100 * (1000 + 2048), 1000, 2048)


def test_oow_order_stays_in_table_so_its_cancel_is_not_unknown() -> None:
    # §12: dropping out-of-window orders from the table turns their cancels into unknown_ref.
    book = BoundedBook(base_ticks=1000, half_window=10)
    assert book.apply(add(1, "B", 100, 100 * 5000)) is None
    assert book.price_oow == 1
    assert not book.bids
    book.apply(Reduce(1, 2, 1, 40))
    book.apply(Delete(1, 3, 1))
    assert book.counters.unknown_ref == 0
    assert not book.orders


def test_replace_moves_between_window_and_out_of_window() -> None:
    book = BoundedBook(base_ticks=1000, half_window=10)
    book.apply(add(1, "B", 100, 100 * 5000))
    bbo = book.apply(Replace(1, 2, 1, 2, 70, 100 * 1000))
    assert bbo is not None and (bbo.bid_price, bbo.bid_size) == (100_000, 70)
    bbo = book.apply(Replace(1, 3, 2, 3, 70, 100 * 5000))
    assert bbo is not None and bbo.bid_size == 0


def test_full_bucket_overflows_and_later_messages_are_unknown() -> None:
    book = BoundedBook(base_ticks=1000, sets=4, ways=2)
    for ref in (4, 8, 12):  # all index 0
        book.apply(add(ref, "B", 100, 100_000))
    assert book.table_overflow == 1
    assert book.bids[100_000] == 200  # never silently counted in the level
    book.apply(Delete(1, 2, 12))
    assert book.counters.unknown_ref == 1


def test_delete_frees_the_slot() -> None:
    book = BoundedBook(base_ticks=1000, sets=4, ways=1)
    book.apply(add(4, "B", 100, 100_000))
    book.apply(Delete(1, 2, 4))
    book.apply(add(8, "B", 100, 100_000))
    assert book.table_overflow == 0


def test_bounded_equals_unbounded_when_limits_never_bind() -> None:
    # If this fails, the limits changed behaviour beyond capacity, and §6.4's invariant is void.
    msgs = generate(7, 20_000)
    wide = BoundedBook(base_ticks=10_000, half_window=1 << 20, sets=1 << 20, ways=1 << 20)
    assert replay(wide, msgs) == replay(OrderBook(), msgs)
    assert wide.table_overflow == 0
    assert wide.price_oow == 0


def test_generator_is_deterministic_and_seed_sensitive() -> None:
    assert generate(1, 2000) == generate(1, 2000)
    assert generate(1, 2000) != generate(2, 2000)


def test_generator_actually_reaches_the_adversarial_cases() -> None:
    # A generator that never triggers these leaves the RTL's error paths untested.
    msgs = generate(3, 50_000, Config(sets=512))
    book = BoundedBook(base_ticks=10_000, half_window=2048, sets=512, ways=4)
    bbos = replay(book, msgs)
    assert book.table_overflow > 0
    assert book.price_oow > 0
    assert book.counters.unknown_ref > 0
    assert any(b.crossed for b in bbos)
    assert any(b.locked for b in bbos)
    kinds = {m[:1] for m in msgs}
    assert {b"R", b"A", b"F", b"E", b"C", b"X", b"D", b"U"} <= kinds
    assert kinds - {b"R", b"A", b"F", b"E", b"C", b"X", b"D", b"U"}  # non-subset noise exists
