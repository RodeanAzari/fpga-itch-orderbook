"""cocotb tests for rtl/meas/t0_track against model.timestamps.T0Track (ADR 0011).

Why these tests matter: every BBO's T0 comes from this match. If a status popped the
wrong number of entries, or a loss went unrecorded, every later frame would carry the T0
of a different frame, which looks plausible and is wrong by microseconds. Reference: the
model, built from eth_rx's contract (one status per frame with an SFD, in wire order) and
the RTL's same-cycle rules. Checked on every cycle: each frame start's (t0, valid), and the
lost_o pulse. Both modes (CUT_THROUGH = 0, 1; ADR 0013); in cut-through the model is
itself checked against a frame-level reference (model/tests/test_timestamps.py), and
`ct_frames` drives that reference's contract-shaped traffic. Stimulus: realistic order
(an SFD, later its status, later its start) with random gaps, plus pressure: missed SFDs
(as in reset), multi-frame overflows larger than the queue, statuses and SFDs on the same
cycle, and small queues that fill.
"""

from __future__ import annotations

import os
from collections import deque

import cocotb
from cocotb.clock import Clock
from cocotb.triggers import ClockCycles, FallingEdge, ReadOnly, RisingEdge
from tb_common import get_rng

from model.tests.test_timestamps import Cycle, schedule
from model.timestamps import T0Track

PEND_AW = int(os.environ.get("PEND_AW", "6"))
DONE_AW = int(os.environ.get("DONE_AW", "7"))
CUT = int(os.environ.get("CUT_THROUGH", "0"))


async def reset(dut) -> None:
    cocotb.start_soon(Clock(dut.clk_i, 8, unit="ns").start())
    for s in (
        "sfd_i",
        "ok_i",
        "drop_i",
        "ovf_i",
        "ovf_frames_i",
        "frame_start_i",
        "now_i",
        "emitted_i",
    ):
        getattr(dut, s).value = 0
    dut.rst_i.value = 1
    await ClockCycles(dut.clk_i, 3)
    await FallingEdge(dut.clk_i)
    dut.rst_i.value = 0


async def run(dut, name: str, cycles: int, p: dict[str, float]) -> dict[str, int]:
    """Drive random but contract-shaped stimulus for `cycles` cycles, checking each one."""
    rng = get_rng(name)
    await reset(dut)
    model = T0Track(pend_n=1 << PEND_AW, done_n=1 << DONE_AW, cut_through=bool(CUT))
    expect: deque[tuple[int, bool]] = deque()
    stats = {"starts": 0, "valid": 0, "lost": 0, "ovf": 0}
    now = 1000
    for _ in range(cycles):
        await FallingEdge(dut.clk_i)
        now += 1
        unjudged, delivered = len(model.pend), len(model.done)  # shapes the stimulus only
        sfd = rng.random() < p["sfd"]
        status = unjudged > 0 and rng.random() < p["status"]
        ok = drop = False
        ovf = 0
        if status:
            r = rng.random()
            if r < p["ovf"]:
                ovf = rng.randint(1, min(255, unjudged + 3))  # may exceed what is queued
            elif r < p["ovf"] + p["drop"]:
                drop = True
            else:
                ok = True
        if not status and rng.random() < p["phantom"]:
            ok = True  # a status whose SFD was missed (reset window)
        if CUT:  # a start needs an SFD queued (or parked in hold), like a real frame
            delivered = unjudged + (model.hold is not None)
        start = (delivered > 0 or rng.random() < p["phantom"]) and rng.random() < p["start"]
        emitted = (ok or drop or ovf > 0) and rng.random() < p.get("emitted", 0.5)
        dut.now_i.value = now
        dut.sfd_i.value = int(sfd)
        dut.ok_i.value = int(ok)
        dut.drop_i.value = int(drop)
        dut.ovf_i.value = int(ovf > 0)
        dut.ovf_frames_i.value = ovf
        dut.frame_start_i.value = int(start)
        dut.emitted_i.value = int(emitted)
        lost_before = model.lost
        out = model.step(
            now, sfd=sfd, ok=ok, drop=drop, ovf_frames=ovf, frame_start=start, emitted=emitted
        )
        if out is not None:
            expect.append(out)
        stats["ovf"] += ovf > 0
        await RisingEdge(dut.clk_i)
        await ReadOnly()
        if out is not None:
            got = (int(dut.t0_o.value), bool(int(dut.t0_valid_o.value)))
            assert got == expect.popleft(), f"cycle t={now}: t0 {got} != model {out}"
            stats["starts"] += 1
            stats["valid"] += got[1]
        assert int(dut.lost_o.value) == (model.lost - lost_before), f"lost_o at t={now}"
        stats["lost"] = model.lost
    cocotb.log.info("%s: %s", name, stats)
    return stats


