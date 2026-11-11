"""cocotb end-to-end test for rtl/top/ob_core: Ethernet frames in, protocol datagrams out.

Why this test matters: it is the simulation twin of the Phase D hardware run. Each block
has its own test; this one proves they are wired together as docs/protocol.md says: the
config reaches the parser and resets the right blocks, the feed goes only to the decoder
and only after the ack, every record leaves in a datagram addressed to the config's
sender, and the 41 stats counters come from the right sources. Frames go in on the MII
pins (eth-core's tagged tb helpers) and come out on the MII TX pins, where FCS, IPv4
checksum, addresses and ports are checked independently.

Oracle: model.protocol.expected() for the record stream (BBO mode, then trace mode with a
configured locate after a second config), model counters for the stats. Feed packets are
lost, duplicated and mixed with heartbeats; foreign frames, bad FCS and bad control
packets are mixed in. Feed frames sent before the first config must produce nothing.
"""

from __future__ import annotations

import os
import struct
import sys
import zlib
from pathlib import Path

import cocotb
from cocotb.clock import Clock
from cocotb.triggers import ClockCycles, Edge, FallingEdge, ReadOnly, RisingEdge, Timer
from cocotb.utils import get_sim_time
from tb_common import get_rng

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "rtl" / "eth" / "tb"))
from eth import MiiDriver, MiiFrame, MiiTxMonitor, UdpFrame, ip_checksum, with_fcs  # noqa: E402

from model import generator  # noqa: E402
from model.bounded import BoundedBook  # noqa: E402
from model.moldudp64 import FeedDecoder, heartbeat, pack, packetize  # noqa: E402
from model.protocol import (  # noqa: E402
    BBO,
    CONFIG,
    CONFIG_ACK,
    STATS,
    STATS_NAMES,
    TRACE,
    Config,
    expected,
    packet,
    parse,
    window_lo_price,
    without_times,
)

SET_BITS = int(os.environ.get("SET_BITS", "11"))
WAYS = int(os.environ.get("WAYS", "16"))
TICK_CYCLES = int(os.environ.get("TICK_CYCLES", "30000"))
OWN_MAC, OWN_IP, CTRL_PORT = bytes.fromhex("02000a0b0c0d"), 0xC0A80A02, 7000
GROUP, PORT = 0xEF010101, 26400
GOOD_RESIDUE = 0x2144DF1C  # zlib.crc32 over data + a correct FCS
BASE_TICKS = generator.DEFAULT.base_price // 100
CORE_NS = 8
# Feed payload sent right after a config: its burst out of eth_rx's store-and-forward
# buffer spans the feed's release. Measured: release ~32.7 us after the config's last
# wire byte (the book clear, 2^12 cycles of levels, dominates); a 325-byte payload's frame
# ends 31.3 us after it and its ~2.6 us burst covers the release (311-342 B work). The
# test asserts it did.
STRADDLE_BYTES = int(os.environ.get("STRADDLE_BYTES", "325"))


def parse_frame(data: bytes) -> tuple[bytes, int, int, int, bytes]:
    """(dst MAC, dst IP, src port, dst port, payload) of a frame from the TX pins, after
    checking everything a receiving NIC and IP stack would."""
    assert zlib.crc32(data) == GOOD_RESIDUE, "FCS does not check"
    body = data[:-4]
    assert body[6:12] == OWN_MAC and body[12:14] == b"\x08\x00", "src MAC / EtherType"
    ip = body[14:34]
    assert ip[0] == 0x45 and ip[9] == 17 and ip_checksum(ip) == 0, "IPv4 header"
    assert int.from_bytes(ip[12:16]) == OWN_IP, "src IP"
    end = 14 + int.from_bytes(ip[2:4])
    src_port, dst_port, ulen = struct.unpack_from(">HHH", body, 34)
    assert ulen == end - 34, "UDP length"
    return body[:6], int.from_bytes(ip[16:20]), src_port, dst_port, body[42:end]


