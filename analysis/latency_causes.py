"""T2-T1 per cause message: the ITCH type and whether it removed the last shares at the
best price (docs/latency.md: the book takes longer when the best level empties).

Each BBO is joined to its cause message by MoldUDP64 sequence number; the golden model's
book (model.bounded) gives the state just before that message.

Usage: python -m analysis.latency_causes --log data/runs/RUN.log --packets PKTS --out X.csv
CSV: msg_type, empties_best, t2_minus_t1, count.
"""

from __future__ import annotations

import argparse
import csv
import struct
from collections import Counter
from pathlib import Path

from analysis.compare import base_ticks, read_log
from model import protocol as P
from model.bounded import BoundedBook
from model.itch import Delete, Reduce, Replace, iter_raw
from model.moldudp64 import FeedDecoder

REMOVES = (Reduce, Delete, Replace)  # take shares off an order (Reduce = E, C, X)


def empties_best(book: BoundedBook, msg: object) -> bool:
    """msg takes the last shares off the best price on its order's side."""
    if not isinstance(msg, REMOVES):
        return False
    order = book.orders.get(msg.ref)
    if order is None:
        return False
    levels = book.bids if order.side == "B" else book.asks
    if order.price not in levels:  # out of window (ADR 0002): held in no level
        return False
    best = max(levels) if order.side == "B" else min(levels)
    gone = order.shares if isinstance(msg, (Delete, Replace)) else min(msg.shares, order.shares)
    return order.price == best and levels[order.price] == gone


def causes(log: Path, packets: Path) -> Counter[tuple[str, bool, int]]:
    cfg, dgs = read_log(log)
    by_seq = {d.pkt_seq: d for d in dgs if d.kind == P.BBO}
    lat: dict[int, int] = {}
    for k in sorted(by_seq):
        for r in by_seq[k].records:
            _, t1, t2 = struct.unpack(">QQQ", r[P.TIMES])
            lat[int.from_bytes(r[32:40])] = t2 - t1  # docs/protocol.md: mold_seq at 32
    book = BoundedBook(base_ticks=base_ticks(cfg))
    dec = FeedDecoder(cfg.symbol, locate=cfg.locate if cfg.locate_valid else None)
    out: Counter[tuple[str, bool, int]] = Counter()
    seen: set[int] = set()
    for p in iter_raw(packets.open("rb")):
        for rec in dec.feed(p):
            if rec.seq in seen:
                continue
            seen.add(rec.seq)
            if rec.seq in lat:
                out[(type(rec.msg).__name__, empties_best(book, rec.msg), lat[rec.seq])] += 1
            book.apply(rec.msg)
    return out


def main(argv: list[str] | None = None) -> None:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--log", type=Path, required=True)
    ap.add_argument("--packets", type=Path, required=True)
    ap.add_argument("--out", type=Path, required=True)
    a = ap.parse_args(argv)
    c = causes(a.log, a.packets)
    with a.out.open("w", newline="") as f:
        w = csv.writer(f)
        w.writerow(("msg_type", "empties_best", "t2_minus_t1", "count"))
        for (kind, emp, lat), n in sorted(c.items()):
            w.writerow((kind, int(emp), lat, n))
            print(kind, int(emp), lat, n)


if __name__ == "__main__":
    main()
