"""cocotb tests for rtl/out/out_builder: records, stats and acks -> UDP payloads.

Why these tests matter: this is the only path from the book to the host. Every datagram
must parse under `model.protocol.parse` (header, count, record_len, exact length), pkt_seq
must start at 0 with the config ack and step by 1 per datagram (a jump is how the host
detects loss), and the records must arrive exactly as pushed, in order, none dropped,
duplicated or misaligned (docs/protocol.md: "No record is ever dropped after it is
produced"). Batching is flush-on-idle: one record per datagram at light load, MAX_COUNT
under saturation, never more. Stats are snapshotted on one cycle: every counter in a
stats datagram is driven with the current cycle number, so a snapshot that is taken
piecewise shows mixed cycles. An ack flushes the previous run (including a
half-written record), resets pkt_seq and applies the new mode, but never cuts the
datagram in flight. Nothing at all is sent before the first ack.
"""

from __future__ import annotations

import cocotb
from cocotb.clock import Clock
from cocotb.triggers import ClockCycles, RisingEdge
from cocotb.utils import get_sim_time
from tb_common import get_rng
from tb_common.axis import AxisSink

from model.protocol import (
    BBO,
    CONFIG_ACK,
    MAX_COUNT,
    N_STATS,
    RECORD_LEN,
    STATS,
    TRACE,
    Config,
    Datagram,
    parse,
)

STAT_REP = sum(1 << (64 * (N_STATS - 1 - k) + 8) for k in range(N_STATS))
STAT_BASE = sum(k << (64 * (N_STATS - 1 - k)) for k in range(N_STATS))
REC_KIND = {0: BBO, 1: TRACE}
PERIOD_NS = 10


def now(dut) -> int:
    """Cycle number of the most recent rising edge."""
    return int(get_sim_time(unit="ns")) // PERIOD_NS


class Harness:
    def __init__(self, dut, name: str, p_ready: float = 1.0) -> None:
        self.dut = dut
        self.rng = get_rng(name)
        self.dgrams: list[tuple[int, Datagram]] = []  # (cycle first byte valid, datagram)
        self.starts: list[int] = []  # cycles a datagram's first byte became valid
        self.pulse_dgrams = 0
        self.pulse_recs = 0
        self.flushes = 0
        self._in_dgram = False
        cocotb.start_soon(Clock(dut.clk_i, PERIOD_NS, unit="ns").start())
        dut.rst_i.value = 1
        dut.mode_i.value = 0
        dut.s_valid_i.value = 0
        dut.s_rec_i.value = 0
        dut.stats_req_i.value = 0
        dut.ack_req_i.value = 0
        dut.ack_rec_i.value = 0
        self.sink = AxisSink(dut, dut.clk_i, "m_", self.rng, p_ready, self._on_frame)
        cocotb.start_soon(self._tick())

    async def reset(self) -> None:
        self.dut.rst_i.value = 1
        await ClockCycles(self.dut.clk_i, 3)
        self.dut.rst_i.value = 0

    def _on_frame(self, frame) -> None:
        payload = bytes(b.data for b in frame)
        self.dgrams.append((self.starts[len(self.dgrams)], parse(payload)))

    async def _tick(self) -> None:
        dut = self.dut
        while True:
            await RisingEdge(dut.clk_i)
            cyc = now(dut)
            dut.stats_i.value = STAT_BASE + cyc * STAT_REP
            if int(dut.m_tvalid_o.value) and not self._in_dgram:
                self._in_dgram = True
                self.starts.append(cyc)
            if int(dut.m_tvalid_o.value) and int(dut.m_tready_i.value) and int(dut.m_tlast_o.value):
                self._in_dgram = False
            self.pulse_dgrams += int(dut.dgram_o.value)
            self.pulse_recs += int(dut.dgram_recs_o.value)
            self.flushes += int(dut.flush_o.value)

    async def push(self, rec: bytes) -> int:
        """Offer one record (padded with junk to 64 bytes); return the accept cycle."""
        dut = self.dut
        pad = bytes(self.rng.getrandbits(8) for _ in range(64 - len(rec)))
        dut.s_rec_i.value = int.from_bytes(rec + pad)
        dut.s_valid_i.value = 1
        while True:
            await RisingEdge(dut.clk_i)
            if int(dut.s_ready_o.value):
                break
        dut.s_valid_i.value = 0
        return now(dut)

    async def pulse(self, sig) -> None:
        sig.value = 1
        await RisingEdge(self.dut.clk_i)
        sig.value = 0

    async def ack(self, cfg: Config) -> None:
        self.dut.mode_i.value = cfg.mode
        self.dut.ack_rec_i.value = int.from_bytes(cfg.record())
        n = self.flushes
        await self.pulse(self.dut.ack_req_i)
        # Contract: the record source stays in reset until flush_o.
        while self.flushes == n:
            await RisingEdge(self.dut.clk_i)

    def random_records(self, mode: int, n: int) -> list[bytes]:
        size = RECORD_LEN[REC_KIND[mode]]
        return [bytes(self.rng.getrandbits(8) for _ in range(size)) for _ in range(n)]

    async def idle(self, quiet: int = 200, limit: int = 200_000) -> None:
        """Wait until no datagram has started or been in flight for `quiet` cycles."""
        last = (len(self.starts), len(self.dgrams))
        still = 0
        for _ in range(limit):
            await RisingEdge(self.dut.clk_i)
            now = (len(self.starts), len(self.dgrams))
            still = still + 1 if now == last and not self._in_dgram else 0
            last = now
            if still >= quiet:
                return
        raise AssertionError("output never went idle")


