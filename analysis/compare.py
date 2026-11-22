"""Diff the FPGA's output (a host/capture log) against the golden model (buildplan §7).

Usage: python -m analysis.compare --log LOG --packets PKTS [--context N]

LOG is what host/capture wrote; its first datagram is the config ack, which echoes the
config the run used (mode, symbol, locate, price window), so nothing about the config is
restated here. PKTS is the feed exactly as sent: `host/replay ... --out PKTS` with the same
input and flags as the hardware run (replay's packets do not depend on pacing). The model
runs model.protocol.expected() over PKTS with the RTL's book geometry, and the FPGA's
BBO/trace records, in pkt_seq order, are compared with it record by record.

In BBO mode `unflagged_wrong` counts BBOs that are wrong without the stale flag (CLAUDE.md:
never silently produce a wrong book); it must be 0 even when a run lost data.

The log's last stats snapshot is also checked against the model's feed counters
(MoldUDP64 sequencing, the ITCH type/locate filter, the learned locate; model.protocol.
feed_stats): `feed_counters_match` is 1 or 0, or -1 when the log has no stats. It is
reported beside `match`, not folded into it: it shows the filter on messages that make
no record (a whole-day replay's other symbols, buildplan §8.4).

After an output loss, `received_wrong` and `missing_records` still check every record that
arrived: each is placed by its cause message's mold_seq (at most one record per message in
either mode) and must equal the model's record there. A record is also wrong if it is not
the model's next record although no datagram was lost before it (skipped, repeated or
reordered by the board). `missing_records` counts the model's records not received intact;
with received_wrong 0 they lie in the lost datagrams. A lost stats datagram also excuses a
skip there, as a lost datagram's kind is unknown.

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


def _mold_seq(bbo: bytes) -> int:
    return int.from_bytes(bbo[32:40])  # docs/protocol.md BBO record, offset 32


def _cause_seq(kind: int, rec: bytes) -> int:
    """docs/protocol.md: mold_seq at offset 32 (BBO record) or 8 (trace record)."""
    return _mold_seq(rec) if kind == P.BBO else int.from_bytes(rec[8:16])


def received_vs_model(kind: int, dgs: list[P.Datagram], want: list[bytes]) -> tuple[int, int]:
    """(received_wrong, missing_records), see the module docstring. `dgs` in pkt_seq order
    after the ack (pkt_seq 0), BBO times already zeroed."""
    at = {_cause_seq(kind, w): i for i, w in enumerate(want)}
    nxt = prev = wrong = 0
    gap = False
    intact: set[int] = set()
    for d in dgs:
        gap = gap or d.pkt_seq != prev + 1  # pending until a record is placed after it
        prev = d.pkt_seq
        if d.kind != kind:
            continue
        for r in d.records:
            i = at.get(_cause_seq(kind, r))
            if i is None or i < nxt:  # no such message, or one already passed: the
                wrong += 1  # position and a pending gap stay for the records after it
                continue
            if want[i] == r and (i == nxt or gap):
                intact.add(i)
            else:
                wrong += 1
            nxt, gap = i + 1, False
    return wrong, len(want) - len(intact)


def unflagged_wrong(got: list[bytes], want: list[bytes]) -> int:
    """FPGA BBOs that differ from the model's BBO for the same cause message (or that the
    model did not produce at all) and do not carry the stale flag: a silently wrong book.
    Matching by mold_seq (one BBO per message at most) keeps this exact after a loss,
    where positions no longer line up. Must be 0 on every run, lossy or not."""
    by_seq = {_mold_seq(w): w for w in want}
    return sum(
        1 for g in got if g != by_seq.get(_mold_seq(g)) and not int.from_bytes(g[2:4]) & P.STALE
    )


def compare(
    log: Path, packets: Path, context: int = 5, cut_through: bool = False
) -> tuple[dict[str, int], list[str]]:
    """Summary counters, and report lines for the first mismatch (empty on a match).

    `cut_through`: the bitstream's build mode, which the operator states (every BBO carries
    it as a flag, ADR 0013; taking it from the records would make that flag uncheckable).
    """
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
    if kind == P.BBO:  # t0/t1/t2 are measured (ADR 0011); every other byte must match
        dgs = [
            P.Datagram(d.kind, d.pkt_seq, tuple(P.without_times(r) for r in d.records))
            if d.kind == kind
            else d
            for d in dgs
        ]
    got = [r for d in dgs if d.kind == kind for r in d.records]
    want, dec = P.expected(
        iter_raw(packets.open("rb")), cfg, BoundedBook(base_ticks=base_ticks(cfg)), cut_through
    )

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
    summary["received_wrong"], summary["missing_records"] = received_vs_model(kind, dgs, want)
    if kind == P.BBO:
        summary["unflagged_wrong"] = unflagged_wrong(got, want)
    report: list[str] = []
    if first is not None:
        report.append(f"first mismatch at record {first}:")
        for i in range(max(0, first - context), first + 1):
            g = decode(kind, got[i]) if i < len(got) else "(none)"
            w = decode(kind, want[i]) if i < len(want) else "(none)"
            report.append(f"  [{i}] fpga  {g}")
            report.append(f"  [{i}] model {w}")
    stats = [d for d in dgs if d.kind == P.STATS]
    summary["feed_counters_match"] = -1  # no stats in the log
    if stats:  # the last snapshot, by pkt_seq: capture's --idle >= 2 takes it after the feed
        # Older bitstreams sent fewer counters (later ones are appended): zip to the shorter.
        vals = (int.from_bytes(r) for r in stats[-1].records)
        board = dict(zip(P.STATS_NAMES, vals, strict=False))
        diff = [(k, board[k], v) for k, v in P.feed_stats(dec).items() if board[k] != v]
        summary["feed_counters_match"] = int(not diff)
        report += [f"counter {k}: fpga {b} model {m}" for k, b, m in diff]
    return summary, report


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--log", type=Path, required=True)
    ap.add_argument("--packets", type=Path, required=True)
    ap.add_argument("--context", type=int, default=5)
    ap.add_argument(
        "--cut-through", action="store_true", help="the bitstream was built cut-through"
    )
    a = ap.parse_args(argv)
    summary, report = compare(a.log, a.packets, a.context, a.cut_through)
    for k, v in summary.items():
        print(f"{k}={v}")
    for line in report:
        print(line, file=sys.stderr)
    return 0 if summary["match"] else 1


if __name__ == "__main__":
    sys.exit(main())
