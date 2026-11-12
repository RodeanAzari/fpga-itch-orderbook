"""Golden encoder/decoder for the FPGA <-> host wire protocol (docs/protocol.md, ADR 0007).

Pure functions, no I/O. Datagram boundaries depend on RTL timing (flush on idle), so the
expected output is a record stream; `parse` splits received datagrams back into records.
"""

from __future__ import annotations

import struct
from collections.abc import Iterable, Sequence
from dataclasses import dataclass

from model.book import Bbo
from model.bounded import BoundedBook
from model.itch import Add, Delete, Reduce, Replace
from model.moldudp64 import FeedDecoder, Record

MAGIC = 0x4F42
VERSION = 1
HEADER = struct.Struct(">HBBIHH")  # magic, version, type, pkt_seq, count, record_len
MAX_PAYLOAD = 1472

BBO, TRACE, STATS, CONFIG, CONFIG_ACK = 0x01, 0x02, 0x03, 0x10, 0x11
RECORD_LEN = {BBO: 64, TRACE: 40, STATS: 8, CONFIG: 24, CONFIG_ACK: 24}
MAX_COUNT = {t: (MAX_PAYLOAD - HEADER.size) // n for t, n in RECORD_LEN.items()}

# BBO flag bits
STALE, CROSSED, LOCKED, OUT_OF_WINDOW, CUT_THROUGH = (1 << i for i in range(5))

STATS_NAMES = (
    "uptime_cycles",
    "rx_frames_ok",
    "rx_crc_err",
    "rx_err",
    "rx_runt",
    "rx_oversize",
    "rx_fifo_overflow",
    "feed_datagrams",
    "feed_filtered",
    "ip_csum_err",
    "udp_malformed",
    "mold_packets",
    "mold_heartbeats",
    "mold_end_of_session",
    "mold_gaps",
    "mold_gap_messages",
    "mold_duplicates",
    "mold_session_changes",
    "mold_malformed_packets",
    "itch_unknown_types",
    "itch_malformed_messages",
    "itch_other_locate",
    "itch_directory_matches",
    "itch_locate_changes",
    "locate",
    "book_records",
    "ref_overflow",
    "duplicate_ref",
    "shares_overflow",
    "table_overflow",
    "unknown_ref",
    "over_reduce",
    "price_oow",
    "price_subpenny",
    "bbo_oow",
    "stale",
    "out_records",
    "out_datagrams",
    "ctrl_ok",
    "ctrl_bad",
    "t0_lost",
    "cut_through_bad",
    "inj_cycles",
    "inj_bbos",
    "inj_digest",
)
N_STATS = len(STATS_NAMES)

_BBO = struct.Struct(">HHII6s6s6sHQQQQ")
_TRACE = struct.Struct(">cB6sQQQII")
_CONFIG = struct.Struct(">IHBB8sIHH")


def packet(kind: int, pkt_seq: int, records: Sequence[bytes]) -> bytes:
    """One datagram payload: header + records, each exactly RECORD_LEN[kind] bytes."""
    n = RECORD_LEN[kind]
    if not 1 <= len(records) <= MAX_COUNT[kind] or any(len(r) != n for r in records):
        raise ValueError(f"type {kind:#x}: bad record count or length")
    return HEADER.pack(MAGIC, VERSION, kind, pkt_seq, len(records), n) + b"".join(records)


@dataclass(frozen=True, slots=True)
class Datagram:
    kind: int
    pkt_seq: int
    records: tuple[bytes, ...]


def parse(payload: bytes) -> Datagram:
    """Inverse of `packet`; raises ValueError on anything the protocol does not allow."""
    if len(payload) < HEADER.size:
        raise ValueError("shorter than the header")
    magic, version, kind, seq, count, n = HEADER.unpack_from(payload)
    if magic != MAGIC or version != VERSION or RECORD_LEN.get(kind) != n:
        raise ValueError(f"bad header {magic:#x} v{version} type {kind:#x} len {n}")
    if not 1 <= count <= MAX_COUNT[kind] or len(payload) != HEADER.size + count * n:
        raise ValueError(f"type {kind:#x}: count {count} vs {len(payload)} bytes")
    body = payload[HEADER.size :]
    return Datagram(kind, seq, tuple(body[i * n : (i + 1) * n] for i in range(count)))


TIMES = slice(40, 64)  # BBO record t0, t1, t2: measurements, not model output (ADR 0011)


def without_times(rec: bytes) -> bytes:
    """A BBO record with t0/t1/t2 zeroed, for comparison with the model's records."""
    return rec[: TIMES.start] + bytes(TIMES.stop - TIMES.start)


def bbo_record(
    bbo: Bbo,
    mold_seq: int,
    stale: bool,
    t: tuple[int, int, int] = (0, 0, 0),
    cut_through: bool = False,
) -> bytes:
    flags = (
        STALE * stale
        | CROSSED * bbo.crossed
        | LOCKED * bbo.locked
        | OUT_OF_WINDOW * bbo.out_of_window
        | CUT_THROUGH * cut_through
    )
    return _BBO.pack(
        0,
        flags,
        bbo.bid_price,
        bbo.ask_price,
        bbo.bid_size.to_bytes(6),
        bbo.ask_size.to_bytes(6),
        bbo.ts.to_bytes(6),
        0,
        mold_seq,
        *t,
    )


def trace_record(rec: Record) -> bytes:
    m = rec.msg
    assert isinstance(m, Add | Reduce | Delete | Replace)
    sell, ref, new_ref, shares, price = 0, m.ref, 0, 0, 0
    if isinstance(m, Add):
        sell, shares, price = int(m.side == "S"), m.shares, m.price
    elif isinstance(m, Reduce):
        shares = m.shares
    elif isinstance(m, Replace):
        new_ref, shares, price = m.new_ref, m.shares, m.price
    return _TRACE.pack(
        bytes([rec.kind]), sell, m.ts.to_bytes(6), rec.seq, ref, new_ref, shares, price
    )


def stats_records(values: Sequence[int]) -> list[bytes]:
    if len(values) != N_STATS:
        raise ValueError(f"{len(values)} stats, want {N_STATS}")
    return [v.to_bytes(8) for v in values]


@dataclass(frozen=True, slots=True)
class Config:
    feed_group: int = 0xEF010101  # 239.1.1.1
    feed_port: int = 26400
    mode: int = 0  # 0 = BBO, 1 = trace, 2 = inject (ADR 0014)
    locate_valid: bool = False
    symbol: str = "AAPL"
    lo_price: int = 0
    locate: int = 0

    def record(self) -> bytes:
        return _CONFIG.pack(
            self.feed_group,
            self.feed_port,
            self.mode,
            int(self.locate_valid),
            self.symbol.ljust(8).encode(),
            self.lo_price,
            self.locate,
            0,
        )


def parse_config(payload: bytes) -> Config | None:
    """What the RTL accepts: a well-formed type 0x10 packet, mode 0-2, locate_valid 0/1.
    Returns None (counted in ctrl_bad, no ack) for anything else."""
    try:
        d = parse(payload)
    except ValueError:
        return None
    if d.kind != CONFIG or len(d.records) != 1:
        return None
    group, port, mode, lv, sym, lo, loc, _ = _CONFIG.unpack(d.records[0])
    if mode > 2 or lv > 1:
        return None
    return Config(group, port, mode, bool(lv), sym.decode("latin-1").rstrip(" "), lo, loc)


def window_lo_price(base_ticks: int, half_window: int = 2048) -> int:
    """Config.lo_price for a window centred on base_ticks (ADR 0002)."""
    return (base_ticks - half_window) * 100


def expected(
    pkts: Iterable[bytes], cfg: Config, book: BoundedBook, cut_through: bool = False
) -> tuple[list[bytes], FeedDecoder]:
    """The record stream the FPGA must send for these feed payloads (BBO or trace mode).

    The BBO stale flag is the cause record's feed stale (it rides with the record) OR the
    book's stale state after applying it. `book` is the RTL's geometry; it is updated.
    `cut_through`: the bitstream's build mode, carried in every BBO's flags (ADR 0013).
    """
    dec = FeedDecoder(cfg.symbol, locate=cfg.locate if cfg.locate_valid else None)
    out: list[bytes] = []
    for p in pkts:
        for rec in dec.feed(p):
            if cfg.mode == 1:
                out.append(trace_record(rec))
            elif (bbo := book.apply(rec.msg)) is not None:
                out.append(
                    bbo_record(bbo, rec.seq, rec.stale or book.stale, cut_through=cut_through)
                )
    return out, dec
