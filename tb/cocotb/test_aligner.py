"""cocotb tests for rtl/feed/msg_aligner (DATA_W 64, ADR 0015 step 2).

Why these tests matter: itch_decoder at 64 bits reads a field by its byte offset in the
message, assuming byte k sits in beat k // 8, lane k % 8. If the aligner shifts a message
by one lane, drops or repeats a byte at a beat boundary, or splits a tail wrongly, every
field after that point is wrong. Expected output is computed independently of the RTL:
the message's bytes packed from lane 0 (tb_common.axis.bytes_to_beats), so each output
beat's tkeep and kept bytes must match exactly, with the message's seq, T0 and tuser.

Input is in mold_deframer's output shape: the first beat starts at any lane, middle
beats are full, the last ends at any lane; lanes outside tkeep carry garbage. Coverage of
every (start lane, length mod 8) pair, single-beat messages, and the two-beat tail flush
is asserted. At full rate the throughput bound (input beats + flushes) is asserted.
"""

from __future__ import annotations

from collections import Counter

import cocotb
from cocotb.clock import Clock
from cocotb.triggers import ClockCycles, RisingEdge
from tb_common import InOrderScoreboard, get_rng
from tb_common.axis import bytes_to_beats

LANES = 8


def deframer_beats(rng, msg: bytes, off: int) -> list[tuple[int, int, bool]]:
    """(data, keep, last) beats as mold_deframer emits them for a message starting at lane
    `off`; unkept lanes are random."""
    beats = []
    pos, lane = 0, off
    while pos < len(msg):
        n = min(LANES - lane, len(msg) - pos)
        data = bytearray(rng.getrandbits(8) for _ in range(LANES))
        data[lane : lane + n] = msg[pos : pos + n]
        keep = ((1 << n) - 1) << lane
        pos += n
        beats.append((int.from_bytes(data, "little"), keep, pos == len(msg)))
        lane = 0
    return beats


def messages(rng, n: int) -> list[tuple[bytes, int, int, int, int]]:
    """(bytes, start lane, seq, t0, tuser): every length 1..24 at every lane, then random."""
    out = []
    for length in range(1, 3 * LANES + 1):
        for off in range(LANES):
            out.append((length, off))
    out += [
        (rng.choice([rng.randrange(1, 10), rng.randrange(1, 80)]), rng.randrange(LANES))
        for _ in range(n)
    ]
    rng.shuffle(out)
    return [
        (
            bytes(rng.getrandbits(8) for _ in range(length)),
            off,
            rng.getrandbits(64),
            rng.getrandbits(64),
            int(rng.random() < 0.05),
        )
        for length, off in out
    ]


async def run(dut, name: str, msgs, p_valid: float, p_ready: float) -> Counter:
    rng = get_rng(name)
    cocotb.start_soon(Clock(dut.clk_i, 8, unit="ns").start())
    dut.s_tvalid_i.value = 0
    dut.m_tready_i.value = 0
    dut.rst_i.value = 1
    await ClockCycles(dut.clk_i, 3)
    dut.rst_i.value = 0

    sb = InOrderScoreboard(name, fmt=lambda x: f"seq={x[0]:#x} beats={len(x[3])}")
    beats_in = []
    cov: Counter = Counter()
    for msg, off, seq, t0, user in msgs:
        out = tuple((b.keep, b.data) for b in bytes_to_beats(msg, LANES))
        sb.expect((seq, t0, user, out))
        bs = deframer_beats(rng, msg, off)
        beats_in += [(d, k, last, seq, t0, user if last else 0) for d, k, last in bs]
        cov[f"off{off}_mod{len(msg) % LANES}"] += 1
        cov["single_beat"] += len(bs) == 1
        last_n = bin(bs[-1][1]).count("1")
        cov["flush"] += (len(msg) - last_n) % LANES + last_n > LANES  # tail leaves in two

    cyc = {"n": 0, "first": None, "last": 0}

    async def monitor():
        cur: list[tuple[int, int]] = []
        meta = None
        while True:
            await RisingEdge(dut.clk_i)
            cyc["n"] += 1
            if int(dut.s_tvalid_i.value) and int(dut.s_tready_o.value) and cyc["first"] is None:
                cyc["first"] = cyc["n"]
            if int(dut.m_tvalid_o.value) and int(dut.m_tready_i.value):
                keep = int(dut.m_tkeep_o.value)
                data = int(dut.m_tdata_o.value)
                mask = sum(0xFF << (8 * i) for i in range(LANES) if keep >> i & 1)
                m = (int(dut.m_seq_o.value), int(dut.m_t0_o.value))
                assert meta is None or m == meta, f"{name}: seq/t0 changed within a message"
                meta = m
                cur.append((keep, data & mask))
                last = int(dut.m_tlast_o.value)
                assert last or not int(dut.m_tuser_o.value)
                if last:
                    sb.observe((*m, int(dut.m_tuser_o.value), tuple(cur)))
                    cur, meta = [], None
                    cyc["last"] = cyc["n"]
            dut.m_tready_i.value = int(rng.random() < p_ready)

    cocotb.start_soon(monitor())

    # Driver: tvalid holds with stable data until accepted.
    for d, k, last, seq, t0, user in beats_in:
        while rng.random() >= p_valid:
            dut.s_tvalid_i.value = 0
            await RisingEdge(dut.clk_i)
        dut.s_tdata_i.value = d
        dut.s_tkeep_i.value = k
        dut.s_tlast_i.value = int(last)
        dut.s_tuser_i.value = user
        dut.s_seq_i.value = seq
        dut.s_t0_i.value = t0
        dut.s_tvalid_i.value = 1
        await RisingEdge(dut.clk_i)
        while not int(dut.s_tready_o.value):
            await RisingEdge(dut.clk_i)
    dut.s_tvalid_i.value = 0
    for _ in range(200):
        if not sb.pending:
            break
        await ClockCycles(dut.clk_i, 10)
    sb.check_done()
    cov["cycles"] = cyc["last"] - (cyc["first"] or 0) + 1
    cov["beats_in"] = len(beats_in)
    cocotb.log.info(
        "%s: %d messages, %d beats in, %d cycles, flushes %d",
        name,
        len(msgs),
        len(beats_in),
        cov["cycles"],
        cov["flush"],
    )
    return cov


def check_coverage(cov: Counter) -> None:
    want = [f"off{o}_mod{m}" for o in range(LANES) for m in range(LANES)]
    want += ["single_beat", "flush"]
    missing = [k for k in want if cov[k] == 0]
    assert not missing, f"coverage holes: {missing}"


@cocotb.test()
async def full_rate(dut):
    """Every start lane and length, back to back: exact packing and the throughput bound."""
    msgs = messages(get_rng("aln_full_gen"), 3000)
    cov = await run(dut, "aln_full", msgs, 1.0, 1.0)
    check_coverage(cov)
    bound = cov["beats_in"] + cov["flush"] + 2  # one cycle per beat, one per flush
    assert cov["cycles"] <= bound, f"{cov['cycles']} cycles > bound {bound}"


@cocotb.test()
async def backpressure(dut):
    """The same with input gaps and output stalls (residual and flush held across stalls)."""
    msgs = messages(get_rng("aln_bp_gen"), 3000)
    cov = await run(dut, "aln_bp", msgs, 0.6, 0.4)
    check_coverage(cov)
