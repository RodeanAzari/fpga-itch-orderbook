"""Book with the RTL's capacity limits: the scoreboard's reference for the DUT.

Order table: 2-bank d-left hash (ADR 0001). Price window: fixed base, out-of-window orders
stay in the table and are tracked by edge so the true BBO can be flagged (ADR 0002). Levels
are 1-tick slots, so an in-window sub-penny order stays in the table but out of the levels,
and marks the book stale (ADR 0006).
"""

from __future__ import annotations

from collections import Counter
from dataclasses import dataclass, field

from model.book import Bbo, OrderBook

TICK = 100  # ITCH price units per cent (4 implied decimals)
REF_BITS = 32
SHARES_BITS = 20
MULTIPLIERS = (0x9E3779B1, 0x85EBCA6B)  # odd: each bank's hash is a bijection on 32-bit refs


def in_window(price: int, base_ticks: int, half_window: int) -> bool:
    """Same range as a 2*half_window array indexed by ticks - base + half_window."""
    return -half_window <= price // TICK - base_ticks < half_window


def bucket(bank: int, ref: int, set_bits: int) -> int:
    """Top set_bits of (ref * M_bank) mod 2^32. The low bits of the product are the tag."""
    return (ref * MULTIPLIERS[bank] & 0xFFFFFFFF) >> (REF_BITS - set_bits)


def colliding_refs(set_bits: int, count: int) -> list[int]:
    """Refs whose candidate buckets are set 0 in both banks: d-left's worst case.

    Inverts bank 0's hash, so every candidate already lands in bank 0's set 0.
    """
    inverse = pow(MULTIPLIERS[0], -1, 1 << REF_BITS)
    found: list[int] = []
    product = 1  # products below 2^(32 - set_bits) are exactly bank 0's set 0
    while len(found) < count:
        ref = product * inverse & 0xFFFFFFFF
        # Skip small refs so these never clash with a generator's sequential refs.
        if ref >= 1 << 20 and bucket(1, ref, set_bits) == 0:
            found.append(ref)
        product += 1
        if product >> (REF_BITS - set_bits):
            raise ValueError(f"only {len(found)} colliding refs exist for {set_bits} set bits")
    return found


@dataclass(slots=True)
class BoundedBook(OrderBook):
    base_ticks: int = 0
    half_window: int = 2048
    sets_per_bank: int = 8192  # power of two
    ways: int = 4
    table_overflow: int = 0
    ref_overflow: int = 0
    shares_overflow: int = 0
    price_oow: int = 0
    price_subpenny: int = 0  # in-window orders whose price is not a whole tick (ADR 0006)
    bbo_oow: int = 0  # BBO updates published with out_of_window set
    _fill: list[Counter[int]] = field(default_factory=lambda: [Counter(), Counter()])
    _slot: dict[int, tuple[int, int]] = field(default_factory=dict)  # ref -> (bank, set)
    # Live out-of-window orders per (side, edge). "better" beats every in-window price.
    _oow: Counter[tuple[str, str]] = field(default_factory=Counter)
    # Same shape as _state() (BBO plus the out-of-window flag), so the first message is
    # published only if it really changes the empty book.
    _last: tuple[int, ...] = (0, 0, 0, 0, False)

    @property
    def stale(self) -> bool:
        """Sticky: some order is untracked, or tracked without its level, so the published
        book may differ from the feed's in a way no flag shows (ADR 0006). Out-of-window
        orders do not count: ADR 0002's flag covers them exactly."""
        c = self.counters
        return bool(
            self.table_overflow
            or self.ref_overflow
            or self.shares_overflow
            or self.price_subpenny
            or c.unknown_ref
            or c.duplicate_ref
            or c.over_reduce
        )

    def _in_window(self, price: int) -> bool:
        return in_window(price, self.base_ticks, self.half_window)

    def _has_level(self, price: int) -> bool:
        return self._in_window(price) and price % TICK == 0

    def _edge(self, side: str, price: int) -> str:
        above = price // TICK - self.base_ticks >= self.half_window
        return "better" if above == (side == "B") else "worse"

    def _insert(self, ref: int, side: str, price: int, shares: int) -> None:
        if ref in self.orders:  # the base class counts the duplicate; no slot is taken
            OrderBook._insert(self, ref, side, price, shares)
            return
        if ref >> REF_BITS:
            self.ref_overflow += 1
            return
        if shares >> SHARES_BITS:
            self.shares_overflow += 1
            return
        bits = self.sets_per_bank.bit_length() - 1
        # d-left: the emptier candidate bucket wins, ties go to bank 0.
        fill, bank = min((self._fill[b][bucket(b, ref, bits)], b) for b in (0, 1))
        if fill >= self.ways:
            self.table_overflow += 1  # untracked: its later messages count as unknown_ref
            return
        index = bucket(bank, ref, bits)
        self._fill[bank][index] += 1
        self._slot[ref] = (bank, index)
        if not self._in_window(price):
            self.price_oow += 1
            self._oow[side, self._edge(side, price)] += 1
        elif not self._has_level(price):
            self.price_subpenny += 1
        OrderBook._insert(self, ref, side, price, shares)

    def _remove(self, ref: int) -> None:
        order = self.orders.get(ref)
        if order is not None:
            bank, index = self._slot.pop(ref)
            self._fill[bank][index] -= 1
            if not self._in_window(order.price):
                self._oow[order.side, self._edge(order.side, order.price)] -= 1
        OrderBook._remove(self, ref)

    def _state(self) -> tuple[int, ...]:
        state = OrderBook._state(self)
        flagged = any(
            self._oow[side, "better"] or (not levels and self._oow[side, "worse"])
            for side, levels in (("B", self.bids), ("S", self.asks))
        )
        return (*state, flagged)

    def _bbo_if_changed(self, ts: int) -> Bbo | None:
        bbo = OrderBook._bbo_if_changed(self, ts)
        if bbo is not None and bbo.out_of_window:
            self.bbo_oow += 1
        return bbo