class Host:
    """The capture side: one sender address, the datagrams received from the FPGA."""

    def __init__(self, rng) -> None:
        self.mac = bytes([0x02]) + rng.randbytes(5)
        self.ip = 0xC0A80A00 | rng.randrange(3, 250)
        self.port = rng.randrange(1024, 65536)

    def ctrl_frame(self, payload: bytes) -> bytes:
        return with_fcs(
            UdpFrame(payload, OWN_IP, CTRL_PORT, dst_mac=OWN_MAC, src_mac=self.mac,
                     src_ip=self.ip, src_port=self.port).build()
        )  # fmt: skip


class Bench:
    def __init__(self, dut) -> None:
        self.dut = dut
        self.drv = MiiDriver(dut, dut.mii_rx_clk_i, dut.mii_rxd_i, dut.mii_rx_dv_i,
                             dut.mii_rx_er_i)  # fmt: skip
        self.mon = MiiTxMonitor(dut.mii_tx_clk_i, dut.mii_txd_o, dut.mii_tx_en_o)
        self.seen = 0  # TX frames already returned by datagrams()
        self.feed_sent: list[bytes] = []  # every feed-port payload sent, in order
        self.frames_sent = 0
        self.feed_frame: list[int] = []  # index of each feed payload's frame
        self.sfd_now: list[int] = []  # now_q when each frame's sfd_o rose (one per frame)
        self.t2_seen: list[int] = []  # ob_core's first-valid captures, one per BBO
        self.t2_mark = 0
        self.straddles = 0  # payloads the feed's release fell inside

    async def start(self) -> None:
        dut = self.dut
        cocotb.start_soon(Clock(dut.clk_i, CORE_NS, unit="ns").start())
        cocotb.start_soon(Clock(dut.mii_rx_clk_i, 40, unit="ns").start())
        await Timer(13, unit="ns")  # tx_clk: same frequency, unrelated phase
        cocotb.start_soon(Clock(dut.mii_tx_clk_i, 40, unit="ns").start())
        cocotb.start_soon(self.mon.run())
        cocotb.start_soon(self.watch_sfd())
        cocotb.start_soon(self.watch_t2())
        for r in (dut.rst_i, dut.mii_rx_rst_i, dut.mii_tx_rst_i):
            r.value = 1
        await ClockCycles(dut.mii_rx_clk_i, 4)
        for r in (dut.rst_i, dut.mii_rx_rst_i, dut.mii_tx_rst_i):
            r.value = 0
        await ClockCycles(dut.clk_i, 10)

    async def watch_release(self) -> list[bool]:
        """From a config until the feed is released and no payload is in flight: per feed
        udp_rx payload, whether it passes the gate by the spec rule (feed_rst low on every
        cycle from its first beat to its last). Every later payload passes. Runs only in
        this window: a Python wake-up per core cycle doubles the simulation time."""
        dut = self.dut
        flags: list[bool] = []
        mid, blocked, first_rst, was_rst = False, False, False, False
        while True:
            await FallingEdge(dut.clk_i)
            rst = bool(int(dut.feed_rst.value))
            was_rst |= rst
            if mid:
                blocked |= rst
            if int(dut.u_tvalid.value) and int(dut.u_tready.value):
                if not mid:
                    mid, blocked, first_rst = True, rst, rst
                if int(dut.u_tlast.value):
                    flags.append(not blocked)
                    self.straddles += first_rst and not rst  # released mid-payload
                    mid = False
            if was_rst and not rst and not mid:
                self.release_ns = get_sim_time(unit="ns")
                return flags

    async def watch_sfd(self) -> None:
        """T0's reference (ADR 0011): the core counter when eth_rx's SFD pulse rises."""
        while True:
            await RisingEdge(self.dut.sfd_o)
            await ReadOnly()
            self.sfd_now.append(int(self.dut.now_q.value))

    async def watch_t2(self) -> None:
        """T2's reference: b_t2_q changes exactly when a new BBO first becomes valid (the
        counter never repeats), so each change is one BBO's first valid cycle."""
        while True:
            await Edge(self.dut.b_t2_q)
            await ReadOnly()
            self.t2_seen.append(int(self.dut.b_t2_q.value))

    def feed(self, payload: bytes, src_mac: bytes = bytes.fromhex("0200deadbeef")) -> bytes:
        """A feed-port frame; its payload is recorded for the gate reference."""
        self.feed_sent.append(payload)
        self.feed_frame.append(self.frames_sent)  # feed() is always sent next
        return with_fcs(UdpFrame(payload, GROUP, PORT, src_mac=src_mac).build())

    async def send(self, frame: bytes, slack: float = 0.0) -> None:
        """One frame; slack = extra idle as a fraction of the frame (output pacing)."""
        self.frames_sent += 1
        await self.drv.send(MiiFrame(frame, ifg=24 + int(2 * len(frame) * slack)))

    def datagrams(self, host: Host) -> list:
        """Parsed payloads of the TX frames since the last call, all addressed to host."""
        out = []
        for wf in self.mon.frames[self.seen :]:
            data, framing = wf.decode()
            assert framing, "bad preamble/nibble framing"
            mac, ip, sport, dport, payload = parse_frame(data)
            assert (mac, ip, sport, dport) == (host.mac, host.ip, CTRL_PORT, host.port), (
                f"datagram sent to {mac.hex()} {ip:#x}:{dport} from :{sport}"
            )
            out.append(parse(payload))
        self.seen = len(self.mon.frames)
        return out

    async def wait_frames(self, n: int, limit_us: int) -> None:
        for _ in range(limit_us):
            if len(self.mon.frames) >= n:
                return
            await Timer(1, unit="us")
        raise AssertionError(f"{len(self.mon.frames)} TX frames after {limit_us} us, want {n}")


