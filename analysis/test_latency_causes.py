"""Tests for analysis/latency_causes.empties_best, which sorts the board's BBOs into the
cycle-budget cases of docs/latency.md.

Why they matter: a wrong "empties the best" label would move BBOs between the 17/23 and
24/29 budgets and make a real wait look like a budget case (or the reverse). Reference:
the definition (the message takes the last shares at the best price of its order's side),
on hand-built books.
"""

from __future__ import annotations

from analysis.latency_causes import empties_best
from model.bounded import TICK, BoundedBook
from model.itch import Add, Delete, Reduce, Replace

BASE = 20_000  # ticks


def book_with(*orders: tuple[int, str, int, int]) -> BoundedBook:
    b = BoundedBook(base_ticks=BASE)
    for ref, side, shares, ticks in orders:
        b.apply(Add(0, 1, ref, side, shares, "AAPL", ticks * TICK))
    return b


def test_delete_last_order_at_best() -> None:
    b = book_with((1, "B", 100, BASE), (2, "B", 100, BASE - 5), (3, "S", 100, BASE + 5))
    assert empties_best(b, Delete(0, 1, 1))
    assert not empties_best(b, Delete(0, 1, 2))  # not the best
    assert empties_best(b, Delete(0, 1, 3))  # ask side: best is the lowest


def test_shared_level_and_partial_reduce() -> None:
    b = book_with((1, "B", 100, BASE), (2, "B", 50, BASE))
    assert not empties_best(b, Delete(0, 1, 1))  # 2 keeps the level
    b = book_with((1, "B", 100, BASE))
    assert not empties_best(b, Reduce(0, 1, 1, 99))
    assert empties_best(b, Reduce(0, 1, 1, 100))
    assert empties_best(b, Reduce(0, 1, 1, 150))  # over-reduce still removes the order


def test_replace_unknown_and_out_of_window() -> None:
    b = book_with((1, "S", 100, BASE), (2, "S", 100, BASE + 3000))  # 2: out of window
    assert empties_best(b, Replace(0, 1, 1, 9, 100, (BASE + 1) * TICK))
    assert not empties_best(b, Delete(0, 1, 2))  # held in no level
    assert not empties_best(b, Delete(0, 1, 77))  # unknown ref
    assert not empties_best(b, Add(0, 1, 5, "B", 1, "AAPL", BASE * TICK))
