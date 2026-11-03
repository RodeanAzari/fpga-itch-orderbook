"""Book with the RTL's capacity limits (CLAUDE.md §6.3-6.4): the scoreboard's reference."""

from __future__ import annotations

from collections import Counter
from dataclasses import dataclass, field

from model.book import OrderBook

TICK = 100  # ITCH price units per cent (4 implied decimals)


def in_window(price: int, base_ticks: int, half_window: int) -> bool:
    """Same range as a 2*half_window array indexed by ticks - base + half_window."""
    return -half_window <= price // TICK - base_ticks < half_window


@dataclass(slots=True)
class BoundedBook(OrderBook):
    """Limited table and price window. Out-of-window orders stay in the table but touch no level."""

    base_ticks: int = 0
    half_window: int = 2048
    sets: int = 512  # power of two: the set index is the low bits of the order ref
    ways: int = 4
    table_overflow: int = 0
    price_oow: int = 0
    _fill: Counter[int] = field(default_factory=Counter)

    def _has_level(self, price: int) -> bool:
        return in_window(price, self.base_ticks, self.half_window)

    def _insert(self, ref: int, side: str, price: int, shares: int) -> None:
        if ref not in self.orders:  # a duplicate takes no slot; the base class counts it
            index = ref & (self.sets - 1)
            if self._fill[index] >= self.ways:
                self.table_overflow += 1  # untracked: its later messages count as unknown_ref
                return
            self._fill[index] += 1
            if not self._has_level(price):
                self.price_oow += 1
        OrderBook._insert(self, ref, side, price, shares)

    def _remove(self, ref: int) -> None:
        if ref in self.orders:
            self._fill[ref & (self.sets - 1)] -= 1
        OrderBook._remove(self, ref)
