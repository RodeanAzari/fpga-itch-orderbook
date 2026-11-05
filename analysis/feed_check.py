"""Replay a whole day file through MoldUDP64 packetization and the feed decoder (Phase C model).

Usage: feed_check.py <day.gz> <SYMBOL> <out.csv>

Every message of the day is batched into MoldUDP64 packets (max 1,472-byte payload, as the
host replayer will send them), fed to model.moldudp64.FeedDecoder, and the decoder's
counters and output are written as metric,value rows. `records_sha256` fingerprints the
decoded record stream so a later RTL or hardware run can be compared without storing it.
"""

from __future__ import annotations

import csv
import gzip
import hashlib
import io
import sys
from collections import Counter
from dataclasses import asdict

from model.itch import iter_raw
from model.moldudp64 import FeedDecoder, packetize

SESSION = b"20191018AA"


def main(path: str, symbol: str, out: str) -> None:
    dec = FeedDecoder(symbol)
    types: Counter[str] = Counter()
    digest = hashlib.sha256()
    packets = 0
    with gzip.open(path, "rb") as raw:
        msgs = iter_raw(io.BufferedReader(raw, 1 << 20))
        for pkt in packetize(msgs, SESSION):
            packets += 1
            for rec in dec.feed(pkt):
                types[type(rec.msg).__name__] += 1
                digest.update(repr(rec).encode())
            dec.records.clear()  # keep memory flat; the digest carries the stream

    rows: list[tuple[str, object]] = [
        ("symbol", symbol),
        ("locate", dec.locate),
        ("packets", packets),
        ("stale", int(dec.stale)),
    ]
    rows += [(f"seq_{k}", v) for k, v in asdict(dec.tracker.c).items()]
    rows += [(f"feed_{k}", v) for k, v in asdict(dec.c).items()]
    rows += [(f"records_{k}", v) for k, v in sorted(types.items())]
    rows += [("records_total", sum(types.values())), ("records_sha256", digest.hexdigest())]
    with open(out, "w", newline="") as f:
        w = csv.writer(f)
        w.writerow(["metric", "value"])
        w.writerows(rows)
    for k, v in rows:
        print(f"{k}={v}")


if __name__ == "__main__":
    main(*sys.argv[1:4])
