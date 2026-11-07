"""cocotb tests for rtl/book/book_engine (order_table -> book_levels), scored against
model.bounded.BoundedBook's BBO stream.

Why these tests matter: this is the book the project publishes. Every message goes to the
DUT and to the golden model; the DUT must publish exactly the model's BBO updates, in
order, field by field (bid price/size, ask price/size, out-of-window flag, and the seq and
ts of the message that caused it), no more and no fewer. A best level that empties
without a correct next-best search, a bitmap bit left set, a size taken from the wrong
level, a Replace that publishes its transient state, or an unflagged out-of-window BBO
all show up as a mismatch or a missing/extra update. Counters, bbo_oow and stale must
match at the end.

Stimulus: the adversarial generator + directed limits (as test_order_table), a "wide"
stream that spreads orders over the whole window and keeps removing the best level (so
the two-level bitmap search runs across groups and at long distances), backpressure on
both ports, and the first 50,000 messages of 2019-10-18 AAPL at the real geometry.
"""

from __future__ import annotations

import os

import cocotb
from book_stim import (
    FIXTURE,
    HALF,
    REAL_BASE,
    a_msg,
    adversarial,
    d_msg,
    dut_fields,
    e_msg,
    records,
    send_records,
    u_msg,
    user_bit,
)
from cocotb.clock import Clock
from cocotb.triggers import ClockCycles, RisingEdge
from tb_common import InOrderScoreboard, get_rng

from model import generator
from model.bounded import TICK, BoundedBook
from model.itch import Message, iter_raw

SET_BITS = int(os.environ.get("SET_BITS", "13"))
WIN = 2 * HALF
STATUS = {  # DUT pulse -> model counter (attribute path)
    "ref_overflow_o": "ref_overflow",
    "duplicate_ref_o": "counters.duplicate_ref",
    "shares_overflow_o": "shares_overflow",
    "table_overflow_o": "table_overflow",
    "unknown_ref_o": "counters.unknown_ref",
    "over_reduce_o": "counters.over_reduce",
    "price_oow_o": "price_oow",
    "price_subpenny_o": "price_subpenny",
    "bbo_oow_o": "bbo_oow",
}


def model_count(book: BoundedBook, path: str) -> int:
    obj: object = book
    for part in path.split("."):
        obj = getattr(obj, part)
    assert isinstance(obj, int)
    return obj


async def reset(dut, base: int) -> None:
    cocotb.start_soon(Clock(dut.clk_i, 8, unit="ns").start())
    dut.cfg_lo_price_i.value = (base - HALF) * TICK
    dut.s_valid_i.value = 0
    dut.m_ready_i.value = 0
    dut.rst_i.value = 1
    await ClockCycles(dut.clk_i, 3)
    dut.rst_i.value = 0


async def run(dut, name: str, recs: list[tuple[int, Message]], base: int, p_valid: float,
              p_ready: float, stats: dict[str, int] | None = None) -> BoundedBook:  # fmt: skip
    rng = get_rng(name)
    await reset(dut, base)
    book = BoundedBook(base_ticks=base, half_window=HALF, sets_per_bank=1 << SET_BITS, ways=4)
    sb = InOrderScoreboard(
        name,
        fmt=lambda x: f"#{x[0]} ts={x[1]} bid {x[2]}x{x[3]} ask {x[4]}x{x[5]} oow={x[6]} u={x[7]}",
    )
    for i, (_, m) in enumerate(recs):
        bbo = book.apply(m)
        if bbo is not None:
            sb.expect(
                (i, bbo.ts, bbo.bid_price, bbo.bid_size, bbo.ask_price, bbo.ask_size,
                 int(bbo.out_of_window), user_bit(i))
            )  # fmt: skip
    counts = dict.fromkeys(STATUS, 0)

    async def monitor() -> None:
        while True:
            await RisingEdge(dut.clk_i)
            for s in STATUS:
                counts[s] += int(getattr(dut, s).value)
            if int(dut.m_valid_o.value) and int(dut.m_ready_i.value):
                sb.observe(
                    (
                        int(dut.m_seq_o.value),
                        int(dut.m_ts_o.value),
                        int(dut.m_bid_px_o.value),
                        int(dut.m_bid_sz_o.value),
                        int(dut.m_ask_px_o.value),
                        int(dut.m_ask_sz_o.value),
                        int(dut.m_oow_o.value),
                        int(dut.m_user_o.value),
                    )
                )
            dut.m_ready_i.value = int(rng.random() < p_ready)

    cocotb.start_soon(monitor())
    await send_records(dut, recs, rng, p_valid)
    # Drained = every expected update seen; bounded so a missing one fails, not hangs.
    for _ in range(2000):
        if not sb.pending:
            break
        await ClockCycles(dut.clk_i, 10)
    await ClockCycles(dut.clk_i, 100)  # an extra (unexpected) update would show up here
    sb.check_done()

    want = {s: model_count(book, p) for s, p in STATUS.items()}
    assert counts == want, f"{name}: counters\n DUT   {counts}\n model {want}"
    assert int(dut.stale_o.value) == int(book.stale), "stale flag"
    cocotb.log.info(
        "%s: %d messages, %d BBO updates, counters %s", name, len(recs), sb.matched, want
    )
    if stats is not None:
        stats["bbo_updates"] = sb.matched
    return book


