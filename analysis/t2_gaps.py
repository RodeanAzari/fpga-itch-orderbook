"""T2 spacing behind waited BBOs: for each T2-T1 above the no-wait budget, how far the
previous BBO's T2 was (ADR 0012's evidence that the output writer paced the book).

A wait caused by the output shows up as a fixed spacing between consecutive T2s (the
writer's per-record time); a wait on the book's own input does not.

Usage: python -m analysis.t2_gaps --log data/runs/RUN.log --out data/results/X.csv
CSV: t2_minus_t1, prev_t2_gap, count (cycles), only for T2-T1 > NO_WAIT_MAX.
"""

from __future__ import annotations

import argparse
import csv
import struct
from collections import Counter
from pathlib import Path

from analysis.compare import read_log
from model import protocol as P

NO_WAIT_MAX = 24  # docs/latency.md: the largest no-wait T2-T1 (a Replace)


def gaps(recs: list[bytes]) -> Counter[tuple[int, int]]:
    """(T2-T1, T2 - previous T2) -> count, for records that waited."""
    out: Counter[tuple[int, int]] = Counter()
    prev_t2 = None
    for r in recs:
        _, t1, t2 = struct.unpack(">QQQ", r[P.TIMES])
        if prev_t2 is not None and t2 - t1 > NO_WAIT_MAX:
            out[(t2 - t1, t2 - prev_t2)] += 1
        prev_t2 = t2
    return out


def main(argv: list[str] | None = None) -> None:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--log", type=Path, required=True)
    ap.add_argument("--out", type=Path, required=True)
    a = ap.parse_args(argv)
    cfg, dgs = read_log(a.log)
    if cfg.mode != 0:
        raise SystemExit("t2_gaps needs a BBO-mode run")
    by_seq = {d.pkt_seq: d for d in dgs if d.kind == P.BBO}  # as latency.py
    recs = [r for k in sorted(by_seq) for r in by_seq[k].records]
    g = gaps(recs)
    with a.out.open("w", newline="") as f:
        w = csv.writer(f)
        w.writerow(("t2_minus_t1", "prev_t2_gap", "count"))
        for (lat, gap), n in sorted(g.items()):
            w.writerow((lat, gap, n))
    per_lat: Counter[int] = Counter()
    for (lat, _), n in g.items():
        per_lat[lat] += n
    for lat, total in per_lat.most_common(3):
        gap, n = max(((gp, c) for (lt, gp), c in g.items() if lt == lat), key=lambda x: x[1])
        print(f"T2-T1 {lat}: {total} BBOs; previous T2 gap {gap} in {n}")


if __name__ == "__main__":
    main()
