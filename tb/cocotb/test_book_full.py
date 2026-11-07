"""make sim-full: book_engine at the real geometry on 1M+ events (Phase D DoD) and, when
the extract is available, the whole 2019-10-18 AAPL day. Same scoreboard as test_book
(every BBO update against model.bounded.BoundedBook, field by field, no extras).

Not part of `make sim` (minutes, not seconds). Each passing test appends one row to
$SIM_FULL_CSV: test, seed, messages, BBO updates matched, every counter, wall seconds. A
failing test writes nothing and fails the run. Seeds come from $SEED (set by the
Makefile), so a run reproduces exactly.
"""

from __future__ import annotations

import csv
import os
import time
from pathlib import Path

import cocotb
from book_stim import REAL_BASE, adversarial, records
from test_book import STATUS, model_count, run, wide

from model import generator
from model.bounded import TICK, BoundedBook
from model.itch import iter_raw

SET_BITS = int(os.environ.get("SET_BITS", "13"))
EXTRACT = Path(os.environ.get("ITCH_EXTRACT", "/mnt/d/itch/derived/2019-10-18_AAPL.itch"))


def record(test: str, n: int, book: BoundedBook, stats: dict[str, int], t0: float) -> None:
    out = os.environ.get("SIM_FULL_CSV")
    if not out:
        return
    row = {
        "test": test,
        "seed": os.environ.get("SEED", ""),
        "set_bits": SET_BITS,
        "messages": n,
        "bbo_updates_matched": stats["bbo_updates"],
        "mismatches": 0,  # any mismatch fails the test before this row is written
        **{p.split(".")[-1]: model_count(book, p) for p in STATUS.values()},
        "book_stale": int(book.stale),
        "wall_s": round(time.monotonic() - t0, 1),
    }
    path = Path(out)
    new = not path.exists()
    with path.open("a", newline="") as f:
        w = csv.DictWriter(f, fieldnames=list(row))
        if new:
            w.writeheader()
        w.writerow(row)


@cocotb.test(skip=SET_BITS != 13)
async def random_1m(dut):
    """1M+ adversarial book records plus the directed limit blocks.

    Live orders are capped near the real day's peak (27,814), so the table runs below
    capacity and this exercises the normal paths, not mostly overflow.
    """
    t0 = time.monotonic()
    recs = adversarial(10, 1_040_000, generator.Config(max_live=30_000))
    assert len(recs) >= 1_000_000, len(recs)
    stats: dict[str, int] = {}
    base = generator.DEFAULT.base_price // TICK
    book = await run(dut, "full_random", recs, base, 1.0, 0.9, stats)
    record("random_1m", len(recs), book, stats, t0)


@cocotb.test(skip=SET_BITS != 13)
async def wide_200k(dut):
    """200,000 whole-window messages: bitmap search at scale."""
    t0 = time.monotonic()
    recs, search = wide(11, 200_000, 10_000)
    cocotb.log.info("wide search stats: %s", search)
    stats: dict[str, int] = {}
    book = await run(dut, "full_wide", recs, 10_000, 1.0, 0.9, stats)
    record("wide_200k", len(recs), book, stats, t0)


@cocotb.test(skip=SET_BITS != 13 or not EXTRACT.exists())
async def real_full_day(dut):
    """Every book message of 2019-10-18 AAPL (needs the off-repo extract)."""
    t0 = time.monotonic()
    with EXTRACT.open("rb") as f:
        recs = records(list(iter_raw(f)))
    stats: dict[str, int] = {}
    book = await run(dut, "full_day", recs, REAL_BASE, 1.0, 1.0, stats)
    assert not book.stale and book.bbo_oow == 4  # ADR 0002's count for this day
    record("real_full_day", len(recs), book, stats, t0)
