"""Tests for model.timestamps.T0Track in cut-through mode (ADR 0013).

Why these matter: in cut-through every BBO's T0 comes from matching a frame's start at
eth_rx's output to an SFD queued long before. One status that pops the wrong number of
entries shifts every later T0 onto another frame: plausible and wrong by microseconds.
Reference: a frame-level schedule built from eth_rx's contract, independent of the
model's queue logic. Each frame has its own SFD cycle; every frame that starts must get
exactly that cycle back. The schedule follows eth_rx's ordering rules (one status per
frame or overflow group, in wire order; a frame's first beat leaves after every earlier
frame's status; a 5-byte frame may start after its own status) and nothing more.
"""

from __future__ import annotations

import random
from collections import defaultdict
from dataclasses import dataclass, field

import pytest

from model.timestamps import T0Track


@dataclass
class Cycle:
    sfd: bool = False
    ok: bool = False
    drop: bool = False
    ovf_frames: int = 0
    emitted: bool = False
    start: bool = False
    want: tuple[int, bool] | None = None  # expected (t0, valid) at a start


@dataclass
class Schedule:
    cycles: dict[int, Cycle] = field(default_factory=lambda: defaultdict(Cycle))
    end: int = 0


def schedule(rng: random.Random, frames: int) -> Schedule:
    """Random contract-shaped traffic. SFDs run ahead of the core-side events (the async
    FIFO holds several frames); start/status of frame k never precede frame k-1's."""
    sch = Schedule()
    sfd_t = 10
    core_t = 10  # last cycle with a core-side event
    k = 0
    while k < frames:
        group = rng.choice((1, 1, 1, 1, 2, 3, 6)) if rng.random() < 0.3 else 0
        n = max(group, 1)
        sfds = []
        for _ in range(n):
            sfd_t += rng.randint(1, 12)
            sch.cycles[sfd_t].sfd = True
            sfds.append(sfd_t)
        kind = rng.choice(("ok", "bad", "tiny", "late")) if not group else "ovf"
        emitted = kind in ("ok", "bad", "late") or (group > 0 and rng.random() < 0.5)
        late = kind == "late" or (group > 0 and emitted and rng.random() < 0.5)
        first = max(core_t, sfds[-1]) + 1  # the status follows the group's last SFD
        start: int | None
        if emitted and not late:
            start = max(core_t, sfds[0]) + rng.randint(1, 4)
            status = max(start, first) + rng.choice((0, 0, 1, 5))  # same cycle allowed
        else:
            status = first + rng.randint(0, 4)
            start = status + rng.randint(0, 3) if emitted else None
        c = sch.cycles[status]
        c.emitted = emitted
        if group:
            c.ovf_frames = group
        elif kind == "ok":
            c.ok = True
        else:
            c.drop = True
        if start is not None:
            sch.cycles[start].start = True
            sch.cycles[start].want = (sfds[0], True)
        core_t = max(status, start or 0)
        k += n
    sch.end = max(sfd_t, core_t) + 1
    return sch


def run(t: T0Track, sch: Schedule) -> int:
    """Step every cycle; assert each start's T0. Returns the number of starts."""
    starts = 0
    for now in range(sch.end + 1):
        c = sch.cycles.get(now, Cycle())
        got = t.step(
            now,
            sfd=c.sfd,
            ok=c.ok,
            drop=c.drop,
            ovf_frames=c.ovf_frames,
            emitted=c.emitted,
            frame_start=c.start,
        )
        if c.start:
            starts += 1
            assert got == c.want, f"cycle {now}"
        else:
            assert got is None
    return starts


@pytest.mark.parametrize("seed", range(40))
def test_every_start_gets_its_own_sfd(seed: int) -> None:
    t = T0Track(cut_through=True)
    starts = run(t, schedule(random.Random(seed), 400))
    assert starts > 100
    assert t.lost == 0
    assert not t.pend and t.hold is None