def check_run(dgrams: list[tuple[int, Datagram]], cfg: Config, recs: list[bytes]) -> None:
    """One run: an ack with pkt_seq 0 echoing cfg, then pkt_seq +1 per datagram, the
    pushed records exactly and in order in datagrams of the config's mode, and stats
    snapshots taken on one cycle each, in time order."""
    assert dgrams, "no datagrams"
    _, first = dgrams[0]
    assert (first.kind, first.pkt_seq, first.records) == (CONFIG_ACK, 0, (cfg.record(),))
    got: list[bytes] = []
    last_snap = -1
    for i, (start, d) in enumerate(dgrams):
        assert d.pkt_seq == i, f"datagram {i}: pkt_seq {d.pkt_seq}"
        if i == 0:
            continue
        if d.kind == STATS:
            vals = [int.from_bytes(r) for r in d.records]
            assert [v & 0xFF for v in vals] == list(range(N_STATS)), "stats order"
            cycles = {v >> 8 for v in vals}
            assert len(cycles) == 1, f"stats snapshot spans cycles {sorted(cycles)}"
            snap = cycles.pop()
            assert last_snap < snap < start, f"snapshot cycle {snap} vs start {start}"
            last_snap = snap
        else:
            assert d.kind == REC_KIND[cfg.mode], f"datagram {i}: type {d.kind:#x}"
            assert len(d.records) <= MAX_COUNT[d.kind]
            got.extend(d.records)
    assert len(got) == len(recs), f"{len(got)} records out, {len(recs)} pushed"
    for k, (g, w) in enumerate(zip(got, recs, strict=True)):
        assert g == w, f"record {k}: got {g.hex()} want {w.hex()}"


@cocotb.test()
async def nothing_before_ack(dut):
    """No records or stats leave before the first ack; the ack flushes what was queued."""
    h = Harness(dut, "nothing_before_ack")
    await h.reset()
    for r in h.random_records(0, 3):
        await h.push(r)
    for _ in range(3):
        await h.pulse(dut.stats_req_i)
        await ClockCycles(dut.clk_i, 50)
    await ClockCycles(dut.clk_i, 500)
    assert not h.starts, "sent something before the first ack"
    cfg = Config(lo_price=123_400, locate=7, locate_valid=True)
    await h.ack(cfg)
    await h.idle()
    check_run(h.dgrams, cfg, [])
    assert len(h.dgrams) == 1 and h.flushes == 1


async def stream(dut, mode: int) -> None:
    h = Harness(dut, f"stream_mode{mode}", p_ready=0.5)
    await h.reset()
    cfg = Config(mode=mode, symbol="AAPL", lo_price=22_407_00)
    await h.ack(cfg)
    recs = h.random_records(mode, 400)

    async def stats() -> None:
        for _ in range(12):
            # > the longest datagram at p_ready 0.5 (~2,900 cycles): requests that
            # arrive while one is pending merge, by design (the module header)
            await ClockCycles(dut.clk_i, h.rng.randrange(3500, 6000))
            await h.pulse(dut.stats_req_i)

    st = cocotb.start_soon(stats())
    for r in recs:
        await h.push(r)
        if h.rng.random() < 0.3:
            await ClockCycles(dut.clk_i, h.rng.randrange(1, 400))
    await st
    await h.idle()
    check_run(h.dgrams, cfg, recs)
    n_stats = sum(d.kind == STATS for _, d in h.dgrams)
    assert n_stats == 12, f"{n_stats} stats datagrams for 12 requests"
    assert h.pulse_dgrams == len(h.dgrams) and h.pulse_recs == len(recs), "status pulses"
    batched = max(len(d.records) for _, d in h.dgrams[1:] if d.kind != STATS)
    cocotb.log.info("mode %d: %d datagrams, largest batch %d", mode, len(h.dgrams), batched)


@cocotb.test()
async def stream_bbo(dut):
    """Random BBO records, gaps, backpressure and stats requests: exact record stream."""
    await stream(dut, 0)


@cocotb.test()
async def stream_trace(dut):
    """As stream_bbo with 40-byte trace records (the record's other 24 bytes are junk)."""
    await stream(dut, 1)


