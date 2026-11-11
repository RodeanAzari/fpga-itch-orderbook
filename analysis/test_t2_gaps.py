"""Tests for analysis/t2_gaps.py, the evidence for ADR 0012.

Why they matter: the claim "the output writer paced the book" rests on counting, for each
waited BBO, the spacing to the previous BBO's T2. Counting a no-wait BBO, pairing with the
wrong neighbour, or measuring T1 instead of T2 spacing would invent or hide the 65-cycle
signature. Reference: hand-made time sequences.
"""

from __future__ import annotations

import struct

from analysis.t2_gaps import NO_WAIT_MAX, gaps


def rec(t1: int, t2: int) -> bytes:
    return bytes(40) + struct.pack(">QQQ", 0, t1, t2)


def test_counts_waited_records_against_previous_t2() -> None:
    recs = [
        rec(1000, 1017),  # first record: no previous T2, never counted
        rec(1010, 1082),  # waited 72; previous T2 65 earlier
        rec(1100, 1147),  # waited 47; previous T2 65 earlier
        rec(1200, 1217),  # no wait (17): not counted
        rec(1250, 1282),  # waited 32; previous T2 65 earlier
        rec(1270, 1300),  # waited 30; previous T2 18 earlier (T1 spacing is 20: not used)
    ]
    assert gaps(recs) == {(72, 65): 1, (47, 65): 1, (32, 65): 1, (30, 18): 1}


def test_boundary_is_exclusive() -> None:
    assert not gaps([rec(0, 10), rec(100, 100 + NO_WAIT_MAX)])
    assert gaps([rec(0, 10), rec(100, 100 + NO_WAIT_MAX + 1)]) == {(NO_WAIT_MAX + 1, 115): 1}
