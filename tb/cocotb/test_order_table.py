"""cocotb tests for rtl/book/order_table, scored against model.bounded.BoundedBook.

Why these tests matter: the order table is the only place an order's side, price and size
live after its Add. A wrong placement, a missed tag match, a reduce applied to the wrong
way, a replace that loses its side, or an out-of-window order dropped from the table all
turn into a silently wrong book later. So each record is applied to the DUT and to the
golden model in lockstep. The DUT's level deltas are applied to a shadow book, and after
every message's last beat the shadow's bid levels, ask levels and out-of-window counts
must equal the model's, exactly. Every counter and the stale flag must match at the end.
Nothing here re-derives the table's logic: the model is the reference (ADR 0001/0002/0006).

Stimulus: the seeded adversarial generator (bucket collisions aimed at 512 sets per bank,
far prices, unknown refs, locked/crossed episodes) plus directed messages for each limit
(ref and shares width, duplicates, over-reduce, sub-penny prices, every window edge), with
and without backpressure; and at the real geometry, the first 50,000 messages of
2019-10-18 AAPL.
"""

from __future__ import annotations

import os
from collections import Counter, deque

import cocotb
from book_stim import (
    FIXTURE,
    HALF,
    REAL_BASE,
    a_msg,
    adversarial,
    dut_fields,
    records,
    send_records,
    u_msg,
)
from cocotb.clock import Clock
from cocotb.triggers import ClockCycles, RisingEdge
from tb_common import get_rng

from model import generator
from model.bounded import TICK, BoundedBook
from model.itch import Message, iter_raw

SET_BITS = int(os.environ.get("SET_BITS", "13"))
K_NONE, K_LEVEL, K_OOW = 0, 1, 2
STATUS = {  # DUT pulse -> model counter (attribute path)
    "ref_overflow_o": "ref_overflow",
    "duplicate_ref_o": "counters.duplicate_ref",
    "shares_overflow_o": "shares_overflow",
    "table_overflow_o": "table_overflow",
    "unknown_ref_o": "counters.unknown_ref",
    "over_reduce_o": "counters.over_reduce",
    "price_oow_o": "price_oow",
    "price_subpenny_o": "price_subpenny",
}


def model_count(book: BoundedBook, path: str) -> int:
    obj: object = book
    for part in path.split("."):
        obj = getattr(obj, part)
    assert isinstance(obj, int)
    return obj


# ------------------------------------------------------------------ raw ITCH builders
# ------------------------------------------------------------------ the run
async def run(dut, name: str, recs: list[tuple[int, Message]], base: int, p_valid: float,
              p_ready: float) -> BoundedBook:  # fmt: skip
    rng = get_rng(name)
    cocotb.start_soon(Clock(dut.clk_i, 8, unit="ns").start())
    lo_ticks = base - HALF
    dut.cfg_lo_price_i.value = lo_ticks * TICK
    dut.s_valid_i.value = 0
    dut.m_ready_i.value = 0
    dut.rst_i.value = 1
    await ClockCycles(dut.clk_i, 3)
    dut.rst_i.value = 0

    book = BoundedBook(base_ticks=base, half_window=HALF, sets_per_bank=1 << SET_BITS, ways=4)
    pending = deque(enumerate(recs))
    history: deque[str] = deque(maxlen=8)
    bids: dict[int, int] = {}
    asks: dict[int, int] = {}
    oow: Counter[tuple[str, str]] = Counter()
    beats: list[str] = []
    counts = dict.fromkeys(STATUS, 0)
    done = {"messages": 0}

    def fail(why: str) -> None:
        ctx = "\n  ".join(history)
        raise AssertionError(f"{name}: {why}\n last messages:\n  {ctx}\n beats: {beats}")

    def apply_beat() -> None:
        kind, sell = int(dut.m_kind_o.value), int(dut.m_sell_o.value)
        dec, shares = int(dut.m_dec_o.value), int(dut.m_shares_o.value)
        side = "S" if sell else "B"
        beats.append(f"kind={kind} side={side} idx={int(dut.m_idx_o.value)} dec={dec} n={shares}")
        if kind == K_LEVEL:
            levels = asks if sell else bids
            price = (lo_ticks + int(dut.m_idx_o.value)) * TICK
            now = levels.get(price, 0) + (-shares if dec else shares)
            if now < 0:
                fail(f"level {price} went negative")
            if now:
                levels[price] = now
            else:
                levels.pop(price, None)
        elif kind == K_OOW:
            oow[side, "better" if int(dut.m_better_o.value) else "worse"] += -1 if dec else 1
        elif kind != K_NONE:
            fail(f"bad delta kind {kind}")

    def check_message() -> None:
        if not pending:
            fail("a last beat with no message outstanding")
        i, (kind, m) = pending.popleft()
        history.append(f"#{i} {chr(kind)} {m}")
        if int(dut.m_seq_o.value) != i or int(dut.m_ts_o.value) != m.ts:
            fail(f"seq/ts {int(dut.m_seq_o.value)}/{int(dut.m_ts_o.value)} != {i}/{m.ts}")
        book.apply(m)
        want_oow = {k: v for k, v in book._oow.items() if v}
        got_oow = {k: v for k, v in oow.items() if v}
        if bids != book.bids or asks != book.asks or got_oow != want_oow:
            fail(
                f"book differs after #{i}\n DUT   bids {bids}\n model bids {book.bids}\n"
                f" DUT   asks {asks}\n model asks {book.asks}\n DUT oow {got_oow} model {want_oow}"
            )
        beats.clear()
        done["messages"] += 1

    async def monitor() -> None:
        while True:
            await RisingEdge(dut.clk_i)
            for s in STATUS:
                counts[s] += int(getattr(dut, s).value)
            if int(dut.m_valid_o.value) and int(dut.m_ready_i.value):
                apply_beat()
                if int(dut.m_last_o.value):
                    check_message()
            dut.m_ready_i.value = int(rng.random() < p_ready)

    cocotb.start_soon(monitor())
    await send_records(dut, recs, rng, p_valid)

    # Drained = every message's last beat seen; bounded so a lost beat fails, not hangs.
    for _ in range(1000):
        if done["messages"] == len(recs):
            break
        await ClockCycles(dut.clk_i, 10)
    await ClockCycles(dut.clk_i, 5)
    if done["messages"] != len(recs):
        fail(f"only {done['messages']} of {len(recs)} messages completed")

    want = {s: model_count(book, p) for s, p in STATUS.items()}
    assert counts == want, f"{name}: counters\n DUT   {counts}\n model {want}"
    assert int(dut.stale_o.value) == int(book.stale), "stale flag"
    cocotb.log.info("%s: %d messages, counters %s, stale %d", name, len(recs), want, book.stale)
    return book