@cocotb.test()
async def latency_matches_header(dut):
    """Light load: one record per datagram, first header byte REC_LEN + 2 cycles after
    the record is accepted (the module header's figure)."""
    for mode in (0, 1):
        h = Harness(dut, f"latency_mode{mode}")
        await h.reset()
        cfg = Config(mode=mode)
        await h.ack(cfg)
        await h.idle(quiet=20)
        recs = h.random_records(mode, 20)
        for r in recs:
            n = len(h.starts)
            acc = await h.push(r)
            await h.idle(quiet=100)
            lat = h.starts[n] - acc
            assert lat == RECORD_LEN[REC_KIND[mode]] + 2, f"mode {mode}: latency {lat}"
        check_run(h.dgrams, cfg, recs)
        assert all(len(d.records) == 1 for _, d in h.dgrams), "light load batched"


@cocotb.test()
async def saturation_batches_max(dut):
    """Sink stalled until the FIFO fills: the backlog leaves in MAX_COUNT batches."""
    for mode in (0, 1):
        h = Harness(dut, f"saturation_mode{mode}")
        await h.reset()
        cfg = Config(mode=mode)
        await h.ack(cfg)
        await h.idle(quiet=20)
        h.sink.p_ready = 0.0
        recs = h.random_records(mode, 120)
        pusher = cocotb.start_soon(_push_all(h, recs))
        low = 0
        while low < 200:  # writer stuck: the FIFO is full
            await RisingEdge(dut.clk_i)
            low = 0 if int(dut.s_ready_o.value) else low + 1
        # a 1-record datagram started on the first record and waits on the sink
        h.sink.p_ready = 1.0
        await pusher
        await h.idle()
        check_run(h.dgrams, cfg, recs)
        counts = [len(d.records) for _, d in h.dgrams[1:]]
        mx = MAX_COUNT[REC_KIND[mode]]
        assert counts[0] == 1 and counts[1] == mx, f"mode {mode}: batches {counts}"


async def _push_all(h: Harness, recs: list[bytes]) -> None:
    for r in recs:
        await h.push(r)


@cocotb.test()
async def ack_mid_traffic(dut):
    """A second ack while a datagram is in flight: the datagram completes intact, the
    rest of the queue is dropped, pkt_seq restarts at 0. The new mode is set long before
    the ack (the control block latches the config, then clears the book's RAMs), and
    the old run's queued records must still leave as BBO datagrams."""
    h = Harness(dut, "ack_mid_traffic", p_ready=0.3)
    await h.reset()
    cfg0 = Config(mode=0)
    await h.ack(cfg0)
    old = h.random_records(0, 150)
    cfg1 = Config(mode=1, symbol="MSFT")
    n_mode = 0
    for k, r in enumerate(old):
        if k == 60:  # new mode while the old run still has 90 records to go
            dut.mode_i.value = cfg1.mode
            n_mode = len(h.starts)
        await h.push(r)
    while not h._in_dgram:
        await RisingEdge(dut.clk_i)
    n_before = len(h.starts)
    await h.ack(cfg1)
    new = h.random_records(1, 60)
    for r in new:
        await h.push(r)
    await h.idle()

    i_ack = next(i for i, (_, d) in enumerate(h.dgrams) if i > 0 and d.kind == CONFIG_ACK)
    assert i_ack == n_before, "ack did not wait for the datagram in flight, or was late"
    assert i_ack - n_mode >= 2, "too few old datagrams after the mode change to test it"
    assert all(d.kind == BBO for _, d in h.dgrams[1:i_ack]), "old run changed mode"
    sent_old = [r for _, d in h.dgrams[1:i_ack] for r in d.records]
    assert 0 < len(sent_old) < len(old) and sent_old == old[: len(sent_old)], "old run"
    for i, (_, d) in enumerate(h.dgrams[:i_ack]):
        assert d.pkt_seq == i
    check_run(h.dgrams[i_ack:], cfg1, new)


@cocotb.test()
async def ack_flushes_half_record(dut):
    """An ack that starts while the writer is mid-record drops that record's remaining
    bytes, so the next run's records stay byte-aligned."""
    h = Harness(dut, "ack_flushes_half_record")
    await h.reset()
    cfg = Config(mode=0)
    await h.ack(cfg)
    a, b, c, d = h.random_records(0, 4)
    await h.push(a)
    await h.idle(quiet=100)
    await h.push(b)
    await ClockCycles(dut.clk_i, 5)
    assert not int(dut.s_ready_o.value), "writer should still be copying b"
    n_before = len(h.dgrams)
    await h.ack(cfg)
    await h.push(c)
    await h.push(d)
    await h.idle()
    check_run(h.dgrams[:n_before], cfg, [a])
    check_run(h.dgrams[n_before:], cfg, [c, d])
