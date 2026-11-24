"""make capacity-sim (book half): book_engine alone at full rate, the cycles it takes per
record. Not part of `make sim`.

Every record is offered the cycle after the previous one is taken and every BBO is taken
at once (p_valid = p_ready = 1), so the time between two input handshakes is the book's
own service time for the first of them. Each run is also the usual scoreboarded run
(test_book.run): every BBO must equal the bounded model's, so a rate is only written for
a correct run.

Inputs: the injector image's messages (analysis/injector_image.py; the same 1,043
messages the board's inject run measures through the whole chain) and, when the off-repo
extract exists (ITCH_EXTRACT), every book message of the real AAPL day.

$RATE_CSV gets rows: stage, input, type (a record type, or "all"), records, cycles.
For a type, cycles = the sum of the gaps after its records (the last record of the run
has no gap and is left out); for "all", cycles = last accept - first accept.
"""

from __future__ import annotations

import csv
import os
from collections import defaultdict
from pathlib import Path

import cocotb
from book_stim import REAL_BASE, records
from cocotb.triggers import ReadOnly, RisingEdge
from test_book import run

from analysis.injector_image import BASE_TICKS, image, messages
from model.itch import iter_raw

EXTRACT = Path(os.environ.get("ITCH_EXTRACT", "/mnt/d/itch/derived/2019-10-18_AAPL.itch"))
COLS = ("stage", "input", "type", "records", "cycles")


def write_rows(stage: str, name: str, kinds: list[int], accepts: list[int]) -> None:
    assert len(accepts) == len(kinds), f"{len(accepts)} handshakes for {len(kinds)} records"
    n: dict[str, int] = defaultdict(int)
    cyc: dict[str, int] = defaultdict(int)
    for k, a, b in zip(kinds, accepts, accepts[1:], strict=False):
        n[chr(k)] += 1
        cyc[chr(k)] += b - a
    rows = [
        {"stage": stage, "input": name, "type": t, "records": n[t], "cycles": cyc[t]}
        for t in sorted(n)
    ]
    rows.append(
        {"stage": stage, "input": name, "type": "all", "records": len(kinds),
         "cycles": accepts[-1] - accepts[0]}
    )  # fmt: skip
    append_rows(rows)


def append_rows(rows: list[dict[str, int | str]]) -> None:
    """Log the rows and append them to $RATE_CSV (header if new)."""
    for r in rows:
        cocotb.log.info("%s", r)
    out = os.environ.get("RATE_CSV")
    if out:
        path = Path(out)
        new = not path.exists()
        with path.open("a", newline="") as f:
            w = csv.DictWriter(f, COLS, lineterminator="\n")
            if new:
                w.writeheader()
            w.writerows(rows)


async def sample(dut, accepts: list[int], idle: list[int]) -> None:
    """Settled values each cycle (ReadOnly): a cycle with s_valid and s_ready is a
    handshake at the next edge; one with s_valid low after the first handshake is a
    bench bubble (counted, must be 0 for the rate to be the book's)."""
    cycle = 0
    while True:
        await RisingEdge(dut.clk_i)
        await ReadOnly()
        cycle += 1
        v, r = int(dut.s_valid_i.value), int(dut.s_ready_o.value)
        if v and r:
            accepts.append(cycle)
        elif not v and accepts:
            idle.append(cycle)


@cocotb.test()
async def injector_image(dut):
    """The injector image's book records (the board chain's 1,043 messages)."""
    assert BASE_TICKS == REAL_BASE
    recs = records(messages()[: image().messages])
    accepts: list[int] = []
    idle: list[int] = []
    cocotb.start_soon(sample(dut, accepts, idle))
    await run(dut, "rate_inj", recs, BASE_TICKS, 1.0, 1.0, wait_idle=True)
    bubbles = sum(c < accepts[-1] for c in idle)
    assert bubbles == 0, f"the bench left s_valid low on {bubbles} cycles"
    write_rows("book", "injector_image", [k for k, _ in recs], accepts)


@cocotb.test(skip=not EXTRACT.exists())
async def full_day(dut):
    """Every book record of the real AAPL day (off-repo extract)."""
    with EXTRACT.open("rb") as f:
        recs = records(list(iter_raw(f)))
    accepts: list[int] = []
    idle: list[int] = []
    cocotb.start_soon(sample(dut, accepts, idle))
    await run(dut, "rate_day", recs, REAL_BASE, 1.0, 1.0, wait_idle=True)
    bubbles = sum(c < accepts[-1] for c in idle)
    assert bubbles == 0, f"the bench left s_valid low on {bubbles} cycles"
    write_rows("book", f"full_day {EXTRACT.stem}", [k for k, _ in recs], accepts)
