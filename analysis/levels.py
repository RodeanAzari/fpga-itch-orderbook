"""Price-level sizing evidence (ADR 0006) from a one-symbol extract (see itch_extract.py).

Usage: levels.py <symbol.itch> <base_ticks> <out_prefix>
Writes <out_prefix>_levels.csv, one row per side. Runs the bounded book (ADR 0001/0002
geometry) and measures, for the in-window level arrays only:
  - max_level_shares: largest aggregate shares any one level ever held (sets the word width)
  - max_live_levels: most non-empty levels at once (depth the bitmap must track)
  - best_emptied_search: times the best level emptied while other in-window levels remained,
    i.e. the bitmap had to find the next best
  - best_emptied_side_empty: times the best level emptied and the side went empty
  - best_improved: times a new level became the best (no search needed)
  - max_search_ticks / searches_over_64_ticks: distance from the emptied best to the next
    best, i.e. what a linear scan (the rejected alternative) would have to walk
"""

from __future__ import annotations

import csv
import sys
from pathlib import Path

from model.bounded import TICK, BoundedBook
from model.itch import Add, Delete, Reduce, Replace, decode, iter_raw

FIELDS = (
    "side",
    "max_level_shares",
    "max_level_shares_bits",
    "max_live_levels",
    "best_emptied_search",
    "best_emptied_side_empty",
    "best_improved",
    "max_search_ticks",
    "searches_over_64_ticks",
)


def main(path: str, base_ticks: int, prefix: str) -> None:
    book = BoundedBook(base_ticks=base_ticks)
    stats = {s: dict.fromkeys(FIELDS[1:], 0) for s in ("B", "S")}
    with open(path, "rb") as f:
        for raw in iter_raw(f):
            msg = decode(raw)
            if not isinstance(msg, Add | Reduce | Delete | Replace):
                continue
            bid0, _, ask0, _ = book._state()[:4]
            book.apply(msg)
            bid1, _, ask1, _ = book._state()[:4]
            # Levels only grow on an insert, so the touched level is the only candidate max.
            new_ref = msg.ref if isinstance(msg, Add) else getattr(msg, "new_ref", None)
            grown = book.orders.get(new_ref) if new_ref is not None else None
            for side, levels, before, after, better in (
                ("B", book.bids, bid0, bid1, bid1 > bid0),
                ("S", book.asks, ask0, ask1, ask0 == 0 or (ask1 != 0 and ask1 < ask0)),
            ):
                st = stats[side]
                if grown is not None and grown.side == side and grown.price in levels:
                    st["max_level_shares"] = max(st["max_level_shares"], levels[grown.price])
                st["max_live_levels"] = max(st["max_live_levels"], len(levels))
                if before and before not in levels:
                    key = "best_emptied_search" if levels else "best_emptied_side_empty"
                    st[key] += 1
                    if levels:
                        ticks = abs(after - before) // TICK
                        st["max_search_ticks"] = max(st["max_search_ticks"], ticks)
                        st["searches_over_64_ticks"] += ticks > 64
                elif after and after != before and better:
                    st["best_improved"] += 1
    out = Path(f"{prefix}_levels.csv")
    with out.open("w", newline="") as f:
        w = csv.writer(f)
        w.writerow(FIELDS)
        for side, st in stats.items():
            st["max_level_shares_bits"] = st["max_level_shares"].bit_length()
            w.writerow([side, *(st[k] for k in FIELDS[1:])])
    print(out.read_text(), end="")


if __name__ == "__main__":
    main(sys.argv[1], int(sys.argv[2]), sys.argv[3])
