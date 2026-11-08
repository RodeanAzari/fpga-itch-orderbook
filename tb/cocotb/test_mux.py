"""cocotb tests for rtl/out/out_mux: mode routing and wire-record packing.

Why these tests matter: this is where the book's and decoder's fields become the bytes
the host parses. A field at the wrong offset, a size truncated below 48 bits, a flag on
the wrong bit, or crossed/locked computed without the "both sides non-empty" rule would
make every record wrong, or wrong only at the book's edges (an empty side at price 0).
Records are compared byte for byte with model.protocol.bbo_record / trace_record. The
routing check proves that in trace mode the book gets nothing and in BBO mode the
decoder's records reach only the book, with ready passed back the right way.
"""

from __future__ import annotations

import os

import cocotb
from cocotb.triggers import Timer
from tb_common import get_rng

from model.book import Bbo
from model.itch import Add, Delete, Reduce, Replace
from model.moldudp64 import Record
from model.protocol import bbo_record, trace_record

LEVEL_W = int(os.environ.get("LEVEL_W", "36"))


async def settle() -> None:
    await Timer(1, unit="ns")


def rec_bytes(dut) -> bytes:
    return int(dut.o_rec_o.value).to_bytes(64)


def random_bbo(rng) -> Bbo:
    def side() -> tuple[int, int]:
        if rng.random() < 0.15:
            return 0, 0  # empty
        return rng.choice([1, 2_345_500, (1 << 32) - 1, rng.getrandbits(32)]), rng.choice(
            [1, (1 << LEVEL_W) - 1, rng.getrandbits(LEVEL_W) or 1]
        )

    (bp, bs), (ap, az) = side(), side()
    r = rng.random()
    if r < 0.2 and bs and az:
        ap = bp  # locked
    elif r < 0.3:
        ap = bp  # equal prices, maybe with an empty side: not locked then
    return Bbo(rng.getrandbits(48), bp, bs, ap, az, rng.random() < 0.3)


def random_record(rng) -> Record:
    loc, ts, ref = 1, rng.getrandbits(48), rng.getrandbits(64)
    k = rng.choice(b"AFECXDU")
    if k in b"AF":
        m = Add(loc, ts, ref, rng.choice("BS"), rng.getrandbits(32), "AAPL", rng.getrandbits(32))
    elif k in b"ECX":
        m = Reduce(loc, ts, ref, rng.getrandbits(32))
    elif k == ord("D"):
        m = Delete(loc, ts, ref)
    else:
        m = Replace(loc, ts, ref, rng.getrandbits(64), rng.getrandbits(32), rng.getrandbits(32))
    return Record(rng.getrandbits(64), m, k)


def drive_record(dut, rec: Record) -> None:
    """As itch_decoder presents it: unused fields 0 (its header)."""
    m = rec.msg
    dut.f_type_i.value = rec.kind
    dut.f_seq_i.value = rec.seq
    dut.f_ts_i.value = m.ts
    dut.f_ref_i.value = m.ref
    dut.f_new_ref_i.value = m.new_ref if isinstance(m, Replace) else 0
    dut.f_sell_i.value = int(isinstance(m, Add) and m.side == "S")
    dut.f_shares_i.value = 0 if isinstance(m, Delete) else m.shares
    dut.f_price_i.value = m.price if isinstance(m, Add | Replace) else 0


@cocotb.test()
async def bbo_records(dut):
    rng = get_rng("bbo_records")
    dut.mode_i.value = 0
    flags_seen = set()
    for _ in range(3000):
        bbo, seq = random_bbo(rng), rng.getrandbits(64)
        fs, bs = rng.getrandbits(1), rng.getrandbits(1)
        dut.b_bid_px_i.value, dut.b_bid_sz_i.value = bbo.bid_price, bbo.bid_size
        dut.b_ask_px_i.value, dut.b_ask_sz_i.value = bbo.ask_price, bbo.ask_size
        dut.b_oow_i.value, dut.b_seq_i.value, dut.b_ts_i.value = int(bbo.out_of_window), seq, bbo.ts
        dut.b_feed_stale_i.value, dut.b_book_stale_i.value = fs, bs
        await settle()
        want = bbo_record(bbo, seq, bool(fs or bs))
        got = rec_bytes(dut)
        assert got == want, f"{bbo} seq {seq} stale {fs}{bs}\n got  {got.hex()}\n want {want.hex()}"
        flags_seen.add(int.from_bytes(want[2:4]))
    # every flag, alone and together, was exercised
    assert {1, 2, 4, 8} <= {f & b for f in flags_seen for b in (1, 2, 4, 8)}
    assert any(f & 2 for f in flags_seen) and any(f & 4 for f in flags_seen)


@cocotb.test()
async def trace_records(dut):
    rng = get_rng("trace_records")
    dut.mode_i.value = 1
    for _ in range(3000):
        rec = random_record(rng)
        drive_record(dut, rec)
        await settle()
        want = trace_record(rec) + bytes(24)
        got = rec_bytes(dut)
        assert got == want, f"{rec}\n got  {got.hex()}\n want {want.hex()}"


@cocotb.test()
async def routing(dut):
    """Every handshake input combination, both modes: who sees valid, who gets ready."""
    for mode in (0, 1):
        dut.mode_i.value = mode
        for bits in range(8):
            fv, bmv, ordy = bits & 1, bits >> 1 & 1, bits >> 2 & 1
            for bsr in (0, 1):
                dut.f_valid_i.value, dut.b_m_valid_i.value = fv, bmv
                dut.o_ready_i.value, dut.b_s_ready_i.value = ordy, bsr
                await settle()
                got = (int(dut.b_s_valid_o.value), int(dut.f_ready_o.value),
                       int(dut.o_valid_o.value), int(dut.b_m_ready_o.value))  # fmt: skip
                want = (fv, bsr, bmv, ordy) if mode == 0 else (0, ordy, fv, 0)
                assert got == want, f"mode {mode} fv{fv} bmv{bmv} ordy{ordy} bsr{bsr}: {got}"
