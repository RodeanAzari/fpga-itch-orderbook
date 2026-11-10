"""Tests for analysis/board_bbo.py.

Why: the day plot claims "board = model" minute by minute; that claim is only as good as
the extraction. The last BBO of each minute must win, both sides must come from the same
config, and a board record that differs must show up in its minute.
"""

from __future__ import annotations

from pathlib import Path

from analysis import board_bbo as B
from analysis.test_compare import datagrams, setup, write_framed
from model import protocol as P
from model.book import Bbo


def rec(ts: int, bid: int, ask: int) -> bytes:
    return P.bbo_record(
        Bbo(ts=ts, bid_price=bid, bid_size=10, ask_price=ask, ask_size=20), 1, False
    )


def test_last_bbo_of_each_minute_wins() -> None:
    m = 60_000_000_000
    got = B.by_minute([rec(5 * m, 1, 2), rec(5 * m + m - 1, 3, 4), rec(6 * m, 5, 6)])
    assert got == {5: (3, 10, 4, 20), 6: (5, 10, 6, 20)}


def test_board_equal_to_model_and_one_difference(tmp_path: Path) -> None:
    pkts, cfg, recs = setup(tmp_path)
    log = write_framed(tmp_path / "log.bin", datagrams(cfg, recs))
    rows = B.rows(log, pkts)
    assert rows and all(
        r[f"board_{k}"] == r[f"model_{k}"] for r in rows for k in B.FIELDS
    )  # fmt: skip
    last = recs[-1]
    changed = last[:4] + (int.from_bytes(last[4:8]) + 100).to_bytes(4) + last[8:]
    log = write_framed(tmp_path / "log.bin", datagrams(cfg, [*recs[:-1], changed]))
    diff = [r for r in B.rows(log, pkts) if r["board_bid_price"] != r["model_bid_price"]]
    assert len(diff) == 1 and diff[0]["minute"] == int.from_bytes(last[24:30]) // 60_000_000_000
