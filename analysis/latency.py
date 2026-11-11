"""Latency distributions from a board run's BBO records (ADR 0011, docs/latency.md).

Usage: python -m analysis.latency --log LOG --out CSV [--hist HIST_CSV]

LOG is a host/capture log in BBO mode. Every BBO record carries T0, T1, T2 (core-clock
cycles, 8 ns). Two metrics, never averaged alone:
  T2-T1  pipeline latency (the headline): exact cycle counts.
  T2-T0  line-rate bound: includes the frame's 100 Mb/s serialization (80 ns per byte)
         and the store-and-forward wait for its FCS. Records with T0 = 0 (T0 lost,
         counted in t0_lost) are left out of it, and their number is reported.
T0 is the captured count, not corrected by the synchronizer constant (docs/latency.md).
Percentiles are nearest-rank: the smallest sample with at least p of the samples at or
below it, so every reported value is a measured one.
"""

from __future__ import annotations

import argparse
import csv
import math
import struct
from collections import Counter
from fractions import Fraction
from pathlib import Path

from analysis.compare import read_log
from model import protocol as P

NS_PER_CYCLE = 8  # 125 MHz core clock (ADR 0009)
PCTS = (50.0, 99.0, 99.9)
COLUMNS = ("metric", "n", "excluded", "min", "p50", "p99", "p99.9", "max", "unit")


def nearest_rank(sorted_vals: list[int], p: float) -> int:
    if not sorted_vals:
        raise ValueError("no samples")
    # exact: in floats 99.9 / 100 * 1000 is 999.0000000000001, one rank too high
    k = max(1, math.ceil(Fraction(str(p)) * len(sorted_vals) / 100))
    return sorted_vals[k - 1]


def latencies(records: list[bytes]) -> tuple[list[int], list[int], int]:
    """(T2-T1 per record, T2-T0 per record with a T0, records without one)."""
    t21, t20 = [], []
    no_t0 = 0
    for r in records:
        t0, t1, t2 = struct.unpack(">QQQ", r[P.TIMES])
        if not t1 < t2 or (t0 and not t0 < t1):
            raise ValueError(f"timestamps out of order: {t0} {t1} {t2}")
        t21.append(t2 - t1)
        if t0:
            t20.append(t2 - t0)
        else:
            no_t0 += 1
    return t21, t20, no_t0


def summary(name: str, vals: list[int], excluded: int) -> list[dict[str, object]]:
    s = sorted(vals)
    cyc = {"min": s[0], "max": s[-1]} | {f"p{p:g}": nearest_rank(s, p) for p in PCTS}
    base = {"metric": name, "n": len(s), "excluded": excluded}
    return [
        base | cyc | {"unit": "cycles"},
        base | {k: v * NS_PER_CYCLE for k, v in cyc.items()} | {"unit": "ns"},
    ]


def main(argv: list[str] | None = None) -> None:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--log", type=Path, required=True)
    ap.add_argument("--out", type=Path, required=True)
    ap.add_argument("--hist", type=Path, help="also write cycles,count per metric")
    a = ap.parse_args(argv)
    cfg, dgs = read_log(a.log)
    if cfg.mode != 0:
        raise SystemExit("latency needs a BBO-mode run (trace records carry no timestamps)")
    # pkt_seq order: the host may reorder datagrams (compare.py); duplicates counted once
    by_seq = {d.pkt_seq: d for d in dgs if d.kind == P.BBO}
    recs = [r for k in sorted(by_seq) for r in by_seq[k].records]
    t21, t20, no_t0 = latencies(recs)
    rows = summary("T2-T1", t21, 0) + summary("T2-T0", t20, no_t0)
    with a.out.open("w", newline="") as f:
        w = csv.DictWriter(f, fieldnames=COLUMNS)
        w.writeheader()
        w.writerows(rows)
    if a.hist:
        with a.hist.open("w", newline="") as f:
            w2 = csv.writer(f)
            w2.writerow(("metric", "cycles", "count"))
            for name, vals in (("T2-T1", t21), ("T2-T0", t20)):
                for c, n in sorted(Counter(vals).items()):
                    w2.writerow((name, c, n))
    for r in rows:
        print(",".join(str(r[c]) for c in COLUMNS))


if __name__ == "__main__":
    main()