def test_late_start_after_overflow_status_keeps_its_t0() -> None:
    """A 4-byte frame cut by an overflow emits one beat; its status (covering it and two
    whole frames lost after it) comes before that beat leaves. Its T0 must survive."""
    t = T0Track(cut_through=True)
    for now in (1, 2, 3, 4):
        t.step(now, sfd=True)
    t.step(10, ovf_frames=3, emitted=True)
    assert t.step(12, frame_start=True) == (1, True)
    assert t.step(20, frame_start=True) == (4, True)
    assert t.lost == 0


def test_start_and_overflow_status_same_cycle() -> None:
    t = T0Track(cut_through=True)
    for now in (1, 2, 3, 4):
        t.step(now, sfd=True)
    assert t.step(10, ovf_frames=3, emitted=True, frame_start=True) == (1, True)
    assert t.step(20, frame_start=True) == (4, True)
    assert t.lost == 0


def test_tiny_frame_status_discards_its_entry() -> None:
    """A frame of <= 4 bytes never starts: its status pops its SFD."""
    t = T0Track(cut_through=True)
    t.step(1, sfd=True)
    t.step(2, sfd=True)
    t.step(5, drop=True, emitted=False)
    assert t.step(6, frame_start=True) == (2, True)


def test_start_with_pend_empty_is_lost_and_next_frame_aligned() -> None:
    """An SFD missed (e.g. during reset): the start gets no T0 and it is counted."""
    t = T0Track(cut_through=True)
    assert t.step(5, frame_start=True) == (0, False)
    t.step(6, ok=True, emitted=True)
    assert t.lost == 1
    t.step(7, sfd=True)
    assert t.step(9, frame_start=True) == (7, True)
    t.step(12, ok=True, emitted=True)
    assert t.lost == 1


def test_late_status_with_pend_empty_is_lost() -> None:
    t = T0Track(cut_through=True)
    t.step(3, ok=True, emitted=True)  # 5-byte frame judged first; its SFD was missed
    assert t.lost == 1
    assert t.step(4, frame_start=True) == (0, False)
    assert t.lost == 1  # counted once, at the status


def test_overflow_larger_than_pend_is_counted() -> None:
    t = T0Track(cut_through=True)
    t.step(1, sfd=True)
    t.step(4, ovf_frames=5)
    assert t.lost == 1 and not t.pend


def test_pend_full_drops_newest_sfd() -> None:
    t = T0Track(pend_n=2, cut_through=True)
    for now in (1, 2, 3):
        t.step(now, sfd=True)
    assert t.lost == 1
    assert list(t.pend) == [1, 2]


def test_store_and_forward_ignores_emitted() -> None:
    """The cut-through input changes nothing in the default mode (ADR 0011 unchanged)."""
    rng = random.Random(7)
    a, b = T0Track(), T0Track()
    for now in range(2000):
        sfd = rng.random() < 0.2
        r = rng.random()
        ok, drop = r < 0.1, 0.1 <= r < 0.13
        ovf = rng.randint(1, 4) if 0.13 <= r < 0.15 else 0
        start = rng.random() < 0.1
        ga = a.step(now, sfd=sfd, ok=ok, drop=drop, ovf_frames=ovf, frame_start=start)
        gb = b.step(
            now,
            sfd=sfd,
            ok=ok,
            drop=drop,
            ovf_frames=ovf,
            frame_start=start,
            emitted=rng.random() < 0.5,
        )
        assert ga == gb
    assert a.lost == b.lost and a.pend == b.pend and a.done == b.done


def test_same_cycle_overflow_short_is_counted() -> None:
    """The start takes the head; the status's other frames find only what is left."""
    t = T0Track(cut_through=True)
    t.step(1, sfd=True)
    t.step(2, sfd=True)
    assert t.step(10, ovf_frames=3, emitted=True, frame_start=True) == (1, True)
    assert t.lost == 1 and not t.pend
