"""Functional coverage (buildplan §8.2): the bins, and the samplers the tests call.

Transaction bins (ITCH type x side, MoldUDP64 paths, bucket fill, bitmap search, message
lane) are sampled from the stimulus a test drove and its scoreboard checked, replayed on a
fresh model with the RTL's geometry: the DUT's output matched the model message by message,
so the model saw the scenario the DUT saw. DUT-signal bins (FIFO occupancy, backpressure)
are sampled on the DUT itself (ob_core, test_core).

Each simulator process writes its bins to $COV_FILE at exit (tb/run.py sets it per
parameter set); `make cov` merges them (tb_common.coverage) and reports holes against
tb/cov_exclusions.py.
"""

from __future__ import annotations

import atexit
import os
from collections.abc import Iterable

import cocotb
from cocotb.triggers import ClockCycles, Edge, First, ReadOnly
from cocotb_coverage.coverage import CoverCross, CoverPoint
from tb_common import coverage

from model.bounded import REF_BITS, SHARES_BITS, TICK, BoundedBook, bucket
from model.itch import Add, Message
from model.moldudp64 import COUNT_END, HEADER, HEADER_LEN, LEN_PREFIX, SeqTracker

if os.environ.get("COV_FILE"):
    atexit.register(coverage.export, os.environ["COV_FILE"])

BOOK_TYPES = list("AFECXDU")
SIDES = ["B", "S"]
FILL_BINS = [*range(16), "full"]  # the chosen bucket's fill at an insert; full = overflow
SEARCH = ["same_group", "next_group", "far_group", "side_empty"]
MOLD = ["in_order", "gap", "duplicate", "heartbeat", "end_of_session", "session_change",
        "malformed_packet"]  # fmt: skip
LANE_TYPES = [*BOOK_TYPES, "R", "skipped"]
MII_LEVEL = ["empty", "low", "mid", "high", "near_full", "overflow"]
RECORD_FIFO = ["empty", "partial", "full"]
# ob_core's AXI-Stream-style interfaces (valid/ready names in rtl/top/ob_core.sv)
INTERFACES = {
    "eth_rx>udp_rx": ("f_tvalid", "f_tready"),
    "udp_rx>pkt_gate": ("u_tvalid", "u_tready"),
    "pkt_gate>feed": ("g_tvalid", "g_tready"),
    "feed_in": ("fe_tvalid", "fe_tready"),
    "feed>mux": ("r_valid", "r_ready"),
    "mux>book": ("b_s_valid", "b_s_ready"),
    "book>mux": ("b_m_valid", "b_m_ready"),
    "mux>builder": ("ob_valid", "ob_ready"),
    "builder>udp_tx": ("p_tvalid", "p_tready"),
    "udp_tx>eth_tx": ("t_tvalid", "t_tready"),
}
INTERFACES_64 = {"packer>feed": ("n_tvalid", "n_tready")}  # DATA_W 64 only


@CoverPoint("itch.type", xf=lambda t, s: t, bins=BOOK_TYPES)
@CoverPoint("itch.side", xf=lambda t, s: s, bins=SIDES)
@CoverCross("itch.type_x_side", items=["itch.type", "itch.side"])
def _type_side(t: str, s: str) -> None:
    pass


@CoverPoint("table.fill", xf=lambda f: f, bins=FILL_BINS)
def _fill(f: int | str) -> None:
    pass


@CoverPoint("levels.side", xf=lambda s, r: s, bins=SIDES)
@CoverPoint("levels.search", xf=lambda s, r: r, bins=SEARCH)
@CoverCross("levels.side_x_search", items=["levels.side", "levels.search"])
def _search(s: str, r: str) -> None:
    pass


@CoverPoint("mold.path", xf=lambda p: p, bins=MOLD)
def _mold(p: str) -> None:
    pass


@CoverPoint("feed.type", xf=lambda t, lane: t, bins=LANE_TYPES)
@CoverPoint("feed.lane", xf=lambda t, lane: lane, bins=list(range(8)))
@CoverCross("feed.type_x_lane", items=["feed.type", "feed.lane"])
def _lane(t: str, lane: int) -> None:
    pass


@CoverPoint("eth.mode", xf=lambda m, f: m, bins=["sf", "ct"])
@CoverPoint("eth.frame", xf=lambda m, f: f, bins=["good", "bad_fcs"])
@CoverCross("eth.mode_x_fcs", items=["eth.mode", "eth.frame"])
def _fcs(m: str, f: str) -> None:
    pass


@CoverPoint("fifo.mii", xf=lambda b: b, bins=MII_LEVEL)
def _mii(b: str) -> None:
    pass


@CoverPoint("fifo.record", xf=lambda b: b, bins=RECORD_FIFO)
def _record(b: str) -> None:
    pass


@CoverPoint("bp.stalled", xf=lambda i: i, bins=[*INTERFACES, *INTERFACES_64])
def _stalled(i: str) -> None:
    pass


