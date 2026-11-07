"""The bounded model is what the RTL is scored against; these pin its limit semantics."""

from model.book import Bbo, OrderBook
from model.bounded import BoundedBook, bucket, colliding_refs, in_window
from model.generator import Config, generate
from model.itch import Add, Delete, Message, Reduce, Replace, decode


def add(ref: int, side: str, shares: int, price: int) -> Add:
    return Add(1, 1, ref, side, shares, "T", price)


def decoded(msgs: list[bytes]) -> list[Message]:
    return [m for m in map(decode, msgs) if m is not None]


def replay(book: OrderBook, msgs: list[bytes]) -> list[Bbo]:
    return [b for m in decoded(msgs) if (b := book.apply(m)) is not None]


def test_window_matches_array_bounds() -> None:
    # Index = ticks - base + half must fit in 0..2*half-1: an off-by-one here is an RTL overrun.
    assert in_window(100 * (1000 - 2048), 1000, 2048)
    assert not in_window(100 * (1000 - 2049), 1000, 2048)
    assert in_window(100 * (1000 + 2047), 1000, 2048)
    assert not in_window(100 * (1000 + 2048), 1000, 2048)


def test_colliding_refs_really_share_both_buckets() -> None:
    # The generator's overflow stimulus is only as good as this helper.
    for bits in (1, 9, 13):
        for ref in colliding_refs(bits, 8):
            assert bucket(0, ref, bits) == 0 and bucket(1, ref, bits) == 0


def test_oow_order_stays_in_table_so_its_cancel_is_not_unknown() -> None:
    # §12: dropping out-of-window orders from the table turns their cancels into unknown_ref.
    book = BoundedBook(base_ticks=1000, half_window=10)
    book.apply(add(1, "B", 100, 100 * 900))  # below the window: worse than any in-window bid
    assert book.price_oow == 1
    assert not book.bids
    book.apply(Reduce(1, 2, 1, 40))
    book.apply(Delete(1, 3, 1))
    assert book.counters.unknown_ref == 0
    assert not book.orders


def test_replace_moves_between_window_and_out_of_window() -> None:
    book = BoundedBook(base_ticks=1000, half_window=10)
    book.apply(add(1, "B", 100, 100 * 900))
    bbo = book.apply(Replace(1, 2, 1, 2, 70, 100 * 1000))
    assert bbo is not None and (bbo.bid_price, bbo.bid_size, bbo.out_of_window) == (
        100_000,
        70,
        False,
    )
    bbo = book.apply(Replace(1, 3, 2, 3, 70, 100 * 900))
    assert bbo is not None and bbo.bid_size == 0 and bbo.out_of_window


def test_flag_marks_a_better_price_hidden_beyond_the_window() -> None:
    # A bid above the window beats every bid the arrays can see: the shown BBO is wrong.
    book = BoundedBook(base_ticks=1000, half_window=10)
    book.apply(add(1, "B", 100, 100 * 1000))
    bbo = book.apply(add(2, "B", 100, 100 * 1100))
    assert bbo is not None and bbo.out_of_window and bbo.bid_price == 100_000
    bbo = book.apply(Delete(1, 2, 2))
    assert bbo is not None and not bbo.out_of_window
    assert book.bbo_oow == 1


def test_worse_price_beyond_window_is_harmless_while_the_side_has_levels() -> None:
    book = BoundedBook(base_ticks=1000, half_window=10)
    book.apply(add(1, "B", 100, 100 * 1000))
    assert book.apply(add(2, "B", 100, 100 * 900)) is None  # nothing visible changed
    bbo = book.apply(Delete(1, 2, 1))  # now the only bid is out of window
    assert bbo is not None and bbo.bid_size == 0 and bbo.out_of_window