def feed_packets(rng, n_msgs: int, session: bytes, gen: generator.Config) -> list[bytes]:
    """Generator messages (ITCH-adversarial) with three damaged ones (one byte long or
    short, empty) in the middle, packetized, with duplicates and heartbeats throughout and
    losses only in the last quarter. With gen.p_unknown_ref = 0 the book stays clean until
    the first loss, so BBOs between the damage and that loss are stale through the feed
    alone: the record's feed stale must reach its BBO."""
    msgs = generator.generate(seed=rng.randrange(1 << 30), count=n_msgs, cfg=gen)
    for how in ("long", "short", "empty"):
        k = rng.randrange(n_msgs * 2 // 5, n_msgs // 2)
        msgs[k] = {"long": msgs[k] + b"\x00", "short": msgs[k][:-1], "empty": b""}[how]
    out: list[bytes] = []
    pkts = list(packetize(msgs, session, max_payload=rng.choice([200, 600, 1472])))
    for i, p in enumerate(pkts):
        seq, count = struct.unpack_from(">QH", p, 10)
        r = rng.random()
        if r < 0.03 and i >= len(pkts) * 3 // 4:
            continue  # lost
        out += [p, p] if r < 0.06 else [p]
        if rng.random() < 0.05:
            out.append(heartbeat(session, seq + count))
    return out


def straddle_packet(pkts: list[bytes], size: int) -> bytes:
    """A genuine first packet of the session, exactly `size` bytes: the first messages of
    pkts' packets, repacked (seq 1), then one filler message of an unknown type ('Z',
    skipped by its length) to hit the size. Passed whole it is an early copy (the model
    sees the overlap); a gate that opened mid-packet would feed its tail to the decoder."""
    session = pkts[0][:10]
    msgs: list[bytes] = []
    used = 20  # MoldUDP64 header
    for p in pkts:
        _, count = struct.unpack_from(">QH", p, 10)
        off = 20
        for _ in range(count):
            (n,) = struct.unpack_from(">H", p, off)
            if used + 2 + n > size - 3:  # room for the filler (2-byte length + >= 1)
                msgs.append(b"Z" + bytes(size - used - 3))
                out = pack(session, 1, msgs)
                assert len(out) == size
                return out
            msgs.append(p[off + 2 : off + 2 + n])
            used += 2 + n
            off += 2 + n
    raise ValueError("not enough messages for the straddle packet")


def book_records(cfg: Config, pkts: list[bytes]) -> tuple[int, FeedDecoder]:
    dec = FeedDecoder(cfg.symbol, locate=cfg.locate if cfg.locate_valid else None)
    return sum(len(dec.feed(p)) for p in pkts), dec


def check_times(b: Bench, cfg: Config, pkts: list[bytes], frames: list[int],
                got: list[bytes]) -> None:  # fmt: skip
    """ADR 0011: every BBO's T0 is its cause frame's SFD plus the synchronizer's constant
    delay (at most one cycle of jitter), and T0 < T1 < T2 with T2 rising."""
    dec = FeedDecoder(cfg.symbol, locate=cfg.locate if cfg.locate_valid else None)
    frame_of: dict[int, int] = {}  # cause message seq -> the frame that delivered it
    kind_of: dict[int, str] = {}
    for p, f in zip(pkts, frames, strict=True):
        for rec in dec.feed(p):
            frame_of.setdefault(rec.seq, f)
            kind_of.setdefault(rec.seq, type(rec.msg).__name__)
    assert len(b.sfd_now) == b.frames_sent, (len(b.sfd_now), b.frames_sent)
    delays: dict[int, int] = {}
    last_t2 = 0
    for i, g in enumerate(got):
        seq, t0, t1, t2 = struct.unpack(">QQQQ", g[32:64])
        d = t0 - b.sfd_now[frame_of[seq]]
        delays[d] = delays.get(d, 0) + 1
        assert t0 < t1 < t2 and t2 > last_t2, f"BBO {i} (seq {seq}): t0 {t0} t1 {t1} t2 {t2}"
        last_t2 = t2
    lat: dict[int, dict[str, int]] = {}  # T2 - T1 -> message type -> count
    for g in got:
        seq, t1, t2 = struct.unpack(">Q8xQQ", g[32:64])
        by = lat.setdefault(t2 - t1, {})
        by[kind_of[seq]] = by.get(kind_of[seq], 0) + 1
    t2s = [struct.unpack(">Q", g[56:64])[0] for g in got]
    assert t2s == b.t2_seen[b.t2_mark :], "T2 is not each BBO's first valid cycle"
    cocotb.log.info("T0 - SFD (cycles: count) %s; T2 - T1 (cycles: type: count) %s",
                    dict(sorted(delays.items())), dict(sorted(lat.items())))  # fmt: skip
    assert max(delays) - min(delays) <= 1 and 0 < min(delays) < 12, delays


async def run_phase(b: Bench, rng, host: Host, cfg: Config, n_msgs: int, session: bytes,
                    ctrl_counts: list[int], slack: float,
                    gen: generator.Config = generator.DEFAULT) -> None:  # fmt: skip
    """Config -> ack -> feed with noise -> records and stats checked against the model.
    slack: idle after each feed frame, as a fraction of it (0 = line rate)."""
    # a malformed control packet first: counted, ignored
    bad = bytearray(packet(CONFIG, 0, [cfg.record()]))
    bad[3] = 0x11
    await b.send(host.ctrl_frame(bytes(bad)))
    ctrl_counts[1] += 1
    pkts = feed_packets(rng, n_msgs, session, gen)
    first = len(b.feed_sent)
    b.t2_mark = len(b.t2_seen)
    release = cocotb.start_soon(b.watch_release())
    await b.send(host.ctrl_frame(packet(CONFIG, rng.getrandbits(32), [cfg.record()])))
    ctrl_counts[0] += 1
    # timed to arrive while the book clears, so the feed's release falls inside it
    sp = straddle_packet(pkts, STRADDLE_BYTES)
    t_cfg = get_sim_time(unit="ns")
    await b.send(b.feed(sp))
    t_sp = get_sim_time(unit="ns")
    # a stats datagram the builder had started before the config may still go to the
    # previous host; the next datagram must be the ack, to this host
    for _ in range(2_000):
        while b.seen < len(b.mon.frames):
            data, _ = b.mon.frames[b.seen].decode()
            if parse_frame(data)[0] == host.mac:
                break
            assert parse(parse_frame(data)[4]).kind == STATS, "old-run datagram after config"
            b.seen += 1
        else:
            await Timer(1, unit="us")
            continue
        break
    ack = b.datagrams(host)[0]
    assert ack.kind == CONFIG_ACK and ack.pkt_seq == 0 and ack.records == (cfg.record(),)
    assert b.seen == len(b.mon.frames) and ack is not None

    # feed + noise; counts of what the frames must produce
    want = dict.fromkeys(STATS_NAMES, 0)
    want["rx_frames_ok"] = want["feed_datagrams"] = 1  # the straddle frame, after the clear
    for k, p in enumerate(pkts):
        await b.send(b.feed(p, host.mac), slack)
        want["rx_frames_ok"] += 1
        want["feed_datagrams"] += 1
        if k % 9 == 4:  # same group, other port: filtered
            await b.send(with_fcs(UdpFrame(p[:40], GROUP, PORT + 1).build()))
            want["rx_frames_ok"] += 1
            want["feed_filtered"] += 1
        if k % 13 == 6:  # bad FCS on a feed frame: never reaches udp_rx
            f = bytearray(with_fcs(UdpFrame(p, GROUP, PORT).build()))
            f[rng.randrange(len(f))] ^= 1 << rng.randrange(8)
            await b.send(bytes(f))
            want["rx_crc_err"] += 1
        if k % 17 == 8:  # control garbage: rejected, filtered by the feed udp_rx
            await b.send(host.ctrl_frame(rng.randbytes(rng.randint(1, 50))))
            want["rx_frames_ok"] += 1
            want["feed_filtered"] += 1
            ctrl_counts[1] += 1

    # expected records and counters: the feed payloads that passed the gate
    flags = await release
    cocotb.log.info(
        "release window: flags %s, straddles %d; straddle %d B on the wire %.1f-%.1f us "
        "after the config's end, window closed at %.1f us",
        flags, b.straddles, len(sp), 0.0, (t_sp - t_cfg) / 1000, (b.release_ns - t_cfg) / 1000,
    )  # fmt: skip
    sent = b.feed_sent[first:]
    assert len(flags) <= len(sent), f"{len(flags)} payloads at the gate, {len(sent)} sent"
    flags += [True] * (len(sent) - len(flags))
    pkts = [p for p, ok in zip(sent, flags, strict=True) if ok]
    frames = [f for f, ok in zip(b.feed_frame[first:], flags, strict=True) if ok]
    book = BoundedBook(
        base_ticks=BASE_TICKS, half_window=2048, sets_per_bank=1 << SET_BITS, ways=WAYS
    )
    recs, _ = expected(pkts, cfg, book)
    n_book, dec = book_records(cfg, pkts)
    kind = TRACE if cfg.mode else BBO
    got: list[bytes] = []
    stats: list = []
    last_seq = 0
    rec_done_seq = None  # pkt_seq of the datagram that completed the records
    timed_out = False
    for _ in range(150):  # 12 ms: all frames are already sent, so this is generous
        await ClockCycles(b.dut.clk_i, 10_000)
        for d in b.datagrams(host):
            assert d.pkt_seq == last_seq + 1, f"pkt_seq {d.pkt_seq} after {last_seq}"
            last_seq = d.pkt_seq
            if d.kind == STATS:
                stats.append(d)
            else:
                assert d.kind == kind, f"datagram type {d.kind:#x} in mode {cfg.mode}"
                got += d.records
                if len(got) >= len(recs) and rec_done_seq is None:
                    rec_done_seq = d.pkt_seq
        # done: all records out and two stats datagrams after them (all frames were sent
        # before this loop, so the second is a full tick after the last one arrived)
        if rec_done_seq is not None and sum(s.pkt_seq > rec_done_seq for s in stats) >= 2:
            break
    else:
        timed_out = True
    c, t = dec.c, dec.tracker.c
    want |= {
        "mold_packets": t.packets, "mold_heartbeats": t.heartbeats,
        "mold_end_of_session": t.end_of_session, "mold_gaps": t.gaps,
        "mold_gap_messages": t.gap_messages, "mold_duplicates": t.duplicates,
        "mold_session_changes": t.session_changes, "mold_malformed_packets": t.malformed_packets,
        "itch_unknown_types": c.unknown_types, "itch_malformed_messages": c.malformed_messages,
        "itch_other_locate": c.other_locate, "itch_directory_matches": c.directory_matches,
        "itch_locate_changes": c.locate_changes,
        "locate": (1 << 16 | dec.locate) if dec.locate is not None else 0,
        "book_records": n_book, "out_records": len(recs),
        "out_datagrams": stats[-1].pkt_seq,
        "stale": int(dec.stale) | (0 if cfg.mode else int(book.stale)) << 1,
        "ctrl_ok": ctrl_counts[0], "ctrl_bad": ctrl_counts[1],
    }  # fmt: skip
    if not cfg.mode:
        want |= {
            "ref_overflow": book.ref_overflow, "duplicate_ref": book.counters.duplicate_ref,
            "shares_overflow": book.shares_overflow, "table_overflow": book.table_overflow,
            "unknown_ref": book.counters.unknown_ref, "over_reduce": book.counters.over_reduce,
            "price_oow": book.price_oow, "price_subpenny": book.price_subpenny,
            "bbo_oow": book.bbo_oow,
        }  # fmt: skip
    vals = {n: int.from_bytes(r) for n, r in zip(STATS_NAMES, stats[-1].records, strict=True)}
    up = [int.from_bytes(s.records[0]) for s in stats]
    # snapshot when the builder starts the datagram: at or after each tick
    assert all(u >= (k + 1) * TICK_CYCLES for k, u in enumerate(up)), f"uptime {up}"
    assert up == sorted(up), f"uptime {up}"
    del vals["uptime_cycles"], want["uptime_cycles"]
    bad_vals = {n: (vals[n], want[n]) for n in want if vals[n] != want[n]}
    assert not bad_vals, f"stats (got, want): {bad_vals}"
    assert not timed_out, f"timeout: {len(got)} of {len(recs)} records"
    if kind == BBO:
        check_times(b, cfg, pkts, frames, got)
        got = [without_times(g) for g in got]
    if got != recs:
        diff = [i for i, (g, w) in enumerate(zip(got, recs, strict=False)) if g != w]
        k = diff[0] if diff else min(len(got), len(recs))
        g = got[k].hex() if k < len(got) else None
        w = recs[k].hex() if k < len(recs) else None
        raise AssertionError(f"{len(got)} records, want {len(recs)}; first diff at {k}: {g} vs {w}")

    cocotb.log.info(
        "mode %d: %d feed packets, %d records in %d datagrams, %d stats; stale %d; %s",
        cfg.mode, len(pkts), len(recs), last_seq, len(stats), want["stale"],
        {n: vals[n] for n in ("mold_gaps", "mold_duplicates", "unknown_ref", "price_oow")},
    )  # fmt: skip


@cocotb.test()
async def frames_to_datagrams(dut):
    """Feed before any config is ignored; a BBO run; then a trace run after a reconfig
    from another host (pkt_seq restarts, counters cleared except ctrl_*)."""
    rng = get_rng("frames_to_datagrams")
    b = Bench(dut)
    await b.start()
    early = feed_packets(rng, 200, b"EARLYSESS0", generator.DEFAULT)
    for p in early[:3]:  # filtered: the feed udp_rx has no group before a config
        await b.send(with_fcs(UdpFrame(p, GROUP, PORT).build()))
    await ClockCycles(dut.clk_i, 20_000)
    assert not b.mon.frames, "datagrams sent before the first config"

    ctrl_counts = [0, 0]
    lo = window_lo_price(BASE_TICKS)
    h1, h2 = Host(rng), Host(rng)
    cfg1 = Config(GROUP, PORT, 0, False, generator.DEFAULT.stock, lo, 0)
    # BBO mode at line rate: the output (BBO changes only) is far below the input
    clean = generator.Config(p_unknown_ref=0.0)  # book stale only after the first loss
    await run_phase(b, rng, h1, cfg1, 1000, b"SESSIONAAA", ctrl_counts, 0.0, clean)
    cfg2 = Config(GROUP, PORT, 1, True, generator.DEFAULT.stock, lo, generator.DEFAULT.locate)
    # trace mode: a 40-byte record per ~38-byte message, so the feed is paced to half rate
    await run_phase(b, rng, h2, cfg2, 300, b"SESSIONBBB", ctrl_counts, 1.0)
    assert b.straddles >= 2, f"the feed release fell inside a payload {b.straddles} times"