class _Book(BoundedBook):
    """BoundedBook that reports bucket fill at each insert and each bitmap search."""

    def _insert(self, ref: int, side: str, price: int, shares: int) -> None:
        if ref not in self.orders and not ref >> REF_BITS and not shares >> SHARES_BITS:
            bits = self.sets_per_bank.bit_length() - 1
            f = min(self._fill[b][bucket(b, ref, bits)] for b in (0, 1))
            _fill(f if f < self.ways else "full")
        super()._insert(ref, side, price, shares)

    def _remove(self, ref: int) -> None:
        o = self.orders.get(ref)
        best = self._best(o.side) if o is not None else None
        super()._remove(ref)
        if o is None or best != o.price or not self._has_level(o.price):
            return
        if o.price in (self.bids if o.side == "B" else self.asks):
            return  # the best level still holds shares: no search
        nxt = self._best(o.side)
        if nxt is None:
            _search(o.side, "side_empty")
            return
        lo = (2 * self.half_window).bit_length() // 2  # book_levels LO_BITS = WIN_BITS / 2
        low = self.base_ticks - self.half_window
        g0, g1 = ((p // TICK - low) >> lo for p in (o.price, nxt))
        _search(o.side, "same_group" if g0 == g1 else "next_group" if abs(g0 - g1) == 1
                else "far_group")  # fmt: skip

    def _best(self, side: str) -> int | None:
        levels = self.bids if side == "B" else self.asks
        if not levels:
            return None
        return max(levels) if side == "B" else min(levels)


def sample_book(recs: Iterable[tuple[int, Message]], base_ticks: int, half_window: int,
                sets_per_bank: int, ways: int) -> None:  # fmt: skip
    """Records (ITCH type byte, message) as driven into a book, in order."""
    book = _Book(base_ticks=base_ticks, half_window=half_window, sets_per_bank=sets_per_bank,
                 ways=ways)  # fmt: skip
    for kind, m in recs:
        if isinstance(m, Add):
            side = m.side
        else:
            o = book.orders.get(m.ref)
            side = o.side if o is not None else None
        if side is not None and chr(kind) in BOOK_TYPES:
            _type_side(chr(kind), side)
        book.apply(m)


def sample_feed(pkts: Iterable[bytes], data_w: int) -> None:
    """MoldUDP64 payloads as driven: each packet's sequencing path, and each message's
    type and start lane on the feed bus (payload offset mod DATA_W/8)."""
    tracker = SeqTracker()
    lanes = data_w // 8
    for p in pkts:
        c0 = (tracker.c.gaps, tracker.c.duplicates, tracker.c.session_changes,
              tracker.c.malformed_packets)  # fmt: skip
        got = tracker.feed(p)
        c1 = (tracker.c.gaps, tracker.c.duplicates, tracker.c.session_changes,
              tracker.c.malformed_packets)  # fmt: skip
        if len(p) < HEADER_LEN:
            _mold("malformed_packet")
            continue
        _, _, cnt = HEADER.unpack(p[:HEADER_LEN])
        names = ("gap", "duplicate", "session_change", "malformed_packet")
        hits = [n for n, a, b in zip(names, c0, c1, strict=True) if b > a]
        if cnt == COUNT_END:
            hits.append("end_of_session")
        elif cnt == 0:
            hits.append("heartbeat")
        elif got and not hits:
            hits.append("in_order")
        for h in hits:
            _mold(h)
        off = HEADER_LEN
        for _ in range(cnt if cnt != COUNT_END else 0):
            if off + LEN_PREFIX > len(p):
                break
            n = int.from_bytes(p[off : off + LEN_PREFIX])
            start = off + LEN_PREFIX
            if n and start < len(p):
                t = chr(p[start])
                _lane(t if t in LANE_TYPES else "skipped", start % lanes)
            off = start + n


def sample_fcs(cut_through: bool, bad: bool) -> None:
    _fcs("ct" if cut_through else "sf", "bad_fcs" if bad else "good")


async def watch_dut(dut, data_w: int, period: int = 16) -> None:
    """ob_core: MII FIFO level and record FIFO state every `period` cycles (a full or
    near-full FIFO stays so for many cycles), MII overflow pulses, and every interface
    that stalls (valid && !ready), on signal edges."""
    depth = 2 ** (len(dut.fifo_level_unused) - 1)
    ifs = INTERFACES | (INTERFACES_64 if data_w == 64 else {})
    for name, (v, r) in ifs.items():
        cocotb.start_soon(_watch_stall(name, getattr(dut, v), getattr(dut, r)))
    cocotb.start_soon(_watch_ovf(dut))
    rec = dut.u_builder.u_rec_fifo
    while True:
        await ClockCycles(dut.clk_i, period)
        await ReadOnly()
        lvl = int(dut.fifo_level_unused.value)
        _mii("empty" if lvl == 0 else "low" if lvl < depth // 4 else "mid" if lvl < depth // 2
             else "high" if lvl < 3 * depth // 4 else "near_full")  # fmt: skip
        full, empty = int(rec.full_o.value), int(rec.empty_o.value)
        _record("full" if full else "empty" if empty else "partial")


async def _watch_ovf(dut) -> None:
    while True:
        await Edge(dut.e_ovf)
        await ReadOnly()
        if int(dut.e_ovf.value):
            _mii("overflow")


async def _watch_stall(name: str, valid, ready) -> None:
    seen = False
    while not seen:
        await First(Edge(valid), Edge(ready))
        await ReadOnly()
        if int(valid.value) and not int(ready.value):
            _stalled(name)
            seen = True  # one hit closes the bin; stop paying for wake-ups
