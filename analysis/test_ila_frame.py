"""Tests for analysis/ila_frame.py: the ILA capture check of one frame through the pipeline.

Why they matter: this script is what turns an ILA screenshot into evidence. If it decodes
nibbles in the wrong order, compares the wrong bytes or accepts a frame cut by the
capture window, a broken pipeline would be reported as checked.
Reference: 802.3 MII (low nibble first; 7 x 0x55 preamble, 0xD5 SFD; FCS = CRC-32,
least significant byte first), IPv4/UDP headers by hand, and the golden model's
FeedDecoder for the expected record types. The traces are built here independently of
the decoder under test, in Vivado's ILA CSV layout.
"""

from __future__ import annotations

import csv
import struct
import zlib
from pathlib import Path

import pytest

from analysis.ila_frame import CORE_ROLES, MII_ROLES, check, load, tables
from model.generator import Config, generate
from model.moldudp64 import FeedDecoder, packetize

S = b"20191018AA"
PKTS = list(packetize(generate(7, 400, Config(stock="AAPL", locate=14)), S))
K = 2  # the captured packet


def frame_of(payload: bytes) -> bytes:
    eth = bytes.fromhex("01005e010101") + bytes.fromhex("020000000001") + b"\x08\x00"
    ip = struct.pack(
        ">BBHHHBBH4s4s",
        0x45,
        0,
        20 + 8 + len(payload),
        0,
        0,
        64,
        17,
        0,
        bytes([192, 168, 10, 1]),
        bytes([239, 1, 1, 1]),
    )
    udp = struct.pack(">HHHH", 40000, 26400, 8 + len(payload), 0)
    return eth + ip + udp + payload


def expected_kinds(k: int) -> list[int]:
    dec = FeedDecoder("AAPL")
    out: list[int] = []
    for p in PKTS[: k + 1]:
        out = [r.kind for r in dec.feed(p)]
    return out


def write_csv(path: Path, names: dict[str, int], rows: list[dict[str, int]], trig: int) -> None:
    """Vivado's layout: probe-name header, radix row, one row per sample (hex buses)."""
    roles = list(names)
    with path.open("w", newline="") as f:
        w = csv.writer(f)
        w.writerow(
            ["Sample in Buffer", "Sample in Window", "TRIGGER"]
            + [f"{n}[{names[n] - 1}:0]" if names[n] > 1 else n for n in roles]
        )
        w.writerow(
            ["Radix - UNSIGNED", "UNSIGNED", "UNSIGNED"]
            + ["HEX" if names[n] > 1 else "BINARY" for n in roles]
        )
        for i, r in enumerate(rows):
            w.writerow(
                [i, i, int(i == trig)]
                + [format(r.get(n, 0), "X" if names[n] > 1 else "b") for n in roles]
            )


MII_NAMES = {n: (4 if n.endswith("rxd_q") else 1) for n in MII_ROLES}
_RXD, _DV = (n for n in MII_ROLES if MII_ROLES[n] in ("rxd", "dv"))
CORE_NAMES = {n: (8 if "tdata" in n or "type" in n else 1) for n in CORE_ROLES}


def mii_rows(
    wire: bytes, lead: int = 32, tail: int = 64, swap: bool = False
) -> list[dict[str, int]]:
    octets = b"\x55" * 7 + b"\xd5" + wire
    rows: list[dict[str, int]] = [{} for _ in range(lead)]
    for o in octets:
        lo, hi = o & 0xF, o >> 4
        for n in (hi, lo) if swap else (lo, hi):
            rows.append({_RXD: n, _DV: 1})
    return rows + [{} for _ in range(tail)]


def core_rows(
    frame: bytes, payload: bytes, kinds: list[int], depth: int = 4096
) -> list[dict[str, int]]:
    rows: list[dict[str, int]] = [{} for _ in range(depth)]
    c = "u_core/"
    t = 20
    for i, b in enumerate(frame):
        rows[t + i] |= {
            c + "f_tdata": b,
            c + "f_tvalid": 1,
            c + "f_tready": 1,
            c + "f_tlast": int(i == len(frame) - 1),
        }
    u0 = t + 44  # udp_rx's payload trails its input by a few cycles
    for i, b in enumerate(payload):
        rows[u0 + i] |= {
            c + "u_tdata": b,
            c + "u_tvalid": 1,
            c + "u_tready": 1,
            c + "u_tlast": int(i == len(payload) - 1),
        }
    for j, k in enumerate(kinds):
        rows[u0 + 60 + 40 * j] |= {c + "r_valid": 1, c + "r_ready": 1, c + "r_type": k}
    return rows


def run(
    tmp_path: Path, wire: bytes, frame: bytes, payload: bytes, kinds: list[int], swap: bool = False
) -> dict[str, int]:
    write_csv(tmp_path / "mii.csv", MII_NAMES, mii_rows(wire, swap=swap), 32)
    write_csv(tmp_path / "core.csv", CORE_NAMES, core_rows(frame, payload, kinds), 64)
    res, _ = check(
        load(tmp_path / "mii.csv", MII_ROLES), load(tmp_path / "core.csv", CORE_ROLES), PKTS
    )
    return res


