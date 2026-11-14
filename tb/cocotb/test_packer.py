"""cocotb tests for rtl/lib/byte_packer (ADR 0015: the network feed into the DATA_W 64
deframer).

Why these matter: the packer decides the byte lane of every network feed byte at DATA_W
64; one lane off and every message after it is garbage. The deframer requires beats full
except a packet's last, tkeep contiguous from lane 0, so each beat must be exactly
tb_common's bytes_to_beats of the packet (an independent packer). The packet's T0 must
be the one presented with its first byte (meta is changed every cycle here, so sampling
it at any other byte is caught). tuser marks a truncated datagram on tlast, so the last
beat must carry the last byte's. And the packer must never slow the byte stream: at full
rate it takes one byte per cycle, so at 1 byte/cycle in, it adds no stall.
"""

from __future__ import annotations

from collections import Counter

import cocotb
from cocotb.clock import Clock
from cocotb.triggers import ClockCycles, FallingEdge, RisingEdge
from tb_common import get_rng
from tb_common.axis import bytes_to_beats

LANES = 8


class Bench:
    def __init__(self, dut) -> None:
        self.dut = dut
        self.cov: Counter[str] = Counter()

    async def reset(self) -> None:
        d = self.dut
        d.rst_i.value = 1
        d.s_tvalid_i.value = 0
        d.m_tready_i.value = 0
        await ClockCycles(d.clk_i, 3)
        await FallingEdge(d.clk_i)
        d.rst_i.value = 0

    async def run(
        self, rng, pkts: list[tuple[bytes, list[int]]], p_valid: float, p_ready: float, limit: int
    ) -> tuple[list[tuple], list[tuple], int]:
        """Send pkts (payload, per-byte tuser); return (got beats, expected beats, cycles
        from the first input byte to the last output beat)."""
        d = self.dut
        want = []
        stream = []  # (byte, last, user, packet index)
        for k, (p, users) in enumerate(pkts):
            beats = bytes_to_beats(p, LANES)
            for j, b in enumerate(beats):
                n = b.keep.bit_length()
                fin = j * LANES + n - 1  # index of the beat's final byte
                want.append((b.data, b.keep, int(b.last), users[fin], k))
            stream += [(byte, int(i == len(p) - 1), users[i], k) for i, byte in enumerate(p)]
        metas: dict[int, int] = {}  # packet -> meta presented with its first byte
        got: list[tuple] = []
        i = 0
        first_in = last_out = -1
        for cyc in range(limit):
            await FallingEdge(d.clk_i)
            meta = rng.getrandbits(64)
            d.s_meta_i.value = meta
            v = i < len(stream) and rng.random() < p_valid
            d.s_tvalid_i.value = int(v)
            if i < len(stream):
                byte, last, user, _ = stream[i]
                d.s_tdata_i.value = byte
                d.s_tlast_i.value = last
                d.s_tuser_i.value = user
            d.m_tready_i.value = int(rng.random() < p_ready)
            await RisingEdge(d.clk_i)  # values read here are the ones the edge sampled
            if v and int(d.s_tready_o.value):
                k = stream[i][3]
                if k not in metas:
                    metas[k] = meta
                first_in = cyc if first_in < 0 else first_in
                i += 1
            elif v:
                self.cov["input_stalled"] += 1
            if int(d.m_tvalid_o.value) and int(d.m_tready_i.value):
                keep = int(d.m_tkeep_o.value)
                data = int(d.m_tdata_o.value) & int.from_bytes(
                    b"".join(b"\xff" if keep >> j & 1 else b"\x00" for j in range(LANES)),
                    "little")  # fmt: skip
                got.append((data, keep, int(d.m_tlast_o.value), int(d.m_tuser_o.value),
                            int(d.m_meta_o.value)))  # fmt: skip
                last_out = cyc
                if int(d.m_tlast_o.value):
                    self.cov[f"last_lane_{keep.bit_length() - 1}"] += 1
            elif int(d.m_tvalid_o.value):
                self.cov["output_held"] += 1
            if len(got) == len(want):
                exp = [(data, keep, last, user, metas[k]) for data, keep, last, user, k in want]
                return got, exp, last_out - first_in + 1
        raise AssertionError(f"{len(got)} of {len(want)} beats after {limit} cycles")


def random_pkts(rng, n: int, max_len: int = 40) -> list[tuple[bytes, list[int]]]:
    out = []
    for _ in range(n):
        ln = rng.randint(1, max_len)
        out.append((rng.randbytes(ln), [int(rng.random() < 0.3) for _ in range(ln)]))
    return out


async def start(dut) -> Bench:
    cocotb.start_soon(Clock(dut.clk_i, 8, unit="ns").start())
    b = Bench(dut)
    await b.reset()
    return b


@cocotb.test()
async def full_rate_every_last_lane(dut):
    """Lengths 1..24 (every final lane, 1 to 3 beats) back to back at full rate: exact
    beats, and one byte per cycle (cycles = bytes + 1 for the last beat's register)."""
    rng = get_rng("packer_full_rate")
    b = await start(dut)
    pkts = [(rng.randbytes(ln), [rng.getrandbits(1) for _ in range(ln)]) for ln in range(1, 25)]
    got, exp, cycles = await b.run(rng, pkts, 1.0, 1.0, 2000)
    assert got == exp
    n = sum(len(p) for p, _ in pkts)
    assert cycles == n + 1, f"{n} bytes in {cycles} cycles"
    assert b.cov["input_stalled"] == 0
    assert all(b.cov[f"last_lane_{j}"] for j in range(LANES)), b.cov


@cocotb.test()
async def random_valid_and_backpressure(dut):
    """Random gaps on both sides: exact beats, tuser of each beat's final byte, the
    packet's first-byte meta on every beat."""
    rng = get_rng("packer_random")
    b = await start(dut)
    for p_valid, p_ready in ((0.5, 0.5), (1.0, 0.3), (0.1, 1.0), (0.9, 0.9)):
        got, exp, _ = await b.run(rng, random_pkts(rng, 300), p_valid, p_ready, 200_000)
        assert got == exp, f"p_valid {p_valid} p_ready {p_ready}"
    assert b.cov["output_held"] and b.cov["input_stalled"], b.cov
    assert all(b.cov[f"last_lane_{j}"] for j in range(LANES)), b.cov


@cocotb.test()
async def reset_mid_packet(dut):
    """A reset drops a partial beat and a waiting beat; the next packet starts at lane 0
    with its own meta."""
    rng = get_rng("packer_reset")
    b = await start(dut)
    d = dut
    for held in (False, True):  # partial beat accumulating; complete beat waiting
        await FallingEdge(d.clk_i)
        d.m_tready_i.value = 0
        for byte in range(11 if held else 5):
            d.s_tvalid_i.value = 1
            d.s_tdata_i.value = 0xA0 + byte
            d.s_tlast_i.value = 0
            d.s_tuser_i.value = 0
            d.s_meta_i.value = 0xDEAD
            await FallingEdge(d.clk_i)
        d.s_tvalid_i.value = 0
        assert int(d.m_tvalid_o.value) == int(held)
        await b.reset()
        assert not int(d.m_tvalid_o.value)
        got, exp, _ = await b.run(rng, random_pkts(rng, 20), 0.7, 0.7, 20_000)
        assert got == exp
