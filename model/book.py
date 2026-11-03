"""Unbounded single-symbol order book: the reference that defines the correct BBO stream."""

from __future__ import annotations

import heapq
from dataclasses import dataclass, field

from model.itch import Add, Delete, Message, Reduce, Replace


@dataclass(frozen=True, slots=True)
class Bbo:
    ts: int
    bid_price: int  # 0 and size 0 when the side is empty
    bid_size: int
    ask_price: int
    ask_size: int

    @property
    def locked(self) -> bool:
        return self.bid_size > 0 and self.ask_size > 0 and self.bid_price == self.ask_price

    @property
    def crossed(self) -> bool:
        return self.bid_size > 0 and self.ask_size > 0 and self.bid_price > self.ask_price


@dataclass(slots=True)
class _Order:
    side: str
    price: int
    shares: int


@dataclass(slots=True)
class Counters:
    unknown_ref: int = 0
    duplicate_ref: int = 0
    over_reduce: int = 0


@dataclass(slots=True)
class OrderBook:
    orders: dict[int, _Order] = field(default_factory=dict)
    bids: dict[int, int] = field(default_factory=dict)
    asks: dict[int, int] = field(default_factory=dict)
    # Lazily cleaned heaps: an entry is live only while its price is still in the level dict.
    _bid_heap: list[int] = field(default_factory=list)  # negated prices
    _ask_heap: list[int] = field(default_factory=list)
    _last: tuple[int, int, int, int] = (0, 0, 0, 0)
    peak_live_orders: int = 0
    counters: Counters = field(default_factory=Counters)

    def apply(self, msg: Message) -> Bbo | None:
        """Apply one message for this symbol. Returns a Bbo only if best price or size changed."""
        if isinstance(msg, Add):
            self._insert(msg.ref, msg.side, msg.price, msg.shares)
        elif isinstance(msg, Reduce):
            self._reduce(msg.ref, msg.shares)
        elif isinstance(msg, Delete):
            self._remove(msg.ref)
        elif isinstance(msg, Replace):
            original = self.orders.get(msg.ref)
            if original is None:
                self.counters.unknown_ref += 1
            else:
                side = original.side  # U carries no side: it comes from the original order
                self._remove(msg.ref)
                self._insert(msg.new_ref, side, msg.price, msg.shares)
        else:
            return None
        return self._bbo_if_changed(msg.ts)

    def _insert(self, ref: int, side: str, price: int, shares: int) -> None:
        if ref in self.orders:
            self.counters.duplicate_ref += 1
            return
        self.orders[ref] = _Order(side, price, shares)
        self.peak_live_orders = max(self.peak_live_orders, len(self.orders))
        levels = self.bids if side == "B" else self.asks
        if price not in levels:
            heap, key = (self._bid_heap, -price) if side == "B" else (self._ask_heap, price)
            heapq.heappush(heap, key)
        levels[price] = levels.get(price, 0) + shares

    def _reduce(self, ref: int, shares: int) -> None:
        order = self.orders.get(ref)
        if order is None:
            self.counters.unknown_ref += 1
            return
        if shares >= order.shares:
            if shares > order.shares:
                self.counters.over_reduce += 1
            self._remove(ref)
            return
        order.shares -= shares
        levels = self.bids if order.side == "B" else self.asks
        levels[order.price] -= shares

    def _remove(self, ref: int) -> None:
        order = self.orders.pop(ref, None)
        if order is None:
            self.counters.unknown_ref += 1
            return
        levels = self.bids if order.side == "B" else self.asks
        levels[order.price] -= order.shares
        if levels[order.price] == 0:
            del levels[order.price]

    def _bbo_if_changed(self, ts: int) -> Bbo | None:
        heap = self._bid_heap
        while heap and -heap[0] not in self.bids:
            heapq.heappop(heap)
        bid = -heap[0] if heap else 0
        heap = self._ask_heap
        while heap and heap[0] not in self.asks:
            heapq.heappop(heap)
        ask = heap[0] if heap else 0
        state = (bid, self.bids.get(bid, 0), ask, self.asks.get(ask, 0))
        if state == self._last:
            return None
        self._last = state
        return Bbo(ts, *state)
