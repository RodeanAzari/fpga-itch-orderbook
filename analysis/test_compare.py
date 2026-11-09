"""Tests for analysis/compare.py.

Why: compare's verdict is the hardware result ("zero mismatches over a day"). A comparison
that cannot fail is worse than none, so each test builds a capture log the way the FPGA
would send it (model.protocol.packet around model.protocol.expected's records, split into
datagrams of varying size with stats interleaved) and then breaks it the ways a real run can
go wrong: one record wrong, one missing or extra, an output datagram lost, datagrams
reordered by the host, the log not starting at our ack.
"""

from __future__ import annotations

import random
import struct
from pathlib import Path

import pytest

from analysis.compare import compare
from model import generator
from model import protocol as P
from model.bounded import BoundedBook
from model.itch import iter_raw
from model.moldudp64 import packetize

BASE_TICKS = generator.DEFAULT.base_price // 100


def write_framed(path: Path, items: list[bytes]) -> Path:
    path.write_bytes(b"".join(struct.pack(">H", len(x)) + x for x in items))
    return path


def setup(tmp_path: Path, mode: int = 0) -> tuple[Path, P.Config, list[bytes]]:
    msgs = generator.generate(seed=11, count=2000)
    pkts = list(packetize(msgs, b"TESTSESS01", max_payload=600))
    cfg = P.Config(
        mode=mode,
        symbol=generator.DEFAULT.stock,
        lo_price=P.window_lo_price(BASE_TICKS),
        locate_valid=mode == 1,
        locate=generator.DEFAULT.locate if mode == 1 else 0,
    )
    recs, _ = P.expected(pkts, cfg, BoundedBook(base_ticks=BASE_TICKS))
    assert len(recs) > 100
    return write_framed(tmp_path / "pkts.bin", pkts), cfg, recs


def datagrams(cfg: P.Config, recs: list[bytes], seed: int = 1) -> list[bytes]:
    """The ack, then records in datagrams of 1..max count, a stats datagram now and then."""
    rng = random.Random(seed)
    kind = P.TRACE if cfg.mode == 1 else P.BBO
    out = [P.packet(P.CONFIG_ACK, 0, [cfg.record()])]
    i = 0
    while i < len(recs):
        if rng.random() < 0.1:
            out.append(P.packet(P.STATS, len(out), [bytes(8)] * P.N_STATS))
        n = rng.randint(1, P.MAX_COUNT[kind])
        out.append(P.packet(kind, len(out), recs[i : i + n]))
        i += n
    return out


def run(tmp_path: Path, pkts: Path, dgs: list[bytes]) -> dict[str, int]:
    summary, report = compare(write_framed(tmp_path / "log.bin", dgs), pkts)
    assert bool(report) == (summary["first_mismatch"] >= 0)
    return summary


@pytest.mark.parametrize("mode", [0, 1])
def test_match(tmp_path: Path, mode: int) -> None:
    pkts, cfg, recs = setup(tmp_path, mode)
    s = run(tmp_path, pkts, datagrams(cfg, recs))
    assert s["match"] == 1 and s["fpga_records"] == s["expected_records"] == len(recs)
    assert s["lost_datagrams"] == 0 and s["first_mismatch"] == -1


@pytest.mark.parametrize("mode", [0, 1])
def test_one_record_wrong(tmp_path: Path, mode: int) -> None:
    pkts, cfg, recs = setup(tmp_path, mode)
    k = len(recs) // 2
    bad = list(recs)
    bad[k] = bad[k][:-1] + bytes([bad[k][-1] ^ 1])  # a timestamp/price bit, not a flag
    s = run(tmp_path, pkts, datagrams(cfg, bad))
    assert (s["match"], s["first_mismatch"], s["mismatched_positions"]) == (0, k, 1)


@pytest.mark.parametrize("change", ["missing_last", "extra", "missing_middle"])
def test_record_count(tmp_path: Path, change: str) -> None:
    pkts, cfg, recs = setup(tmp_path)
    k = len(recs) // 3
    got = {
        "missing_last": recs[:-1],
        "extra": [*recs, recs[-1]],
        "missing_middle": recs[:k] + recs[k + 1 :],
    }[change]
    s = run(tmp_path, pkts, datagrams(cfg, got))
    assert s["match"] == 0
    assert s["first_mismatch"] == {"missing_last": len(recs) - 1, "extra": len(recs)}.get(change, k)


def test_lost_datagram(tmp_path: Path) -> None:
    pkts, cfg, recs = setup(tmp_path)
    dgs = datagrams(cfg, recs)
    stats_i = next(i for i, d in enumerate(dgs) if P.parse(d).kind == P.STATS)
    del dgs[stats_i]  # even a lost stats datagram fails the run: records may hide in a loss
    s = run(tmp_path, pkts, dgs)
    assert (s["lost_datagrams"], s["first_mismatch"], s["match"]) == (1, -1, 0)


def test_host_reordering_is_undone(tmp_path: Path) -> None:
    pkts, cfg, recs = setup(tmp_path)
    dgs = datagrams(cfg, recs)
    dgs[3], dgs[4] = dgs[4], dgs[3]
    assert run(tmp_path, pkts, dgs)["match"] == 1


def test_duplicate_datagram(tmp_path: Path) -> None:
    pkts, cfg, recs = setup(tmp_path)
    dgs = datagrams(cfg, recs)
    rec_i = next(i for i, d in enumerate(dgs) if P.parse(d).kind == P.BBO)
    s = run(tmp_path, pkts, [*dgs[: rec_i + 1], dgs[rec_i], *dgs[rec_i + 1 :]])
    assert (s["duplicate_datagrams"], s["match"]) == (1, 1)
    other = P.packet(P.BBO, P.parse(dgs[rec_i]).pkt_seq, [recs[-1]])
    with pytest.raises(ValueError, match="different content"):
        run(tmp_path, pkts, [*dgs, other])


def test_log_must_start_at_one_ack(tmp_path: Path) -> None:
    pkts, cfg, recs = setup(tmp_path)
    dgs = datagrams(cfg, recs)
    with pytest.raises(ValueError, match="config ack"):
        run(tmp_path, pkts, dgs[1:])
    with pytest.raises(ValueError, match="reconfigured"):
        run(tmp_path, pkts, [*dgs, dgs[0]])


def test_window_comes_from_the_ack(tmp_path: Path) -> None:
    """Another window gives other out-of-window flags: compare must take the window from
    the ack's lo_price, not assume one."""
    pkts, cfg, recs = setup(tmp_path)
    base2 = BASE_TICKS - 2048  # window's top edge at the base: resting asks fall out
    shifted = P.Config(symbol=cfg.symbol, lo_price=P.window_lo_price(base2))
    recs2, _ = P.expected(iter_raw(pkts.open("rb")), shifted, BoundedBook(base_ticks=base2))
    assert recs2 != recs
    assert run(tmp_path, pkts, datagrams(shifted, recs2))["match"] == 1
    assert run(tmp_path, pkts, datagrams(shifted, recs))["match"] == 0
