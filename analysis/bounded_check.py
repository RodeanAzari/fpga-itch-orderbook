"""Replay a one-symbol extract through the bounded (RTL-configuration) and unbounded books.

Checks the ADR 0002 contract on every message: the out-of-window flag is set exactly when
the bounded BBO differs from the true one. Usage: bounded_check.py <symbol.itch> <base> <out>
"""

from __future__ import annotations

import csv
import sys

from model.book import OrderBook
from model.bounded import BoundedBook
from model.itch import decode, iter_raw


def main(path: str, base_ticks: str, out_csv: str) -> None:
    truth = OrderBook()
    book = BoundedBook(base_ticks=int(base_ticks))
    contract_violations = 0
    bbo_updates = 0
    with open(path, "rb") as f:
        for raw in iter_raw(f):
            msg = decode(raw)
            if msg is None:
                continue
            truth.apply(msg)
            bbo_updates += book.apply(msg) is not None
            *shown, flag = book._state()
            contract_violations += bool(flag) != (tuple(shown) != truth._state())

    row = {
        "sets_per_bank": book.sets_per_bank,
        "ways": book.ways,
        "base_ticks": book.base_ticks,
        "half_window": book.half_window,
        "bbo_updates": bbo_updates,
        "table_overflow": book.table_overflow,
        "ref_overflow": book.ref_overflow,
        "shares_overflow": book.shares_overflow,
        "unknown_ref": book.counters.unknown_ref,
        "price_oow": book.price_oow,
        "bbo_oow": book.bbo_oow,
        "flag_contract_violations": contract_violations,
    }
    with open(out_csv, "w", newline="") as f:
        writer = csv.writer(f)
        writer.writerow(["metric", "value"])
        writer.writerows(row.items())
    for key, value in row.items():
        print(f"{key}: {value}")


if __name__ == "__main__":
    main(*sys.argv[1:4])