def good() -> tuple[bytes, bytes, bytes, list[int]]:
    payload = PKTS[K]
    frame = frame_of(payload)
    wire = frame + zlib.crc32(frame).to_bytes(4, "little")
    return wire, frame, payload, expected_kinds(K)


def test_a_good_capture_passes_every_check(tmp_path: Path) -> None:
    wire, frame, payload, kinds = good()
    assert kinds, "the packet must make records, or the records check proves nothing"
    assert run(tmp_path, wire, frame, payload, kinds) == dict.fromkeys(
        ("mii_fcs", "fifo_eq_mii", "payload_eq", "sent", "records"), 1
    )


def test_nibbles_high_first_are_not_a_frame(tmp_path: Path) -> None:
    wire, frame, payload, kinds = good()
    with pytest.raises(ValueError, match="SFD"):  # 0xD5 reads as 0x5D
        run(tmp_path, wire, frame, payload, kinds, swap=True)


def test_bad_fcs(tmp_path: Path) -> None:
    wire, frame, payload, kinds = good()
    bad = wire[:-1] + bytes([wire[-1] ^ 1])
    assert run(tmp_path, bad, frame, payload, kinds)["mii_fcs"] == 0


def test_fifo_dropping_a_byte(tmp_path: Path) -> None:
    wire, frame, payload, kinds = good()
    res = run(tmp_path, wire, frame[:100] + frame[101:], payload, kinds)
    assert res["fifo_eq_mii"] == 0


def test_payload_off_by_one(tmp_path: Path) -> None:
    wire, frame, payload, kinds = good()
    res = run(tmp_path, wire, frame, payload[1:], kinds)  # a header byte miscounted
    assert res["payload_eq"] == 0 and res["sent"] == 0


def test_a_packet_never_sent(tmp_path: Path) -> None:
    payload = bytearray(PKTS[K])
    payload[-1] ^= 0xFF
    frame = frame_of(bytes(payload))
    wire = frame + zlib.crc32(frame).to_bytes(4, "little")
    res = run(tmp_path, wire, frame, bytes(payload), expected_kinds(K))
    assert res["fifo_eq_mii"] == 1 and res["sent"] == 0 and res["records"] == 0


def test_wrong_or_missing_record(tmp_path: Path) -> None:
    wire, frame, payload, kinds = good()
    assert run(tmp_path, wire, frame, payload, [ord("X")] + kinds[1:])["records"] == 0
    assert run(tmp_path, wire, frame, payload, kinds[:-1])["records"] == 0


def test_frame_cut_by_the_window(tmp_path: Path) -> None:
    wire, frame, payload, kinds = good()
    rows = mii_rows(wire, tail=0)[:-10]
    write_csv(tmp_path / "mii.csv", MII_NAMES, rows, 32)
    with pytest.raises(ValueError, match="past the capture window"):
        load_and_frame(tmp_path / "mii.csv")


def load_and_frame(p: Path) -> None:
    from analysis.ila_frame import mii_frame

    mii_frame(load(p, MII_ROLES))


def test_core_window_opening_mid_frame_is_not_a_frame(tmp_path: Path) -> None:
    """The core ILA can trigger on a later packet's bytes; a frame already under way at
    sample 0 has lost its head and must not be compared as if whole."""
    wire, frame, payload, kinds = good()
    rows = core_rows(frame, payload, kinds)[30:]  # the frame started 10 samples earlier
    write_csv(tmp_path / "mii.csv", MII_NAMES, mii_rows(wire), 32)
    write_csv(tmp_path / "core.csv", CORE_NAMES, rows, 34)
    with pytest.raises(ValueError, match="no whole packet"):
        check(load(tmp_path / "mii.csv", MII_ROLES), load(tmp_path / "core.csv", CORE_ROLES), PKTS)


def test_table_pairs_each_payload_byte_with_its_frame_byte(tmp_path: Path) -> None:
    """udp_rx emits a byte some cycles after eth_rx does; the table must pair them by
    byte offset, not by sample, or every payload byte shows against the wrong field."""
    wire, frame, payload, kinds = good()
    write_csv(tmp_path / "mii.csv", MII_NAMES, mii_rows(wire), 32)
    write_csv(tmp_path / "core.csv", CORE_NAMES, core_rows(frame, payload, kinds), 64)
    mii, core = load(tmp_path / "mii.csv", MII_ROLES), load(tmp_path / "core.csv", CORE_ROLES)
    _, info = check(mii, core, PKTS)
    rows = [r.split("|")[1:-1] for r in tables(mii, core, info).splitlines() if r.startswith("| ")]
    paired = [r for r in rows if len(r) == 6 and r[4].strip().startswith("0x")]
    assert len(paired) == 64 - 42 + 1  # offsets 42..64 have a payload byte
    for r in paired:
        assert r[2] == r[4], r  # same byte value at the same offset
        assert int(r[3]) - int(r[1]) == 2  # core_rows: frame byte 42 at t + 42, payload at t + 44
