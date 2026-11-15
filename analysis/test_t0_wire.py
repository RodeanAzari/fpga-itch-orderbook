"""Tests for analysis/t0_wire.py: placing each message on the wire, and the ppm slope.

Why they matter: the T0 check subtracts 10 cycles per byte, so a byte miscounted in a
header or a length prefix shifts every C by 10 and fakes (or hides) a T0 offset.
Reference: the frame layout by hand (Ethernet 14 + IPv4 20 + UDP 8 + MoldUDP64 header 20,
then 2-byte length + message per message, buildplan §5).
"""

from __future__ import annotations

import struct

import pytest

from analysis.t0_wire import message_ends, ppm, wire_offsets
from model.moldudp64 import end_of_session, heartbeat, pack

S = b"SESSION001"


def test_message_ends_by_hand() -> None:
    pkts = [
        pack(S, 5, [b"a" * 36, b"b" * 23]),
        heartbeat(S, 7),
        pack(S, 7, [b"c" * 50]),
        pack(S, 5, [b"x" * 36]),  # a re-sent message: its first delivery counts
        end_of_session(S, 8),
    ]
    end, last = message_ends(pkts)
    assert end == {5: 42 + 20 + 2 + 36, 6: 42 + 20 + 38 + 2 + 23, 7: 42 + 20 + 2 + 50}
    assert last == {6, 7}  # 5 ends only its re-sent copy, which the RTL drops as a duplicate


def rec(seq: int, t0: int, t1: int) -> bytes:
    return bytes(32) + struct.pack(">QQQQ", seq, t0, t1, t1 + 17)


def test_wire_offsets_subtract_10_cycles_per_byte() -> None:
    end = {1: 100, 2: 140}
    out = wire_offsets([rec(1, 1000, 1000 + 1053), rec(2, 1000, 1000 + 1448), rec(1, 0, 9)],
                       end, {2})  # fmt: skip
    assert out == [(False, 53, 100), (True, 48, 140)]  # the T0-less record is left out


def test_ppm_sign_and_size() -> None:
    # host 100 ppm fast: each byte 1e-4 early, so C falls 10 * 1e-4 cycles per byte
    assert ppm([(b, 53 - 1e-3 * b) for b in range(60, 1500, 10)]) == pytest.approx(100.0)
    assert ppm([(b, 53.0) for b in range(60, 1500)]) == 0