def wide(seed: int, n: int, base: int) -> tuple[list[tuple[int, Message]], dict[str, int]]:
    """Orders over the whole window; the best level is removed often, so the next best is
    found by the bitmap search at every distance. Returns the records and search stats
    (from the bounded model: searches, longest distance in ticks, searches over 64)."""
    rng = get_rng(f"book_wide_{seed}")
    lo = (base - HALF) * TICK
    live: dict[int, tuple[str, int]] = {}  # ref -> side, idx
    raws: list[bytes] = []
    ref = 0
    for _ in range(n):
        r = rng.random()
        if r < 0.5 or not live:
            ref += 1
            side = rng.choice("BS")
            if rng.random() < 0.4:
                idx = rng.randrange(WIN)
            else:
                idx = min(WIN - 1, max(0, HALF + rng.randrange(-300, 300)))
            if rng.random() < 0.02:
                idx = rng.choice((0, WIN - 1))  # the window edges
            raws.append(a_msg(ref, side.encode(), rng.randrange(1, 20) * 100, lo + idx * TICK))
            live[ref] = (side, idx)
        else:
            side = rng.choice("BS")
            same = [k for k, (s, _) in live.items() if s == side] or list(live)
            if r < 0.85:  # take out the best level's orders: forces a search
                pick = max if side == "B" else min
                best = pick(live[k][1] for k in same)
                victims = [k for k in same if live[k][1] == best]
            else:
                victims = [rng.choice(same)]
            for k in victims:
                how = rng.random()
                if how < 0.6:
                    raws.append(d_msg(k))
                elif how < 0.8:
                    raws.append(e_msg(k, 10_000_000))  # over-reduce: removes it
                else:
                    ref += 1
                    idx = rng.randrange(WIN)
                    raws.append(u_msg(k, ref, 100, lo + idx * TICK))
                    live[ref] = (live[k][0], idx)
                del live[k]
    recs = records(raws)
    # Search statistics from the model, to prove the stimulus did what it is for.
    probe = BoundedBook(base_ticks=base, half_window=HALF, sets_per_bank=1 << 13, ways=4)
    stats = {"searches": 0, "max_ticks": 0, "over_64": 0}
    for _, m in recs:
        before = probe._state()[:4]
        probe.apply(m)
        after = probe._state()[:4]
        for b, a, levels in ((before[0], after[0], probe.bids), (before[2], after[2], probe.asks)):
            if b and b not in levels and levels:
                ticks = abs(a - b) // TICK
                stats["searches"] += 1
                stats["max_ticks"] = max(stats["max_ticks"], ticks)
                stats["over_64"] += ticks > 64
    return recs, stats


@cocotb.test()
async def adversarial_full_rate(dut):
    """Generator + directed limits at full rate: every counter path, flagged BBOs."""
    base = generator.DEFAULT.base_price // TICK
    book = await run(dut, "book_adv", adversarial(1, 20_000), base, 1.0, 1.0)
    assert book.bbo_oow > 0, "stimulus never flagged a BBO"


@cocotb.test()
async def adversarial_backpressure(dut):
    """Same mix with input gaps and BBO-output stalls."""
    base = generator.DEFAULT.base_price // TICK
    await run(dut, "book_adv_bp", adversarial(2, 8_000), base, 0.6, 0.3)


@cocotb.test()
async def wide_window_bitmap_search(dut):
    """ADR 0006's search: best levels emptied over the whole window, edges included."""
    base = 10_000
    recs, stats = wide(3, 12_000, base)
    cocotb.log.info("wide stimulus: %s", stats)
    assert stats["searches"] > 1000 and stats["over_64"] > 100 and stats["max_ticks"] > 1000
    await run(dut, "book_wide", recs, base, 1.0, 0.8)


@cocotb.test(skip=SET_BITS != 13)
async def real_day_head(dut):
    """First 50,000 messages of 2019-10-18 AAPL at the real geometry."""
    with FIXTURE.open("rb") as f:
        recs = records(list(iter_raw(f)))
    book = await run(dut, "book_real", recs, REAL_BASE, 1.0, 1.0)
    assert not book.stale


@cocotb.test()
async def latency_matches_header(dut):
    """T1 -> T2 inside the book, measured, against the cycle counts derived from the two
    headers (docs/latency.md will sum these). Cycle 0 is the record's handshake; the
    result is the cycle the BBO update is presented. order_table's beat is taken in cycle
    7; book_levels then needs 8 cycles for a level update (read, output register, modify,
    bitmap, done, form, compare, present) and 6 more when the best empties (L1 encode, L2
    read, L2 encode, size read, output register, size set)."""
    base = 10_000
    await reset(dut, base)
    dut.m_ready_i.value = 1
    # Both blocks clear their RAMs after reset; book_levels' (2^WIN_BITS cycles) can outlast
    # order_table's, and s_ready_o only shows the table. Records sent earlier just wait.
    await ClockCycles(dut.clk_i, WIN + 16)
    price = base * TICK

    async def send(raw: bytes) -> int | None:
        """Cycles to the BBO update, or None if the message published none."""
        ((kind, m),) = records([raw])
        for k, v in dut_fields(kind, m).items():
            getattr(dut, f"s_{k}_i").value = v
        dut.s_seq_i.value = 0
        dut.s_valid_i.value = 1
        await RisingEdge(dut.clk_i)
        while not int(dut.s_ready_o.value):
            await RisingEdge(dut.clk_i)
        dut.s_valid_i.value = 0
        found = None
        for n in range(1, 60):  # long enough for both blocks to go idle again
            await RisingEdge(dut.clk_i)
            if found is None and int(dut.m_valid_o.value):
                found = n
        return found

    assert await send(a_msg(1, b"B", 100, price)) == 15  # new best bid
    assert await send(a_msg(2, b"B", 100, price - 500 * TICK)) is None  # worse level
    assert await send(d_msg(1)) == 21  # best emptied: bitmap search to the worse level