@cocotb.test()
async def adversarial_full_rate(dut):
    """Generator + directed limits, no stalls: every counter path must be reached."""
    base = generator.DEFAULT.base_price // TICK
    book = await run(dut, "ot_adv", adversarial(1, 20_000), base, 1.0, 1.0)
    for path in STATUS.values():
        if path == "table_overflow" and SET_BITS != 9:
            continue  # the generator aims its collisions at 512 sets per bank
        assert model_count(book, path) > 0, f"stimulus never produced {path}"


@cocotb.test()
async def adversarial_backpressure(dut):
    """Same mix with input gaps and output stalls: the handshake must not lose a beat."""
    base = generator.DEFAULT.base_price // TICK
    await run(dut, "ot_adv_bp", adversarial(2, 8_000), base, 0.6, 0.4)


@cocotb.test(skip=SET_BITS != 13)
async def real_day_head(dut):
    """First 50,000 messages of 2019-10-18 AAPL at the real geometry (ADR 0001/0002)."""
    with FIXTURE.open("rb") as f:
        recs = records(list(iter_raw(f)))
    book = await run(dut, "ot_real", recs, REAL_BASE, 1.0, 0.9)
    assert not book.stale and book.table_overflow == 0


@cocotb.test()
async def out_of_window_alone_is_not_stale(dut):
    """ADR 0006: out-of-window orders are flagged by ADR 0002, so they must not mark stale.

    The adversarial streams are always stale for other reasons, so they cannot see a stale
    flag raised by price_oow; this clean stream has far prices and nothing else.
    """
    cfg = generator.Config(p_collide=0.0, p_unknown_ref=0.0, p_far_price=0.1)
    msgs = generator.generate(seed=get_rng("ot_oow_gen").randrange(1 << 30), count=4000, cfg=cfg)
    book = await run(dut, "ot_oow", records(msgs), cfg.base_price // TICK, 1.0, 1.0)
    assert book.price_oow > 0 and not book.stale


@cocotb.test()
async def latency_matches_header(dut):
    """The header's cycle counts feed docs/latency.md, so they are measured, not trusted.
    Cycle 0 is the record's handshake cycle: the first beat is presented in cycle LATENCY,
    a U's second beat LATENCY cycles after its first is taken, and s_ready_o is high again
    the cycle after the last beat is taken (all with m_ready_i held high)."""
    latency = 7
    cocotb.start_soon(Clock(dut.clk_i, 8, unit="ns").start())
    base = 10_000
    dut.cfg_lo_price_i.value = (base - HALF) * TICK
    dut.s_valid_i.value = 0
    dut.m_ready_i.value = 1
    dut.rst_i.value = 1
    await ClockCycles(dut.clk_i, 3)
    dut.rst_i.value = 0
    while not int(dut.s_ready_o.value):  # table clear after reset
        await RisingEdge(dut.clk_i)

    async def send(raw: bytes) -> list[int]:
        """Cycles from the accepting edge to each beat's taking edge, and to ready."""
        ((kind, m),) = records([raw])
        for k, v in dut_fields(kind, m).items():
            getattr(dut, f"s_{k}_i").value = v
        dut.s_seq_i.value = 0
        dut.s_valid_i.value = 1
        await RisingEdge(dut.clk_i)
        while not int(dut.s_ready_o.value):
            await RisingEdge(dut.clk_i)
        dut.s_valid_i.value = 0
        marks, n = [], 0
        while True:
            await RisingEdge(dut.clk_i)
            n += 1
            if int(dut.m_valid_o.value) and int(dut.m_ready_i.value):
                marks.append(n)
                if int(dut.m_last_o.value):
                    break
        await RisingEdge(dut.clk_i)
        marks.append(n + 1 if int(dut.s_ready_o.value) else -1)
        return marks

    price = base * TICK
    assert await send(a_msg(7, b"B", 100, price)) == [latency, latency + 1]
    assert await send(u_msg(7, 8, 50, price)) == [latency, 2 * latency, 2 * latency + 1]
