"""Last BBO per ITCH minute from a board run and from the golden model, side by side.

Usage: python -m analysis.board_bbo --log LOG --packets PKTS --out CSV

LOG is a host/capture log (BBO mode); PKTS the feed as sent (`replay --out`). The model is
model.protocol.expected() over PKTS with the config echoed in LOG's ack (the bounded book,
the RTL's geometry), so both columns come from the same record format. One row per minute
in which either side published a BBO: the last BBO of that minute (prices in ITCH units,
0 = empty side). Feeds the board-vs-model day plot in analysis/plots.py; the record-level
comparison is analysis/compare.py.
"""

from __future__ import annotations

import argparse
import csv
from pathlib import Path

from analysis.compare import base_ticks, read_log
from model import protocol as P
from model.bounded import BoundedBook
from model.itch import iter_raw

FIELDS = ("bid_price", "bid_size", "ask_price", "ask_size")


def by_minute(records: list[bytes]) -> dict[int, tuple[int, int, int, int]]:
    """ITCH minute -> (bid, bid size, ask, ask size) of the minute's last BBO record."""
    out: dict[int, tuple[int, int, int, int]] = {}
    for r in records:
        ts = int.from_bytes(r[24:30])  # docs/protocol.md BBO record, offset 24
        out[ts // 60_000_000_000] = (
            int.from_bytes(r[4:8]),
            int.from_bytes(r[12:18]),
            int.from_bytes(r[8:12]),
            int.from_bytes(r[18:24]),
        )
    return out


def rows(log: Path, packets: Path) -> list[dict[str, int]]:
    cfg, dgs = read_log(log)
    if cfg.mode != 0:
        raise ValueError(f"{log}: a trace-mode run has no BBOs")
    dgs.sort(key=lambda d: d.pkt_seq)
    board = by_minute([r for d in dgs if d.kind == P.BBO for r in d.records])
    want, _ = P.expected(iter_raw(packets.open("rb")), cfg, BoundedBook(base_ticks=base_ticks(cfg)))
    model = by_minute(want)
    out = []
    for m in sorted(board.keys() | model.keys()):
        row = {"minute": m}
        for side, src in (("board", board), ("model", model)):
            for k, v in zip(FIELDS, src.get(m, (0, 0, 0, 0)), strict=True):
                row[f"{side}_{k}"] = v
        out.append(row)
    return out


def main(argv: list[str] | None = None) -> None:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--log", type=Path, required=True)
    ap.add_argument("--packets", type=Path, required=True)
    ap.add_argument("--out", type=Path, required=True)
    a = ap.parse_args(argv)
    data = rows(a.log, a.packets)
    with a.out.open("w", newline="") as f:
        w = csv.DictWriter(f, fieldnames=list(data[0]), lineterminator="\n")
        w.writeheader()
        w.writerows(data)
    diff = sum(
        any(r[f"board_{k}"] != r[f"model_{k}"] for k in FIELDS) for r in data
    )  # fmt: skip
    print(f"{a.out}: {len(data)} minutes, {diff} differ")


if __name__ == "__main__":
    main()
