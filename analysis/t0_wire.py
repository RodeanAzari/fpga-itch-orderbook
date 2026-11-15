"""T0 on hardware: a cut-through run's T1 - T0 with the wire time taken out (docs/latency.md).

Usage: python -m analysis.t0_wire --log LOG --packets PKTS --out CSV

In cut-through (ADR 0013) a message is decoded as its bytes arrive, so for every BBO
  T1 - T0 = 10 cycles x (bytes after the SFD up to the cause message's last byte) + C,
10 cycles being one byte at 100 Mb/s (80 ns) in 125 MHz cycles. C is the fixed pipeline
from the pins to the decoder, minus T0's own delay. Simulation (test_core, fixed clock
phases) gives one C per case; on the board each frame's rx_clk phase against the core
clock moves it by a cycle, and a host clock off by some ppm tilts it along the frame. A
T0 synchronizer that misbehaves on hardware would show as a wider or shifted spread.
A frame's last message is reported apart: mii_rx emits a frame's last byte one nibble
sooner than the others (eth-core rtl/mii_rx.sv header), and that byte releases it.

PKTS is the feed as sent (host/replay --out), which places each message in its frame.
Output CSV rows: last_in_frame, cycles, count. Stdout also gives the least-squares slope
of the other messages' C against their byte position, as a host clock offset in ppm.
"""

from __future__ import annotations

import argparse
import csv
import struct
from collections import Counter
from collections.abc import Iterable
from pathlib import Path

from analysis.latency import bbo_records
from model import protocol as P
from model.itch import iter_raw
from model.moldudp64 import COUNT_END, HEADER, HEADER_LEN, LEN_PREFIX

FRAME_HDR = 14 + 20 + 8  # Ethernet, IPv4 (no options), UDP: bytes before the payload
CYCLES_PER_BYTE = 10  # 80 ns per byte at 100 Mb/s, 8 ns cycles (ADR 0009)
SEQ = slice(32, 40)  # docs/protocol.md BBO record, mold_seq


def message_ends(pkts: Iterable[bytes]) -> tuple[dict[int, int], set[int]]:
    """Per message seq: bytes after the SFD to its last byte (first delivery wins); and the
    seqs whose last byte ends their frame's payload."""
    end: dict[int, int] = {}
    last: set[int] = set()
    for p in pkts:
        _, seq, cnt = HEADER.unpack(p[:HEADER_LEN])
        if cnt == COUNT_END:
            continue
        off, new = HEADER_LEN, False
        for k in range(cnt):
            off += LEN_PREFIX + int.from_bytes(p[off : off + LEN_PREFIX])
            new = seq + k not in end
            end.setdefault(seq + k, FRAME_HDR + off)
        if new and off == len(p):
            last.add(seq + cnt - 1)
    return end, last


def wire_offsets(
    recs: list[bytes], end: dict[int, int], last: set[int]
) -> list[tuple[bool, int, int]]:
    """(last in frame, T1 - T0 - wire cycles, byte position) per BBO with a T0."""
    out = []
    for r in recs:
        seq = int.from_bytes(r[SEQ])
        t0, t1, _ = struct.unpack(">QQQ", r[P.TIMES])
        if t0:
            out.append((seq in last, t1 - t0 - CYCLES_PER_BYTE * end[seq], end[seq]))
    return out


def ppm(points: list[tuple[int, float]]) -> float:
    """Host clock offset from the slope of C (cycles) against byte position: a host clock
    fast by x ppm sends each byte x ppm early, so C falls by 10 * x * 1e-6 per byte."""
    n = len(points)
    mx = sum(b for b, _ in points) / n
    my = sum(c for _, c in points) / n
    sxx = sum((b - mx) ** 2 for b, _ in points)
    sxy = sum((b - mx) * (c - my) for b, c in points)
    return -(sxy / sxx) / CYCLES_PER_BYTE * 1e6


def main(argv: list[str] | None = None) -> None:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--log", type=Path, required=True)
    ap.add_argument("--packets", type=Path, required=True)
    ap.add_argument("--out", type=Path, required=True)
    a = ap.parse_args(argv)
    recs = bbo_records(a.log)
    if not all(int.from_bytes(r[2:4]) & P.CUT_THROUGH for r in recs):
        raise SystemExit("t0_wire needs a cut-through run (every record flagged cut_through)")
    with a.packets.open("rb") as f:
        end, last = message_ends(iter_raw(f))
    offs = wire_offsets(recs, end, last)
    hist = Counter((lf, c) for lf, c, _ in offs)
    with a.out.open("w", newline="") as f:
        w = csv.writer(f)
        w.writerow(("last_in_frame", "cycles", "count"))
        for (lf, c), n in sorted(hist.items()):
            w.writerow((int(lf), c, n))
    for (lf, c), n in sorted(hist.items()):
        print(f"last_in_frame={int(lf)} cycles={c} count={n}")
    print(f"records={len(offs)} no_t0={len(recs) - len(offs)}")
    print(f"host_clock_ppm={ppm([(b, float(c)) for lf, c, b in offs if not lf]):.1f}")


if __name__ == "__main__":
    main()
