"""Diff the FPGA's output (a host/capture log) against the golden model (buildplan §7).

Usage: python -m analysis.compare --log LOG --packets PKTS [--context N]

LOG is what host/capture wrote; its first datagram is the config ack, which echoes the
config the run used (mode, symbol, locate, price window), so nothing about the config is
restated here. PKTS is the feed exactly as sent: `host/replay ... --out PKTS` with the same
input and flags as the hardware run (replay's packets do not depend on pacing). The model
runs model.protocol.expected() over PKTS with the RTL's book geometry, and the FPGA's
BBO/trace records, in pkt_seq order, are compared with it record by record.

Output: a key=value summary on stdout for the results sidecar; the first mismatch with the
N records before it, decoded, on stderr. Exit 0 on a full match, 1 otherwise. A run with
lost output datagrams (pkt_seq gaps) cannot match and says so; datagrams the host reordered
or duplicated are put back in order and counted.
"""

from __future__ import annotations

import argparse
import struct
import sys
from pathlib import Path

from model import protocol as P
from model.bounded import BoundedBook
from model.itch import iter_raw

_BBO_FIELDS = "symbol flags bid_px ask_px bid_sz ask_sz itch_ts pad mold_seq t0 t1 t2".split()
_TRACE_FIELDS = "msg_type flags itch_ts mold_seq ref new_ref shares price".split()
_BBO = struct.Struct(">HHII6s6s6sHQQQQ")
_TRACE = struct.Struct(">cB6sQQQII")


def decode(kind: int, rec: bytes) -> dict[str, int | str]:
    """A record's fields by name, for the mismatch report."""
    s, names = (_BBO, _BBO_FIELDS) if kind == P.BBO else (_TRACE, _TRACE_FIELDS)
    out: dict[str, int | str] = {}
    for k, v in zip(names, s.unpack(rec), strict=True):
        if isinstance(v, bytes):  # 6-byte integers, or the 1-byte ITCH type
            out[k] = int.from_bytes(v) if len(v) == 6 else v.decode("latin-1")
        else:
            out[k] = v
    return out


def read_log(path: Path) -> tuple[P.Config, list[P.Datagram]]:
    """The config from the leading ack, then every datagram after it."""
    dgs = [P.parse(p) for p in iter_raw(path.open("rb"))]
    if not dgs or dgs[0].kind != P.CONFIG_ACK:
        raise ValueError(f"{path}: does not start with a config ack")
    cfg = P.parse_config(P.packet(P.CONFIG, 0, dgs[0].records))
    if cfg is None:
        raise ValueError(f"{path}: the ack echoes a config the FPGA would reject")
    if any(d.kind == P.CONFIG_ACK for d in dgs[1:]):
        raise ValueError(f"{path}: reconfigured mid-run (a second ack)")
    return cfg, dgs[1:]


def base_ticks(cfg: P.Config) -> int:
    """Inverse of model.protocol.window_lo_price (ADR 0002)."""
    if cfg.lo_price % 100:
        raise ValueError(f"lo_price {cfg.lo_price} is not a whole tick")
    return cfg.lo_price // 100 + 2048


def compare(log: Path, packets: Path, context: int = 5) -> tuple[dict[str, int], list[str]]:
    """Summary counters, and report lines for the first mismatch (empty on a match)."""
    cfg, dgs = read_log(log)
    kind = P.TRACE if cfg.mode == 1 else P.BBO
    # The host may reorder or duplicate datagrams (progress.md); pkt_seq undoes both. The
    # same pkt_seq with other content is not a host artifact.
    by_seq: dict[int, P.Datagram] = {}
    for d in dgs:
        if by_seq.setdefault(d.pkt_seq, d) != d:
            raise ValueError(f"pkt_seq {d.pkt_seq} received twice with different content")
    dups = len(dgs) - len(by_seq)
    dgs = sorted(by_seq.values(), key=lambda d: d.pkt_seq)
    lost = dgs[-1].pkt_seq - len(dgs) if dgs else 0
    got = [r for d in dgs if d.kind == kind for r in d.records]
    want, _ = P.expected(iter_raw(packets.open("rb")), cfg, BoundedBook(base_ticks=base_ticks(cfg)))

    first = next((i for i, (g, w) in enumerate(zip(got, want, strict=False)) if g != w), None)
    if first is None and len(got) != len(want):
        first = min(len(got), len(want))
    summary = {
        "mode": cfg.mode,
        "expected_records": len(want),
        "fpga_records": len(got),
        "datagrams": len(dgs),
        "lost_datagrams": lost,
        "duplicate_datagrams": dups,
        "mismatched_positions": sum(g != w for g, w in zip(got, want, strict=False)),
        "first_mismatch": -1 if first is None else first,
        "match": int(first is None and lost == 0),
    }
    report: list[str] = []
    if first is not None:
        report.append(f"first mismatch at record {first}:")
        for i in range(max(0, first - context), first + 1):
            g = decode(kind, got[i]) if i < len(got) else "(none)"
            w = decode(kind, want[i]) if i < len(want) else "(none)"
            report.append(f"  [{i}] fpga  {g}")
            report.append(f"  [{i}] model {w}")
    return summary, report


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--log", type=Path, required=True)
    ap.add_argument("--packets", type=Path, required=True)
    ap.add_argument("--context", type=int, default=5)
    a = ap.parse_args(argv)
    summary, report = compare(a.log, a.packets, a.context)
    for k, v in summary.items():
        print(f"{k}={v}")
    for line in report:
        print(line, file=sys.stderr)
    return 0 if summary["match"] else 1


if __name__ == "__main__":
    sys.exit(main())
