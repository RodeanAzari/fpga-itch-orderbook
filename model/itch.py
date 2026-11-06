"""ITCH 5.0 parser for the docs/buildplan.md §5.3 subset. Pure: reads a stream, returns values."""

from __future__ import annotations

import struct
from collections.abc import Iterator
from dataclasses import dataclass
from typing import BinaryIO

_U16 = struct.Struct(">H")
_HEADER = struct.Struct(">BHH6s")  # type, locate, tracking, timestamp (ns since midnight)
_ADD = struct.Struct(">QcI8sI")  # ref 11, side 19, shares 20, stock 24, price 32
_REDUCE = struct.Struct(">QI")  # ref 11, shares 19 (E, C, X)
_REPLACE = struct.Struct(">QQII")  # orig ref 11, new ref 19, shares 27, price 31
_BODY = 11  # ITCH 5.0 common header length; every body offset above starts here

# Spec length of each type the book cares about (ITCH 5.0 §1).
LENGTHS = {
    b"S": 12,
    b"R": 39,
    b"A": 36,
    b"F": 40,
    b"E": 31,
    b"C": 36,
    b"X": 23,
    b"D": 19,
    b"U": 35,
}


class MalformedMessage(ValueError):
    """A known message type whose contents break the spec. Framing is still intact."""


class TruncatedStream(ValueError):
    """The stream ended inside a length prefix or message."""


@dataclass(frozen=True, slots=True)
class Add:
    locate: int
    ts: int
    ref: int
    side: str
    shares: int
    stock: str
    price: int


@dataclass(frozen=True, slots=True)
class Reduce:
    """E, C and X. C's execution price is deliberately not kept: the book uses resting price."""

    locate: int
    ts: int
    ref: int
    shares: int


@dataclass(frozen=True, slots=True)
class Delete:
    locate: int
    ts: int
    ref: int


@dataclass(frozen=True, slots=True)
class Replace:
    locate: int
    ts: int
    ref: int
    new_ref: int
    shares: int
    price: int


@dataclass(frozen=True, slots=True)
class Directory:
    locate: int
    ts: int
    stock: str


@dataclass(frozen=True, slots=True)
class SystemEvent:
    locate: int
    ts: int
    code: str


Message = Add | Reduce | Delete | Replace | Directory | SystemEvent


def iter_raw(stream: BinaryIO) -> Iterator[bytes]:
    """Yield message bytes from a length-prefixed stream. Never interprets the contents."""
    read = stream.read
    unpack = _U16.unpack
    while prefix := read(2):
        if len(prefix) != 2:
            raise TruncatedStream("stream ended inside a length prefix")
        length = unpack(prefix)[0]
        msg = read(length)
        if len(msg) != length:
            raise TruncatedStream(f"expected {length} message bytes, got {len(msg)}")
        yield msg


def decode(msg: bytes) -> Message | None:
    """Decode one message. Returns None for types outside the subset (skipped by length)."""
    if not msg:
        raise MalformedMessage("zero-length message")
    kind = msg[:1]
    expected = LENGTHS.get(kind)
    if expected is None:
        return None
    if len(msg) != expected:
        raise MalformedMessage(f"type {kind!r}: length {len(msg)}, spec says {expected}")

    _, locate, _, raw_ts = _HEADER.unpack_from(msg)
    ts = int.from_bytes(raw_ts)
    if kind in (b"A", b"F"):
        ref, side, shares, stock, price = _ADD.unpack_from(msg, _BODY)
        if side not in (b"B", b"S"):
            raise MalformedMessage(f"bad side {side!r}")
        return Add(locate, ts, ref, side.decode(), shares, stock.decode().rstrip(), price)
    if kind in (b"E", b"C", b"X"):
        ref, shares = _REDUCE.unpack_from(msg, _BODY)
        return Reduce(locate, ts, ref, shares)
    if kind == b"D":
        return Delete(locate, ts, int.from_bytes(msg[_BODY : _BODY + 8]))
    if kind == b"U":
        ref, new_ref, shares, price = _REPLACE.unpack_from(msg, _BODY)
        return Replace(locate, ts, ref, new_ref, shares, price)
    if kind == b"R":
        return Directory(locate, ts, msg[_BODY : _BODY + 8].decode().rstrip())
    return SystemEvent(locate, ts, msg[_BODY : _BODY + 1].decode())
