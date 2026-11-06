"""MoldUDP64 framing (docs/buildplan.md §5.2) and the Phase C feed front end, as golden models.

Pure: functions and small state machines, no I/O. Three pieces:
  - pack / packetize / heartbeat / end_of_session: build packets (host replayer reference,
    test stimulus).
  - SeqTracker: deframes packets and applies the gap / duplicate / session semantics of
    ADR 0003. Yields (sequence number, message bytes) for every message accepted.
  - FeedDecoder: SeqTracker + ITCH decode + stock-locate filter learned from R (ADR 0004).
    Its `records` are the stream the RTL decoder must reproduce, message for message.
"""

from __future__ import annotations

import struct
from collections.abc import Iterable, Iterator
from dataclasses import dataclass, field

from model.itch import LENGTHS, Directory, MalformedMessage, Message, decode

SESSION_LEN = 10
HEADER = struct.Struct(">10sQH")  # session, sequence of first message, message count
HEADER_LEN = HEADER.size  # 20
LEN_PREFIX = 2
COUNT_HEARTBEAT = 0
COUNT_END = 0xFFFF
MAX_UDP_PAYLOAD = 1472  # 1500 MTU - 20 IPv4 - 8 UDP
FIRST_SEQ = 1  # MoldUDP64 sequence numbers start at 1 in each session


def pack(session: bytes, seq: int, msgs: Iterable[bytes]) -> bytes:
    """One packet carrying msgs, the first having sequence number seq."""
    msgs = list(msgs)
    body = b"".join(struct.pack(">H", len(m)) + m for m in msgs)
    return HEADER.pack(session, seq, len(msgs)) + body


def heartbeat(session: bytes, next_seq: int) -> bytes:
    return HEADER.pack(session, next_seq, COUNT_HEARTBEAT)


def end_of_session(session: bytes, next_seq: int) -> bytes:
    return HEADER.pack(session, next_seq, COUNT_END)


def packetize(
    msgs: Iterable[bytes],
    session: bytes,
    first_seq: int = FIRST_SEQ,
    max_payload: int = MAX_UDP_PAYLOAD,
    max_count: int = COUNT_END - 1,
) -> Iterator[bytes]:
    """Greedily batch messages into packets of at most max_payload bytes."""
    seq, size = first_seq, HEADER_LEN
    batch: list[bytes] = []
    for m in msgs:
        need = LEN_PREFIX + len(m)
        if HEADER_LEN + need > max_payload:
            raise ValueError(f"message of {len(m)} bytes cannot fit in {max_payload}")
        if batch and (size + need > max_payload or len(batch) == max_count):
            yield pack(session, seq, batch)
            seq += len(batch)
            batch, size = [], HEADER_LEN
        batch.append(m)
        size += need
    if batch:
        yield pack(session, seq, batch)


@dataclass(slots=True)
class SeqCounters:
    packets: int = 0
    heartbeats: int = 0
    end_of_session: int = 0
    gaps: int = 0  # gap events
    gap_messages: int = 0  # messages missing across all gaps
    duplicates: int = 0  # packets dropped as duplicate or late
    session_changes: int = 0  # not counting the first session seen
    malformed_packets: int = 0  # short header, message overruns packet, or trailing bytes


@dataclass(slots=True)
class SeqTracker:
    """ADR 0003. One session at a time; `stale` is sticky until reset (a new object)."""

    session: bytes | None = None
    expected: int = FIRST_SEQ
    stale: bool = False
    c: SeqCounters = field(default_factory=SeqCounters)

    def feed(self, pkt: bytes) -> list[tuple[int, bytes]]:
        self.c.packets += 1
        if len(pkt) < HEADER_LEN:
            self._malformed()
            return []
        session, seq, count = HEADER.unpack_from(pkt)
        if session != self.session:
            if self.session is not None:
                self.c.session_changes += 1
                self.stale = True  # the book belongs to the old session
            self.session, self.expected = session, FIRST_SEQ

        if seq < self.expected:
            self.c.duplicates += 1  # whole packet dropped; any new tail shows up as a gap
            return []
        if seq > self.expected:
            self.c.gaps += 1
            self.c.gap_messages += seq - self.expected
            self.stale = True
            self.expected = seq

        if count in (COUNT_HEARTBEAT, COUNT_END):
            if count == COUNT_END:
                self.c.end_of_session += 1
            else:
                self.c.heartbeats += 1
            if len(pkt) != HEADER_LEN:
                self._malformed()
            return []

        out: list[tuple[int, bytes]] = []
        off = HEADER_LEN
        for i in range(count):
            if off + LEN_PREFIX > len(pkt):
                break
            (n,) = struct.unpack_from(">H", pkt, off)
            if off + LEN_PREFIX + n > len(pkt):
                break
            out.append((seq + i, pkt[off + LEN_PREFIX : off + LEN_PREFIX + n]))
            off += LEN_PREFIX + n
        if len(out) != count or off != len(pkt):
            self._malformed()  # messages before the break were delivered; the rest are lost
        self.expected = seq + count
        return out

    def _malformed(self) -> None:
        self.c.malformed_packets += 1
        self.stale = True


@dataclass(frozen=True, slots=True)
class Record:
    """One decoded book message for the configured symbol: the RTL decoder's output."""

    seq: int  # MoldUDP64 sequence number of the message (the "cause" in BBO output)
    msg: Message


@dataclass(slots=True)
class FeedCounters:
    messages: int = 0  # accepted by the tracker
    unknown_types: int = 0  # skipped by length
    malformed_messages: int = 0  # known type, wrong length or bad field
    other_locate: int = 0  # book messages for other stocks, or before the locate is known
    directory_matches: int = 0  # R messages for our symbol
    locate_changes: int = 0  # a later R for our symbol carried a different locate


BOOK_TYPES = frozenset(b"AFECXDU")


@dataclass(slots=True)
class FeedDecoder:
    """ADR 0004: the locate is learned from the R message whose stock matches `symbol`,
    unless `locate` is configured up front (host fallback)."""

    symbol: str
    locate: int | None = None
    tracker: SeqTracker = field(default_factory=SeqTracker)
    c: FeedCounters = field(default_factory=FeedCounters)
    records: list[Record] = field(default_factory=list)

    @property
    def stale(self) -> bool:
        return self.tracker.stale or self.c.malformed_messages > 0

    def feed(self, pkt: bytes) -> list[Record]:
        """Same order of checks as the RTL: type, length, locate, then fields."""
        new: list[Record] = []
        for seq, raw in self.tracker.feed(pkt):
            self.c.messages += 1
            if not raw:
                self.c.malformed_messages += 1
                continue
            kind = raw[:1]
            want = LENGTHS.get(kind)
            if want is None:
                self.c.unknown_types += 1  # skipped by its length prefix
                continue
            if len(raw) != want:
                self.c.malformed_messages += 1
                continue
            if kind == b"R":
                d = decode(raw)
                assert isinstance(d, Directory)
                if d.stock == self.symbol:
                    self.c.directory_matches += 1
                    if self.locate is not None and self.locate != d.locate:
                        self.c.locate_changes += 1
                    self.locate = d.locate
                continue
            if kind[0] not in BOOK_TYPES:
                continue  # S: log only
            if int.from_bytes(raw[1:3]) != self.locate:
                self.c.other_locate += 1
                continue
            try:
                m = decode(raw)
            except MalformedMessage:
                self.c.malformed_messages += 1  # e.g. a side that is not B or S
                continue
            assert m is not None
            new.append(Record(seq, m))
        self.records.extend(new)
        return new
