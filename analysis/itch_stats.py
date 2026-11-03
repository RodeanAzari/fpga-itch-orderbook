"""Replay one symbol from a day file through the unbounded model; write Phase A sizing numbers.

Usage: itch_stats.py <day.gz> <SYMBOL> <out_prefix>
Writes <out_prefix>_summary.csv, _by_type.csv and _bbo_1min.csv.
"""

from __future__ import annotations

import csv
import gzip
import io
import sys
from collections import Counter
from pathlib import Path

from model.book import Bbo, OrderBook
from model.bounded import TICK, in_window
from model.itch import Add, Directory, MalformedMessage, Replace, decode, iter_raw

OPEN_NS = int(9.5 * 3600e9)
HALF_WINDOW = 2048  # ticks; CLAUDE.md §6.4 window of 4,096
MINUTE_NS = int(60e9)


def main(path: str, symbol: str, prefix: str) -> None:
    by_type: Counter[str] = Counter()
    symbol_types: Counter[str] = Counter()
    add_prices: Counter[int] = Counter()
    book = OrderBook()
    locate_bytes: bytes | None = None
    malformed = max_ref = 0
    base: int | None = None
    bbos: list[Bbo] = []

    with gzip.open(path, "rb") as raw:
        for msg in iter_raw(io.BufferedReader(raw, 1 << 20)):  # type: ignore[arg-type]
            kind = msg[:1].decode("latin-1")
            by_type[kind] += 1
            if locate_bytes is None:
                if kind == "R":
                    directory = decode(msg)
                    if isinstance(directory, Directory) and directory.stock == symbol:
                        locate_bytes = msg[1:3]
                continue
            if msg[1:3] != locate_bytes:
                continue
            symbol_types[kind] += 1
            try:
                decoded = decode(msg)
            except MalformedMessage:
                malformed += 1
                continue
            if decoded is None:
                continue
            if isinstance(decoded, Add):
                add_prices[decoded.price] += 1
                max_ref = max(max_ref, decoded.ref)
            elif isinstance(decoded, Replace):
                add_prices[decoded.price] += 1
                max_ref = max(max_ref, decoded.new_ref)
            bbo = book.apply(decoded)
            if bbo is not None:
                bbos.append(bbo)

    if locate_bytes is None:
        raise SystemExit(f"{symbol} has no R message: wrong symbol, or file not from start of day")

    # Base: mid of the first two-sided, uncrossed BBO at or after the open.
    regular = [b for b in bbos if b.ts >= OPEN_NS and b.bid_size and b.ask_size and not b.crossed]
    if not regular:
        raise SystemExit(f"{symbol} never had a two-sided book after the open")
    base = (regular[0].bid_price + regular[0].ask_price) // (2 * TICK)

    two_sided = [b for b in bbos if b.bid_size and b.ask_size]
    contained = sum(
        in_window(b.bid_price, base, HALF_WINDOW) and in_window(b.ask_price, base, HALF_WINDOW)
        for b in two_sided
    )
    total_adds = sum(add_prices.values())
    oow_adds = sum(n for p, n in add_prices.items() if not in_window(p, base, HALF_WINDOW))
    summary = {
        "symbol": symbol,
        "locate": int.from_bytes(locate_bytes),
        "messages_total": sum(by_type.values()),
        "messages_symbol": sum(symbol_types.values()),
        "peak_live_orders": book.peak_live_orders,
        "max_order_ref": max_ref,
        "min_add_price_ticks": min(add_prices) // TICK,
        "max_add_price_ticks": max(add_prices) // TICK,
        "window_base_ticks": base,
        "bbo_events": len(bbos),
        "bbo_two_sided": len(two_sided),
        "bbo_two_sided_in_window": contained,
        "bbo_crossed": sum(b.crossed for b in bbos),
        "bbo_locked": sum(b.locked for b in bbos),
        "adds_total": total_adds,
        "adds_out_of_window": oow_adds,
        "unknown_ref": book.counters.unknown_ref,
        "duplicate_ref": book.counters.duplicate_ref,
        "over_reduce": book.counters.over_reduce,
        "malformed": malformed,
    }

    Path(prefix).parent.mkdir(parents=True, exist_ok=True)
    with open(f"{prefix}_summary.csv", "w", newline="") as f:
        writer = csv.writer(f)
        writer.writerow(["metric", "value"])
        writer.writerows(summary.items())
    with open(f"{prefix}_by_type.csv", "w", newline="") as f:
        writer = csv.writer(f)
        writer.writerow(["type", "all_symbols", "symbol"])
        writer.writerows((t, n, symbol_types[t]) for t, n in sorted(by_type.items()))
    with open(f"{prefix}_bbo_1min.csv", "w", newline="") as f:
        writer = csv.writer(f)
        writer.writerow(["minute", "bid_price", "bid_size", "ask_price", "ask_size"])
        last_per_minute = {b.ts // MINUTE_NS: b for b in bbos}
        writer.writerows(
            (m, b.bid_price, b.bid_size, b.ask_price, b.ask_size)
            for m, b in sorted(last_per_minute.items())
        )
    for key, value in summary.items():
        print(f"{key}: {value}")


if __name__ == "__main__":
    main(*sys.argv[1:4])
