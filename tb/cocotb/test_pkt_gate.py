"""cocotb tests for rtl/lib/pkt_gate: whole packets pass or drop while the consumer resets.

Why these tests matter: the gate sits between the feed's udp_rx and the decoder, whose
reset is a config. If it opened mid-packet, the decoder would parse a MoldUDP64 tail as a
header; if it stalled while blocked, eth_rx would back up and control packets (same input
stream) would be lost; if it dropped a packet while open, the book would silently miss
messages. Reference: a packet must arrive, complete and in order, exactly when block_i
was low on every cycle from its first beat to its last; while block_i is high the input
is always ready and nothing is output. Block toggles land before, inside and between
packets, with random valid and ready.
"""

from __future__ import annotations

import cocotb
from cocotb.clock import Clock
from cocotb.triggers import ClockCycles, FallingEdge, ReadOnly
from tb_common import get_rng


async def run(dut, name: str, n_pkts: int, p_block: float, hold: tuple[int, int]) -> None:
    rng = get_rng(name)
    cocotb.start_soon(Clock(dut.clk_i, 10, unit="ns").start())
    for s in ("block_i", "s_tvalid_i", "s_tlast_i", "s_tuser_i", "s_tdata_i", "m_tready_i"):
        getattr(dut, s).value = 0
    dut.rst_i.value = 1
    await ClockCycles(dut.clk_i, 3)
    await FallingEdge(dut.clk_i)
    dut.rst_i.value = 0

    pkts = [
        (bytes(rng.getrandbits(8) for _ in range(rng.choice([1, 2, rng.randint(1, 40)]))),
         rng.random() < 0.2)
        for _ in range(n_pkts)
    ]  # fmt: skip
    want: list[tuple[bytes, bool]] = []
    got: list[tuple[bytes, bool]] = []
    part = bytearray()
    pi, bi = 0, 0
    spoiled = False
    block, block_left = 0, 0
    passed = dropped = 0
    valid = 0
    while pi < n_pkts:
        await FallingEdge(dut.clk_i)
        if block_left == 0:
            block = int(rng.random() < p_block)
            block_left = rng.randint(*hold)
        block_left -= 1
        dut.block_i.value = block
        if not valid:  # AXI: valid stays up until the beat is taken
            valid = int(rng.random() < 0.7)
        p, tuser = pkts[pi]
        dut.s_tvalid_i.value = valid
        dut.s_tdata_i.value = p[bi]
        dut.s_tlast_i.value = int(bi == len(p) - 1)
        dut.s_tuser_i.value = int(tuser and bi == len(p) - 1)
        dut.m_tready_i.value = int(rng.random() < 0.7)
        await ReadOnly()
        s_ready, m_valid = int(dut.s_tready_o.value), int(dut.m_tvalid_o.value)
        if block:
            assert s_ready and not m_valid, f"{name}: blocked but ready {s_ready} valid {m_valid}"
            part.clear()  # the consumer is in reset
        if m_valid and int(dut.m_tready_i.value):
            part.append(int(dut.m_tdata_o.value))
            if int(dut.m_tlast_o.value):
                got.append((bytes(part), bool(int(dut.m_tuser_o.value))))
                part.clear()
        # reference: block on any cycle of a packet (first beat .. last beat) drops it
        if bi > 0 or (valid and s_ready):
            spoiled = spoiled or bool(block)
        if valid and s_ready:
            valid = 0
            if bi == len(p) - 1:
                if spoiled:
                    dropped += 1
                else:
                    want.append((p, tuser))
                    passed += 1
                pi, bi, spoiled = pi + 1, 0, False
            else:
                bi += 1
    assert got == want, f"{name}: {len(got)} packets out, want {len(want)}" + next(
        (f"; first diff at {k}" for k, (g, w) in enumerate(zip(got, want, strict=False)) if g != w),
        "",
    )
    assert passed > n_pkts // 10 and dropped > n_pkts // 10, (passed, dropped)
    cocotb.log.info("%s: %d passed, %d dropped", name, passed, dropped)


@cocotb.test()
async def short_blocks(dut):
    """Blocks of 1-3 cycles: they land inside packets, on first and last beats."""
    await run(dut, "short_blocks", 1500, 0.15, (1, 3))


@cocotb.test()
async def long_blocks(dut):
    """Long blocked and open stretches: whole packets drained, then passed."""
    await run(dut, "long_blocks", 1500, 0.4, (20, 120))
