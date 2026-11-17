"""pytest: the coverage samplers in tb/cocotb/cov.py, on hand-worked cases.

Why: coverage closure is only as true as its samplers. A search classified by the wrong
group arithmetic, a fill read after the insert instead of before, or a lane offset off by
the length prefix would report bins as hit that the RTL never saw. Each expected value
below is worked out by hand from the RTL's structure (book_levels: 64 levels per L2 word;
order_table: d-left, emptier bucket, ties to bank 0; MoldUDP64: 20-byte header, 2-byte
length prefix), not from cov.py.
"""

from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "cocotb"))

import cov  # noqa: E402
from cocotb_coverage.coverage import coverage_db  # noqa: E402

from model.bounded import TICK, colliding_refs  # noqa: E402
from model.itch import Add, Delete, Reduce, Replace  # noqa: E402
from model.moldudp64 import end_of_session, heartbeat, pack  # noqa: E402

BASE, HALF = 3000, 2048
LOW = BASE - HALF  # tick of window index 0


def hits(item: str) -> dict:
    return dict(coverage_db[item].detailed_coverage)


def delta(item: str, before: dict) -> dict:
    return {k: v - before.get(k, 0) for k, v in hits(item).items() if v != before.get(k, 0)}


def px(idx: int) -> int:
    return (LOW + idx) * TICK


def add(ref: int, side: str, idx: int, shares: int = 100) -> tuple[int, Add]:
    return ord("A"), Add(1, 0, ref, side, shares, "TEST", px(idx))


def dele(ref: int) -> tuple[int, Delete]:
    return ord("D"), Delete(1, 0, ref)


def book(recs) -> None:
    cov.sample_book(recs, BASE, HALF, 2048, 16)


def test_search_relation_by_l2_group() -> None:
    b = hits("levels.side_x_search")
    book([add(1, "B", 10), add(2, "B", 20), dele(2)])  # 20 -> 10: both in group 0
    book([add(1, "B", 63), add(2, "B", 64), dele(2)])  # 64 (group 1) -> 63 (group 0)
    book([add(1, "B", 10), add(2, "B", 200), dele(2)])  # group 3 -> group 0
    book([add(1, "S", 10), add(2, "S", 100), dele(1)])  # ask best 10 (g0) -> 100 (g1)
    book([add(1, "S", 10), dele(1)])  # nothing left on the side
    assert delta("levels.side_x_search", b) == {
        ("B", "same_group"): 1,
        ("B", "next_group"): 1,
        ("B", "far_group"): 1,
        ("S", "next_group"): 1,
        ("S", "side_empty"): 1,
    }


def test_no_search_unless_the_best_level_empties() -> None:
    b = hits("levels.side_x_search")
    book([add(1, "B", 10), add(2, "B", 20), dele(1)])  # not the best
    book([add(1, "B", 20), add(2, "B", 20), dele(1)])  # best level keeps order 2
    book([add(1, "B", 10), add(2, "B", 20), (ord("E"), Reduce(1, 0, 2, 50))])  # partial
    book([add(1, "B", 5000), dele(1)])  # out of window: no level, no bitmap
    assert delta("levels.side_x_search", b) == {}
    book([add(1, "B", 10), add(2, "B", 20), (ord("E"), Reduce(1, 0, 2, 100))])  # to zero
    book([add(1, "B", 10), add(2, "B", 20), (ord("U"), Replace(1, 0, 2, 3, 100, px(5)))])
    assert delta("levels.side_x_search", b) == {("B", "same_group"): 2}


def test_fill_is_read_before_the_insert_d_left() -> None:
    b = hits("table.fill")
    refs = colliding_refs(3, 5)  # same bucket pair in both banks at 8 sets per bank
    cov.sample_book([add(r, "B", 10) for r in refs], BASE, HALF, 8, 2)
    # bank0 0, bank1 0 -> 0; then (1, 0) -> 0; (1, 1) -> 1; (2, 1) -> 1; (2, 2) -> full
    assert delta("table.fill", b) == {0: 2, 1: 2, "full": 1}


def test_type_side_takes_the_side_from_the_resting_order() -> None:
    b = hits("itch.type_x_side")
    book([add(1, "S", 10), (ord("C"), Reduce(1, 0, 1, 10)), (ord("X"), Reduce(1, 0, 99, 1))])
    # the X names an unknown ref: no side, no sample
    assert delta("itch.type_x_side", b) == {("A", "S"): 1, ("C", "S"): 1}


def test_mold_paths_and_lanes() -> None:
    s, t = b"SESSIONAAA", b"SESSIONBBB"
    m36 = b"A" + bytes(35)  # an Add-sized message
    pkts = [
        pack(s, 1, [m36, m36]),  # in order
        pack(s, 5, [m36]),  # 3, 4 missing: gap
        pack(s, 5, [m36]),  # again: duplicate
        heartbeat(s, 6),
        pack(t, 1, [m36]),  # new session
        end_of_session(t, 2),
    ]
    b, lb = hits("mold.path"), hits("feed.type_x_lane")
    cov.sample_feed(pkts, 64)
    assert delta("mold.path", b) == {
        "in_order": 1, "gap": 1, "duplicate": 1, "heartbeat": 1, "session_change": 1,
        "end_of_session": 1,
    }  # fmt: skip
    # first message at 20 + 2 = 22 -> lane 6; the second at 22 + 36 + 2 = 60 -> lane 4.
    # Duplicates are driven too, so they count: 4 packets carry a message at 22.
    assert delta("feed.type_x_lane", lb) == {("A", 6): 4, ("A", 4): 1}
    lb = hits("feed.type_x_lane")
    cov.sample_feed([pack(s, 1, [m36, b"Z" + bytes(9)])], 8)  # 8 bits: one lane; Z skipped
    assert delta("feed.type_x_lane", lb) == {("A", 0): 1, ("skipped", 0): 1}


def test_fcs_cross() -> None:
    b = hits("eth.mode_x_fcs")
    cov.sample_fcs(True, True)
    cov.sample_fcs(False, False)
    assert delta("eth.mode_x_fcs", b) == {("ct", "bad_fcs"): 1, ("sf", "good"): 1}
