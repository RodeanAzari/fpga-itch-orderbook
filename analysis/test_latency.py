"""Tests for analysis/latency.py: the percentiles a latency claim rests on.

Why they matter: a percentile that interpolates reports a latency nobody measured, and an
off-by-one rank moves p99.9 to a different sample on a 1,000-record run. Reference:
nearest-rank by definition (the ceil(p * n)-th smallest), checked on hand-made samples.
"""

from __future__ import annotations

import struct

import pytest

from analysis.latency import latencies, nearest_rank, paired_t20, summary
from model import protocol as P


def test_nearest_rank_by_definition() -> None:
    s = list(range(1, 1001))  # 1..1000
    assert nearest_rank(s, 50) == 500
    assert nearest_rank(s, 99) == 990
    assert nearest_rank(s, 99.9) == 999
    assert nearest_rank(s, 100) == 1000
    assert nearest_rank([7], 99.9) == 7
    assert nearest_rank([1, 2, 3, 4], 50) == 2  # no interpolation to 2.5
    assert nearest_rank([1, 2, 3, 4], 51) == 3
    with pytest.raises(ValueError):
        nearest_rank([], 50)


def rec(t0: int, t1: int, t2: int) -> bytes:
    return bytes(40) + struct.pack(">QQQ", t0, t1, t2)


def test_latencies_and_lost_t0() -> None:
    t21, t20, no_t0 = latencies([rec(100, 150, 167), rec(0, 300, 314), rec(400, 500, 524)])
    assert t21 == [17, 14, 24]
    assert t20 == [67, 124]  # the record without a T0 is left out, and counted
    assert no_t0 == 1


def test_out_of_order_times_are_rejected() -> None:
    with pytest.raises(ValueError):
        latencies([rec(100, 90, 120)])
    with pytest.raises(ValueError):
        latencies([rec(0, 300, 300)])  # T1 < T2 is checked without a T0 too


def test_summary_in_cycles_and_ns() -> None:
    cyc, ns = summary("T2-T1", [17] * 998 + [24, 125], 0)
    assert (cyc["p50"], cyc["p99"], cyc["p99.9"], cyc["max"]) == (17, 17, 24, 125)
    assert (ns["min"], ns["max"], ns["unit"]) == (136, 1000, "ns")


def flagged(r: bytes, flags: int) -> bytes:
    return r[:2] + flags.to_bytes(2) + r[4:]


def test_paired_t20_per_record() -> None:
    """Same feed on two builds: the saving is per record (base minus this), the
    cut_through flag and the times may differ, anything else may not."""
    base = [rec(100, 900, 917), rec(0, 950, 967), rec(1000, 1900, 1923)]
    ct = [
        flagged(r, P.CUT_THROUGH)
        for r in (rec(800, 900, 917), rec(900, 950, 967), rec(1700, 1900, 1925))
    ]
    assert paired_t20(base, ct) == ([817 - 117, 923 - 225], 1)  # T0 lost in base: left out
    with pytest.raises(ValueError, match="length"):
        paired_t20(base, ct[:2])
    other = [*ct[:2], flagged(ct[2], P.CUT_THROUGH | P.STALE)]
    with pytest.raises(ValueError, match="record 2"):
        paired_t20(base, other)
    swapped = [ct[0], ct[2], ct[1]]
    assert paired_t20(base, swapped)[0] != paired_t20(base, ct)[0]
