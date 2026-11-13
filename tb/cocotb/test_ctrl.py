"""cocotb tests for rtl/out/out_ctrl: the control-port parser and the config sequence.

Why these tests matter: a config resets the whole pipeline and picks where the output
goes. Accepting a malformed packet (wrong magic, a length off by one, mode 3) would
reconfigure the board from garbage; rejecting a good one leaves the host waiting for an
ack that never comes. Releasing the feed before out_builder has started the ack would put
old-run records after the ack (pkt_seq 0 no longer marks the start of the run); acking
before the book's RAMs are clear would let the first messages land on a half-cleared book.
A config field sliced at the wrong offset or a destination taken from the wrong packet
would be silent until hardware.

Oracle: model.protocol.parse_config decides accept/reject for every payload (a truncated
datagram, udp_rx tuser, is always rejected); accepted configs' fields must appear on
cfg_*_o and the record, as received, on ack_rec_o. The sequence is checked every cycle
against a reference of docs/protocol.md's config rules: a pending config is loaded (stats
clear) exactly when the block is idle (OFF or RUN), and it is the latest accepted one;
ack_req only once the book is not busy; the feed stays in reset from the load until
out_builder's flush; ack_rec stable in between.
"""

from __future__ import annotations

import struct

import cocotb
from cocotb.clock import Clock
from cocotb.triggers import ClockCycles, FallingEdge, ReadOnly
from tb_common import get_rng

from model.protocol import CONFIG, MAGIC, VERSION, Config, packet, parse_config


def random_config(rng) -> Config:
    sym = "".join(rng.choice("ABCDEFGHIJKLMNOPQRSTUVWXYZ") for _ in range(rng.randint(1, 8)))
    return Config(
        feed_group=rng.getrandbits(32),
        feed_port=rng.getrandbits(16),
        mode=rng.randrange(3),  # 2: injector run (ADR 0014)
        locate_valid=bool(rng.getrandbits(1)),
        symbol=sym,
        lo_price=rng.getrandbits(32),
        locate=rng.getrandbits(16),
    )


def good_payload(rng) -> bytes:
    p = bytearray(packet(CONFIG, rng.getrandbits(32), [random_config(rng).record()]))
    p[34:36] = rng.getrandbits(16).to_bytes(2)  # pad: ignored
    return bytes(p)


def payload(rng, p_good: float) -> tuple[bytes, bool, str]:
    """A control payload, whether it is a truncated datagram (tuser), and its kind."""
    p = bytearray(good_payload(rng))
    if rng.random() < p_good:
        return bytes(p), False, "good"
    kind = rng.choice(["byte", "mode", "lv", "short", "long", "count2", "tuser", "garbage", "tiny"])
    if kind == "byte":  # one header byte changed (pkt_seq bytes 4..7 are ignored)
        i = rng.choice([0, 1, 2, 3, 8, 9, 10, 11])
        p[i] ^= 1 << rng.randrange(8)
    elif kind == "mode":
        p[18] = rng.choice([3, 0x80, 255, rng.randint(3, 255)])  # 3: first bad value
    elif kind == "lv":
        p[19] = rng.choice([2, 3, 0x80, 255, rng.randint(2, 255)])
    elif kind == "short":
        p = p[: rng.choice([35, 34, 12, 1, rng.randint(1, 35)])]  # 35: one byte short
    elif kind == "long":
        p += bytes(rng.getrandbits(8) for _ in range(rng.choice([1, 2, 30, 64])))
    elif kind == "count2":  # count 2, length consistent: still not one config
        rec = random_config(rng).record()
        p = bytearray(struct.pack(">HBBIHH", MAGIC, VERSION, CONFIG, 0, 2, 24)) + p[12:] + rec
    elif kind == "tuser":
        return bytes(p), True, kind
    elif kind == "garbage":
        p = bytearray(rng.getrandbits(8) for _ in range(rng.randint(1, 80)))
    else:  # tiny
        p = bytearray(rng.getrandbits(8) for _ in range(rng.randint(1, 3)))
    return bytes(p), False, kind