def test_flag_is_set_exactly_when_bounded_bbo_differs_from_the_truth() -> None:
    # The ADR 0002 contract: no flag means the published BBO equals the unbounded book's.
    truth = OrderBook()
    book = BoundedBook(base_ticks=10_000, half_window=2048, sets_per_bank=1 << 16)
    flagged = 0
    for msg in decoded(generate(11, 30_000, Config(p_collide=0.0))):
        truth.apply(msg)
        book.apply(msg)
        *shown, flag = book._state()
        assert bool(flag) == (tuple(shown) != truth._state()), msg
        flagged += bool(flag)
    assert book.table_overflow == 0
    assert flagged > 0  # otherwise this test proves nothing


def test_full_bucket_pair_overflows_and_later_messages_are_unknown() -> None:
    a, b, c = colliding_refs(1, 3)
    book = BoundedBook(base_ticks=1000, sets_per_bank=2, ways=1)
    for ref in (a, b, c):  # a fills bank 0, b fills bank 1, c has nowhere to go
        book.apply(add(ref, "B", 100, 100_000))
    assert book.table_overflow == 1
    assert book.bids[100_000] == 200  # never silently counted in the level
    book.apply(Delete(1, 2, c))
    assert book.counters.unknown_ref == 1


def test_delete_frees_the_slot() -> None:
    a, b, c = colliding_refs(1, 3)
    book = BoundedBook(base_ticks=1000, sets_per_bank=2, ways=1)
    book.apply(add(a, "B", 100, 100_000))
    book.apply(add(b, "B", 100, 100_000))
    book.apply(Delete(1, 2, a))
    book.apply(add(c, "B", 100, 100_000))
    assert book.table_overflow == 0


def test_entry_width_limits_are_counted_not_truncated() -> None:
    book = BoundedBook(base_ticks=1000)
    book.apply(add(1 << 32, "B", 100, 100_000))
    book.apply(add(5, "B", 1 << 20, 100_000))
    assert (book.ref_overflow, book.shares_overflow) == (1, 1)
    assert not book.orders and not book.bids


def test_bounded_equals_unbounded_when_limits_never_bind() -> None:
    # If this fails, the limits changed behaviour beyond capacity, and §6.4's invariant is void.
    msgs = generate(7, 20_000)
    wide = BoundedBook(base_ticks=10_000, half_window=1 << 20, sets_per_bank=1 << 16, ways=64)
    assert replay(wide, msgs) == replay(OrderBook(), msgs)
    assert wide.table_overflow == 0
    assert wide.price_oow == 0


def test_generator_is_deterministic_and_seed_sensitive() -> None:
    assert generate(1, 2000) == generate(1, 2000)
    assert generate(1, 2000) != generate(2, 2000)


def test_generator_actually_reaches_the_adversarial_cases() -> None:
    # A generator that never triggers these leaves the RTL's error paths untested.
    msgs = generate(3, 50_000, Config(sets_per_bank=512))
    book = BoundedBook(base_ticks=10_000, half_window=2048, sets_per_bank=512, ways=4)
    bbos = replay(book, msgs)
    assert book.table_overflow > 0
    assert book.price_oow > 0
    assert book.bbo_oow > 0
    assert book.counters.unknown_ref > 0
    assert any(b.crossed for b in bbos)
    assert any(b.locked for b in bbos)
    kinds = {m[:1] for m in msgs}
    assert {b"R", b"A", b"F", b"E", b"C", b"X", b"D", b"U"} <= kinds
    assert kinds - {b"R", b"A", b"F", b"E", b"C", b"X", b"D", b"U"}  # non-subset noise exists


def test_subpenny_in_window_stays_out_of_levels_and_marks_stale() -> None:
    # ADR 0006: a 1-tick array would merge $100.0050 into $100.00 and publish a wrong size.
    book = BoundedBook(base_ticks=1000, half_window=10)
    book.apply(add(1, "B", 100, 100_000))
    bbo = book.apply(add(2, "B", 50, 100_050))  # better than the best, but not storable
    assert bbo is None  # the published book did not move...
    assert book.bids == {100_000: 100}
    assert book.price_subpenny == 1 and book.price_oow == 0
    assert book.stale  # ...so it is marked stale instead of silently wrong
    book.apply(Delete(1, 2, 2))  # it is still in the table: its cancel resolves
    assert book.counters.unknown_ref == 0 and 2 not in book.orders
    assert book.stale  # sticky: the book was wrong for a while
    assert all(v == 0 for v in book._oow.values())