@cocotb.test(skip=bool(CUT))
async def realistic(dut):
    """Mostly good frames, a few drops and overflows: most T0s valid, some lost."""
    st = await run(
        dut, "t0_real", 30_000,
        {"sfd": 0.02, "status": 0.05, "ovf": 0.02, "drop": 0.1, "phantom": 0.0, "start": 0.05},
    )  # fmt: skip
    assert st["starts"] > 300 and st["valid"] > 0.8 * st["starts"], st
    assert st["ovf"] > 0, st


@cocotb.test()
async def pressure(dut):
    """Missed SFDs, overflows past the queue, same-cycle events, queues near full."""
    st = await run(
        dut, "t0_press", 30_000,
        {"sfd": 0.3, "status": 0.2, "ovf": 0.1, "drop": 0.2, "phantom": 0.02, "start": 0.15},
    )  # fmt: skip
    assert st["lost"] > 0 and st["valid"] > 0, st


@cocotb.test(skip=not CUT)
async def ct_frames(dut):
    """Cut-through, eth_rx-shaped traffic (frames of every kind, overflow groups, 5-byte
    frames judged before they start): every start gets its own SFD's T0, nothing lost."""
    sch = schedule(get_rng("t0_ct_frames"), 2_000, cap=1 << PEND_AW)
    await reset(dut)
    model = T0Track(pend_n=1 << PEND_AW, done_n=1 << DONE_AW, cut_through=True)
    starts = 0
    for now in range(sch.end + 1):
        c = sch.cycles.get(now, Cycle())
        await FallingEdge(dut.clk_i)
        for sig, v in (
            ("now_i", now),
            ("sfd_i", c.sfd),
            ("ok_i", c.ok),
            ("drop_i", c.drop),
            ("ovf_i", c.ovf_frames > 0),
            ("ovf_frames_i", c.ovf_frames),
            ("emitted_i", c.emitted),
            ("frame_start_i", c.start),
        ):
            getattr(dut, sig).value = int(v)
        out = model.step(
            now,
            sfd=c.sfd,
            ok=c.ok,
            drop=c.drop,
            ovf_frames=c.ovf_frames,
            emitted=c.emitted,
            frame_start=c.start,
        )
        await RisingEdge(dut.clk_i)
        await ReadOnly()
        assert int(dut.lost_o.value) == 0, f"lost_o at t={now}"
        if c.start:
            starts += 1
            got = (int(dut.t0_o.value), bool(int(dut.t0_valid_o.value)))
            assert got == c.want == out, f"t={now}: rtl {got} reference {c.want} model {out}"
    assert starts > 500 and model.lost == 0


@cocotb.test(skip=not CUT)
async def ct_random(dut):
    """Cut-through, unconstrained (beyond one status per cycle): the RTL equals the model
    cycle for cycle, including lost_o, hold and same-cycle cases outside the contract."""
    st = await run(
        dut, "t0_ct_rand", 30_000,
        {"sfd": 0.2, "status": 0.15, "ovf": 0.1, "drop": 0.3, "phantom": 0.02, "start": 0.2,
         "emitted": 0.6},
    )  # fmt: skip
    assert st["lost"] > 0 and st["valid"] > 0 and st["ovf"] > 0, st
