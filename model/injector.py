"""Golden model for the on-chip injector and its meter (ADR 0014).

The injector's ROM image at DATA_W 8: two header words holding the payload byte count
(big-endian), then every MoldUDP64 payload byte, one 9-bit word each: bit 8 = last byte of
its payload. At DATA_W 64 (ADR 0015) the same 9-bit lanes, 8 per 72-bit word, each payload
starting a word (rom_words).
The meter folds each BBO record (times zeroed, docs/protocol.md layout) into a 64-bit
digest: digest = rotl(digest, 1) ^ (XOR of the record's eight big-endian 64-bit words).
Pure functions, no I/O.
"""

from __future__ import annotations

import dataclasses
from collections.abc import Iterable, Sequence
from dataclasses import dataclass

from model.bounded import BoundedBook
from model.moldudp64 import FeedDecoder, packetize
from model.protocol import Config, expected, without_times

DEPTH = 32768  # ROM bytes: 9-bit words at DATA_W 8 (8 BRAM36 at 4 K x 9 or 512 x 72)
HEADER_WORDS = 2
LAST = 1 << 8
LANE_BITS = 9  # {last, byte}
MASK64 = (1 << 64) - 1


def digest(records: Iterable[bytes]) -> int:
    d = 0
    for rec in records:
        if len(rec) != 64:
            raise ValueError(f"BBO record of {len(rec)} bytes")
        rec = without_times(rec)
        fold = 0
        for i in range(0, 64, 8):
            fold ^= int.from_bytes(rec[i : i + 8])
        d = ((d << 1) | (d >> 63)) & MASK64
        d ^= fold
    return d


def _group(lane_words: list[int], lanes: int) -> list[int]:
    """Pad to whole ROM words with 0 lanes; lane i of a word in bits 9i+8..9i."""
    lane_words = lane_words + [0] * (-len(lane_words) % lanes)
    return [
        sum(w << (LANE_BITS * i) for i, w in enumerate(lane_words[k : k + lanes]))
        for k in range(0, len(lane_words), lanes)
    ]


def rom_words(pkts: Sequence[bytes], depth: int | None = None, lanes: int = 1) -> list[int]:
    """The ROM image for an injector of `lanes` bytes per beat (1: DATA_W 8, 8: DATA_W 64;
    ADR 0015). Each word holds `lanes` 9-bit lanes {last, byte}. Every payload starts at
    lane 0 of a new word; lanes after its last byte are 0. The header lanes hold the data
    word count, big-endian (2 words at 1 lane, 1 word at 8). `depth` defaults to the
    ROM of DEPTH bytes: DEPTH // lanes words."""
    if lanes not in (1, 8):
        raise ValueError(f"{lanes} lanes")
    depth = DEPTH // lanes if depth is None else depth
    if any(not p for p in pkts):
        raise ValueError("empty payload")
    data: list[int] = []
    for p in pkts:
        data += _group([*p[:-1], LAST | p[-1]], lanes)
    n = len(data)
    head = _group([n >> 8, n & 0xFF], lanes)
    if len(head) + n > depth or n >= 1 << 16:
        raise ValueError(f"{n} data words do not fit {depth} words")
    return head + data


def bbo_seqs(msgs: Sequence[bytes], session: bytes, cfg: Config, book: BoundedBook) -> list[int]:
    """MoldUDP64 sequence numbers (1 = msgs[0]) of the messages that publish a BBO."""
    dec = FeedDecoder(cfg.symbol, locate=cfg.locate if cfg.locate_valid else None)
    out = []
    for p in packetize(msgs, session):
        for rec in dec.feed(p):
            if book.apply(rec.msg) is not None:
                out.append(rec.seq)
    return out


@dataclass(frozen=True, slots=True)
class Image:
    pkts: list[bytes]
    messages: int  # ITCH messages in the image (all types)
    words: list[int]


def build_image(
    msgs: Sequence[bytes], session: bytes, cfg: Config, book: BoundedBook, depth: int = DEPTH
) -> Image:
    """The longest prefix of msgs that fits the ROM and ends on a BBO-publishing message,
    so the meter's span (first byte to last BBO) covers every message's processing.
    `book` is a fresh book of the RTL's geometry; it is used up."""
    n = 0
    budget = depth - HEADER_WORDS
    for k in range(1, len(msgs) + 1):  # packetize is greedy: sizes grow with the prefix
        if sum(map(len, packetize(msgs[:k], session))) > budget:
            break
        n = k
    seqs = [s for s in bbo_seqs(msgs[:n], session, cfg, book) if s <= n]
    if not seqs:
        raise ValueError("no message in the prefix publishes a BBO")
    n = seqs[-1]
    pkts = list(packetize(msgs[:n], session))
    return Image(pkts, n, rom_words(pkts, depth))


@dataclass(frozen=True, slots=True)
class Expected:
    bbos: int
    digest: int
    records: list[bytes]


def expected_run(img: Image, cfg: Config, book: BoundedBook, cut_through: bool) -> Expected:
    """What the meter must report after mode 2 (inject) replays img once."""
    if cfg.mode != 2:
        raise ValueError("inject runs use config mode 2")
    bbo_cfg = dataclasses.replace(cfg, mode=0)
    recs, _ = expected(img.pkts, bbo_cfg, book, cut_through=cut_through)
    return Expected(len(recs), digest(recs), recs)