def test_subpenny_outside_window_is_out_of_window_not_subpenny() -> None:
    # The day's 10 sub-penny prices are all far stubs: they must stay on ADR 0002's path
    # (flagged exactly), not mark the book stale.
    book = BoundedBook(base_ticks=1000, half_window=10)
    book.apply(add(1, "S", 100, 100 * 2000 + 1))
    assert (book.price_oow, book.price_subpenny) == (1, 0)
    assert book._oow["S", "worse"] == 1
    book.apply(Delete(1, 2, 1))
    assert book._oow["S", "worse"] == 0
    assert not book.stale


def test_every_silent_divergence_marks_stale_and_nothing_else_does() -> None:
    def fresh() -> BoundedBook:
        return BoundedBook(base_ticks=1000, half_window=10)

    clean = fresh()
    clean.apply(add(1, "B", 100, 100_000))
    clean.apply(add(2, "B", 100, 100 * 900))  # out of window: flagged, not stale
    clean.apply(Reduce(1, 2, 1, 100))  # exact full execution
    assert not clean.stale

    a, b, c = colliding_refs(1, 3)
    table_full = BoundedBook(base_ticks=1000, sets_per_bank=2, ways=1)
    for ref in (a, b, c):
        table_full.apply(add(ref, "B", 100, 100_000))
    ref_wide, shares_wide, unknown, duplicate, over = fresh(), fresh(), fresh(), fresh(), fresh()
    ref_wide.apply(add(1 << 32, "B", 100, 100_000))
    shares_wide.apply(add(1, "B", 1 << 20, 100_000))
    unknown.apply(Delete(1, 2, 99))
    duplicate.apply(add(1, "B", 100, 100_000))
    duplicate.apply(add(1, "B", 100, 100_000))
    over.apply(add(1, "B", 100, 100_000))
    over.apply(Reduce(1, 2, 1, 101))
    for book in (table_full, ref_wide, shares_wide, unknown, duplicate, over):
        assert book.stale


def test_first_message_that_changes_nothing_publishes_nothing() -> None:
    # The RTL compares against an all-empty, unflagged BBO from reset; so must the model.
    for book in (OrderBook(), BoundedBook(base_ticks=1000, half_window=10)):
        assert book.apply(Delete(1, 1, 99)) is None
        assert book.counters.unknown_ref == 1


def test_zero_share_order_is_live_but_adds_no_level() -> None:
    # A 0-size level would be "occupied" in the model but not in the RTL's bitmap.
    for book in (OrderBook(), BoundedBook(base_ticks=1000, half_window=10)):
        assert book.apply(add(1, "B", 0, 100_000)) is None
        assert 1 in book.orders and not book.bids
        bbo = book.apply(add(2, "B", 100, 100_000))
        assert bbo is not None and (bbo.bid_price, bbo.bid_size) == (100_000, 100)
        assert book.apply(Delete(1, 2, 1)) is None  # resolves, and the level keeps 100
        assert book.bids == {100_000: 100} and book.counters.unknown_ref == 0


def test_max_live_caps_the_book_and_is_reached() -> None:
    # random_1m relies on this to stay below table capacity; uncapped, this seed passes 500.
    def peak(cfg: Config) -> int:
        book = OrderBook()
        replay(book, generate(3, 20_000, cfg))
        return book.peak_live_orders

    assert peak(Config()) > 2_000
    # The cap binds generator-tracked orders; a locked/crossed episode adds two more briefly.
    assert 490 <= peak(Config(max_live=500)) <= 502
