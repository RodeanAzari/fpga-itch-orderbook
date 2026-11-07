"""Book stimulus shared by test_order_table and test_book: raw ITCH builders, directed
limit/edge messages, the adversarial mix, the real-day fixture, and the record driver.
The DUT input is itch_decoder's record port (type, seq, ts, ref, new_ref, sell, shares,
price), so these drive order_table and book_engine alike.
"""

from __future__ import annotations

import struct
from pathlib import Path

from cocotb.triggers import RisingEdge
from tb_common import get_rng

from model import generator
from model.bounded import TICK
from model.itch import Add, Delete, Message, Reduce, Replace, decode

HALF = 2048
FIXTURE = Path(__file__).resolve().parents[2] / "data/samples/2019-10-18_AAPL_head50k.itch"
REAL_BASE = 23_455  # ADR 0002: 2019-10-18 AAPL opening mid, in ticks


def _hdr(kind: bytes, ts: int) -> bytes:
    return kind + struct.pack(">HH", 1, 0) + ts.to_bytes(6)


def a_msg(ref: int, side: bytes, shares: int, price: int, ts: int = 1) -> bytes:
    return _hdr(b"A", ts) + struct.pack(">QcI8sI", ref, side, shares, b"TEST    ", price)


def e_msg(ref: int, shares: int, ts: int = 1) -> bytes:
    return _hdr(b"E", ts) + struct.pack(">QIQ", ref, shares, 0)


def d_msg(ref: int, ts: int = 1) -> bytes:
    return _hdr(b"D", ts) + struct.pack(">Q", ref)


def u_msg(ref: int, new_ref: int, shares: int, price: int, ts: int = 1) -> bytes:
    return _hdr(b"U", ts) + struct.pack(">QQII", ref, new_ref, shares, price)


def directed(base_ticks: int) -> list[bytes]:
    """One block per limit or edge. Refs from 2^31 up so they never meet the generator's."""
    lo = (base_ticks - HALF) * TICK
    top = lo + 2 * HALF * TICK  # first price above the window
    r = iter(range(1 << 31, (1 << 31) + 10_000))
    out: list[bytes] = []
    # Every window edge, both sides: in, sub-penny, out below, out above. Then remove.
    for side in (b"B", b"S"):
        for price in (lo, lo + 1, lo + 99, lo + 100, top - 100, top - 1, top, lo - 1, lo - 100):
            ref = next(r)
            out += [a_msg(ref, side, 300, price), e_msg(ref, 100), d_msg(ref)]
    # ADR 0002: an order past the worse edge must not flag while its side has in-window
    # levels, and must flag once they are gone.
    for side, far in ((b"B", lo - 500 * TICK), (b"S", top + 500 * TICK)):
        near, past = next(r), next(r)
        out += [a_msg(near, side, 100, lo + 2000 * TICK), a_msg(past, side, 100, far)]
        out += [d_msg(near), d_msg(past)]
    # Width limits: a 33-bit ref and a 2^20-share order are counted, never truncated.
    out += [a_msg(1 << 32 | 5, b"B", 100, lo + 500), a_msg(next(r), b"B", 1 << 20, lo + 500)]
    out += [a_msg(next(r), b"S", (1 << 20) - 1, lo + 900)]  # largest that fits
    # Duplicate Add of a live ref, and over-reduce (removes the order).
    ref = next(r)
    out += [a_msg(ref, b"B", 100, lo + 700), a_msg(ref, b"B", 50, lo + 600)]
    out += [e_msg(ref, 150), e_msg(ref, 1)]  # over-reduce, then unknown
    # Replace keeps the original side, and moves between level, out of window and sub-penny.
    a, b, c, d = next(r), next(r), next(r), next(r)
    out += [a_msg(a, b"S", 200, lo + 1000), u_msg(a, b, 400, top + 500)]  # level -> oow
    out += [u_msg(b, c, 100, lo + 1050), u_msg(c, d, 100, lo + 1051)]  # oow -> level -> sub
    out += [e_msg(d, 40), u_msg(d, d, 70, lo + 1000), d_msg(d)]  # U to the same ref
    # Replace whose new ref is already live: the original goes, the insert is a duplicate.
    a, b = next(r), next(r)
    out += [a_msg(a, b"B", 100, lo + 300), a_msg(b, b"B", 100, lo + 301), u_msg(a, b, 9, lo)]
    out += [u_msg(next(r), next(r), 5, lo)]  # replace of an unknown ref
    return out


def records(raws: list[bytes]) -> list[tuple[int, Message]]:
    out = []
    for raw in raws:
        if raw[:1] in b"AFECXDU" and len(raw) > 1:
            m = decode(raw)
            if isinstance(m, Add | Reduce | Delete | Replace):
                out.append((raw[0], m))
    return out


def dut_fields(kind: int, m: Message) -> dict[str, int]:
    f = {"ref": 0, "new_ref": 0, "sell": 0, "shares": 0, "price": 0}
    if isinstance(m, Add):
        f.update(ref=m.ref, sell=int(m.side == "S"), shares=m.shares, price=m.price)
    elif isinstance(m, Reduce):
        f.update(ref=m.ref, shares=m.shares)
    elif isinstance(m, Delete):
        f.update(ref=m.ref)
    elif isinstance(m, Replace):
        f.update(ref=m.ref, new_ref=m.new_ref, shares=m.shares, price=m.price)
    return f | {"type": kind, "ts": m.ts}


def adversarial(
    seed: int, n: int, cfg: generator.Config = generator.DEFAULT
) -> list[tuple[int, Message]]:
    rng = get_rng(f"ot_gen_{seed}")
    msgs = generator.generate(seed=rng.randrange(1 << 30), count=n, cfg=cfg)
    base = generator.DEFAULT.base_price // TICK
    block = directed(base)
    k = rng.randrange(len(msgs) // 4, len(msgs) // 2)  # mid-stream, with a busy table
    return records(msgs[:k] + block + msgs[k:] + directed(base))


async def send_records(dut, recs: list[tuple[int, Message]], rng, p_valid: float) -> None:
    """Drive each record (seq = its index) through the valid/ready handshake."""
    for i, (kind, m) in enumerate(recs):
        while rng.random() >= p_valid:
            dut.s_valid_i.value = 0
            await RisingEdge(dut.clk_i)
        f = dut_fields(kind, m)
        dut.s_type_i.value = f["type"]
        dut.s_seq_i.value = i
        dut.s_ts_i.value = f["ts"]
        dut.s_ref_i.value = f["ref"]
        dut.s_new_ref_i.value = f["new_ref"]
        dut.s_sell_i.value = f["sell"]
        dut.s_shares_i.value = f["shares"]
        dut.s_price_i.value = f["price"]
        dut.s_valid_i.value = 1
        while True:
            await RisingEdge(dut.clk_i)
            if int(dut.s_ready_o.value):
                break
    dut.s_valid_i.value = 0