def cfg_of(dut) -> Config:
    sym = int(dut.cfg_symbol_o.value).to_bytes(8)
    return Config(
        feed_group=int(dut.cfg_group_o.value),
        feed_port=int(dut.cfg_port_o.value),
        mode=int(dut.cfg_mode_o.value) | int(dut.cfg_inject_o.value) << 1,
        locate_valid=bool(int(dut.cfg_locate_valid_o.value)),
        symbol=sym.decode("latin-1").rstrip(" "),
        lo_price=int(dut.cfg_lo_price_o.value),
        locate=int(dut.cfg_locate_o.value),
    )


async def run(dut, name: str, n_pkts: int, p_good: float, gap: tuple[int, int],
              clear: tuple[int, int], flush: tuple[int, int],
              align: bool = False) -> dict[str, int]:  # fmt: skip
    """align: bytes without gaps, and a due flush waits for the cycle before a tlast, so the
    pending config loads in the same cycle the next one is accepted."""
    rng = get_rng(name)
    cocotb.start_soon(Clock(dut.clk_i, 10, unit="ns").start())
    for s in ("s_tvalid_i", "s_tlast_i", "s_tuser_i", "s_tdata_i", "book_busy_i", "flush_i"):
        getattr(dut, s).value = 0
    dut.rst_i.value = 1
    await ClockCycles(dut.clk_i, 3)
    await FallingEdge(dut.clk_i)
    dut.rst_i.value = 0

    # stimulus: (payload, tuser, src) per packet; bytes go out with random valid gaps
    pkts = []
    for _ in range(n_pkts):
        p, tuser, kind = payload(rng, p_good)
        src = (rng.getrandbits(48), rng.getrandbits(32), rng.getrandbits(16))
        pkts.append((p, tuser, kind, src))

    cov = dict.fromkeys(["ok", "bad", "loads", "superseded", "acks", "load_and_accept"], 0)
    # reference state, as the RTL holds it at the start of the current cycle
    latest = None  # (accepted, record bytes, src, Config) of the last accepted config
    pending = False  # latest not yet loaded
    loaded = None  # the config on cfg_*_o
    in_seq = False  # RST, CLEAR or ACK (else OFF before the first load, or RUN)
    load_q = None  # config loaded at the last edge: RST (stats_clear) now
    verdict_q = None  # accept/reject of last cycle's tlast: ctrl_ok/bad pulse now
    book_rst_q = False
    busy_left = 0
    flush_at = None
    ack_rec = None
    pi, bi, idle = 0, 0, 0
    cyc = 0
    while pi < n_pkts or in_seq or pending or verdict_q is not None or load_q is not None:
        cyc += 1
        assert cyc < 2_000_000, "no progress"
        await FallingEdge(dut.clk_i)
        # book model: busy right after a reset edge, for a random clear time
        if book_rst_q:
            busy_left = rng.randint(*clear)
        busy = busy_left > 0
        busy_left = max(0, busy_left - 1)
        dut.book_busy_i.value = int(busy)
        driving = pi < n_pkts and idle == 0 and (align or rng.random() < 0.8)
        flushing = flush_at == cyc
        if align and flush_at is not None and flush_at <= cyc:
            flushing = pi >= n_pkts or driving and bi == len(pkts[pi][0]) - 2
            flush_at = cyc if flushing else flush_at
        dut.flush_i.value = int(flushing)
        # payload bytes
        tlast_now = None
        if driving:
            p, tuser, kind, src = pkts[pi]
            dut.s_tvalid_i.value = 1
            dut.s_tdata_i.value = p[bi]
            last = bi == len(p) - 1
            dut.s_tlast_i.value = int(last)
            dut.s_tuser_i.value = int(tuser and last)
            dut.src_mac_i.value, dut.src_ip_i.value, dut.src_port_i.value = src
            if last:
                cfg = None if tuser else parse_config(p)
                tlast_now = (cfg is not None, p[12:36], src, cfg)
                pi, bi = pi + 1, 0
                idle = rng.randint(*gap)
            else:
                bi += 1
        else:
            dut.s_tvalid_i.value = 0
            dut.s_tlast_i.value = 0
            dut.s_tuser_i.value = 0
            idle = max(0, idle - 1)
        await ReadOnly()

        ok, bad = int(dut.ctrl_ok_o.value), int(dut.ctrl_bad_o.value)
        want = (0, 0) if verdict_q is None else (int(verdict_q), int(not verdict_q))
        assert (ok, bad) == want, f"cycle {cyc}: ok/bad {(ok, bad)} want {want}"
        feed_rst, book_rst = int(dut.feed_rst_o.value), int(dut.book_rst_o.value)
        clr, ack_req = int(dut.stats_clear_o.value), int(dut.ack_req_o.value)

        # RST: exactly the cycle after a load; cfg/dst are the loaded config from then on
        assert clr == int(load_q is not None), f"cycle {cyc}: stats_clear {clr}"
        if clr:
            loaded, in_seq, ack_rec = load_q, True, None
            cov["loads"] += 1
        if loaded is not None:
            cfg = loaded[3]
            assert cfg_of(dut) == cfg, f"cycle {cyc}: cfg {cfg_of(dut)} want {cfg}"
            dst = (int(dut.dst_mac_o.value), int(dut.dst_ip_o.value), int(dut.dst_port_o.value))
            assert dst == loaded[2], f"cycle {cyc}: dst {dst} want {loaded[2]}"
        assert feed_rst == int(in_seq or loaded is None), f"cycle {cyc}: feed_rst {feed_rst}"
        assert book_rst == int(loaded is None or clr), f"cycle {cyc}: book_rst {book_rst}"
        # ack request (CLEAR, book idle) and flush (out_builder model)
        if ack_req:
            assert in_seq and not clr and ack_rec is None and not busy, f"cycle {cyc}: ack_req"
            ack_rec = int(dut.ack_rec_o.value)
            assert ack_rec.to_bytes(24) == loaded[1], f"cycle {cyc}: ack_rec"
            flush_at = cyc + rng.randint(*flush)
            cov["acks"] += 1
        elif in_seq and ack_rec is None and not clr and not busy:
            raise AssertionError(f"cycle {cyc}: book idle in CLEAR but no ack_req")
        if ack_rec is not None:
            assert int(dut.ack_rec_o.value) == ack_rec, f"cycle {cyc}: ack_rec changed"

        # next edge: a pending config loads if idle now; this cycle's tlast is judged
        load_now = not in_seq and pending
        load_q = latest if load_now else None
        verdict_q = None
        if tlast_now is not None:
            verdict_q = tlast_now[0]
            cov["ok" if verdict_q else "bad"] += 1
            if verdict_q:
                cov["superseded"] += pending and not load_now
                cov["load_and_accept"] += load_now
                latest, pending = tlast_now, True
            elif load_now:
                pending = False
        elif load_now:
            pending = False
        if flushing:
            in_seq, flush_at, ack_rec = False, None, None
        book_rst_q = bool(book_rst)

    await ClockCycles(dut.clk_i, 2)
    assert loaded is latest, "the last accepted config was never loaded"
    cocotb.log.info("%s: %d cycles, %s", name, cyc, cov)
    return cov


@cocotb.test()
async def mixed_packets(dut):
    """Every malformed kind among good configs, spaced so most configs finish first."""
    cov = await run(dut, "mixed_packets", 600, 0.4, (0, 400), (5, 300), (1, 80))
    assert cov["ok"] > 100 and cov["bad"] > 200 and cov["acks"] > 50


@cocotb.test()
async def back_to_back(dut):
    """Configs faster than the sequence: pending configs are replaced, the latest wins."""
    cov = await run(dut, "back_to_back", 400, 0.8, (0, 3), (50, 400), (1, 200))
    assert cov["superseded"] > 20 and cov["loads"] > 10


@cocotb.test()
async def instant_book(dut):
    """Book clear and flush as fast as allowed (1 cycle each)."""
    cov = await run(dut, "instant_book", 300, 0.7, (0, 60), (1, 1), (1, 1))
    assert cov["acks"] > 50


@cocotb.test()
async def accept_on_load(dut):
    """A config accepted in the cycle the pending one loads (first RUN cycle after the
    flush): the new one must stay pending, not be lost."""
    cov = await run(dut, "accept_on_load", 200, 0.9, (0, 2), (20, 60), (1, 30), align=True)
    assert cov["load_and_accept"] > 20
