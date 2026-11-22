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


def setup(
    tmp_path: Path, mode: int = 0, gen: generator.Config = generator.DEFAULT
) -> tuple[Path, P.Config, list[bytes]]:
    msgs = generator.generate(seed=11, count=2000, cfg=gen)
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


def run(tmp_path: Path, pkts: Path, dgs: list[bytes], ct: bool = False) -> dict[str, int]:
    summary, report = compare(write_framed(tmp_path / "log.bin", dgs), pkts, cut_through=ct)
    first = summary["first_mismatch"]  # the record report leads; counter lines follow it
    assert (
        report[:1] == [f"first mismatch at record {first}:"]
        if first >= 0
        else not any("mismatch" in x for x in report)
    )
    return summary


@pytest.mark.parametrize("mode", [0, 1])
def test_match(tmp_path: Path, mode: int) -> None:
    pkts, cfg, recs = setup(tmp_path, mode)
    s = run(tmp_path, pkts, datagrams(cfg, recs))
    assert s["match"] == 1 and s["fpga_records"] == s["expected_records"] == len(recs)
    assert s["lost_datagrams"] == 0 and s["first_mismatch"] == -1
    assert (s["received_wrong"], s["missing_records"]) == (0, 0)


@pytest.mark.parametrize("mode", [0, 1])
def test_one_record_wrong(tmp_path: Path, mode: int) -> None:
    pkts, cfg, recs = setup(tmp_path, mode)
    k = len(recs) // 2
    bad = list(recs)
    i = 7  # a price bit, not a flag: bid_px (BBO) or itch_ts (trace)
    bad[k] = bad[k][:i] + bytes([bad[k][i] ^ 1]) + bad[k][i + 1 :]
    s = run(tmp_path, pkts, datagrams(cfg, bad))
    assert (s["match"], s["first_mismatch"], s["mismatched_positions"]) == (0, k, 1)


def test_times_are_not_compared(tmp_path: Path) -> None:
    """t0/t1/t2 are the FPGA's measurements (ADR 0011): any value matches the model's 0,
    while the byte before them (mold_seq) still counts."""
    pkts, cfg, recs = setup(tmp_path)
    rng = random.Random(3)
    timed = [r[:40] + rng.randbytes(24) for r in recs]
    s = run(tmp_path, pkts, datagrams(cfg, timed))
    assert s["match"] == 1 and s["unflagged_wrong"] == s["received_wrong"] == 0
    k = len(recs) // 2
    timed[k] = timed[k][:39] + bytes([timed[k][39] ^ 1]) + timed[k][40:]
    s = run(tmp_path, pkts, datagrams(cfg, timed))
    assert (s["match"], s["first_mismatch"]) == (0, k)


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


def _record_dgs(cfg: P.Config, dgs: list[bytes]) -> list[int]:
    kind = P.TRACE if cfg.mode == 1 else P.BBO
    return [i for i, d in enumerate(dgs) if P.parse(d).kind == kind]


@pytest.mark.parametrize("mode", [0, 1])
def test_received_records_after_output_loss(tmp_path: Path, mode: int) -> None:
    """A run that lost output datagrams cannot match, but every record that did arrive is
    still checkable by its cause message's mold_seq. The loss must be explained exactly by
    the lost datagrams: a record skipped, repeated or reordered with no datagram lost before
    it is the board's fault, not the host's, and counts as wrong."""
    pkts, cfg, recs = setup(tmp_path, mode)
    dgs = datagrams(cfg, recs)
    rd = _record_dgs(cfg, dgs)
    lost = [rd[len(rd) // 3], rd[len(rd) // 3 + 1], rd[2 * len(rd) // 3]]  # two adjacent
    n_lost = sum(len(P.parse(dgs[i]).records) for i in lost)
    kept = [d for i, d in enumerate(dgs) if i not in lost]
    s = run(tmp_path, pkts, kept)
    assert (s["match"], s["lost_datagrams"]) == (0, 3)
    assert (s["received_wrong"], s["missing_records"]) == (0, n_lost)
    # The last record datagram lost: nothing after it to place, still only missing.
    s = run(tmp_path, pkts, [d for i, d in enumerate(dgs) if i != rd[-1]])
    assert (s["received_wrong"], s["missing_records"]) == (0, len(P.parse(dgs[rd[-1]]).records))

    def bad_log(rs: list[bytes]) -> dict[str, int]:
        """recs changed as given, the same datagrams lost."""
        d2 = datagrams(cfg, rs)
        return run(tmp_path, pkts, [d for i, d in enumerate(d2) if i not in lost])

    k = len(recs) // 2  # a record well inside a datagram run that lost nothing
    flipped = [*recs[:k], recs[k][:20] + bytes([recs[k][20] ^ 1]) + recs[k][21:], *recs[k + 1 :]]
    assert bad_log(flipped)["received_wrong"] == 1
    assert bad_log([*recs[:k], *recs[k + 1 :]])["received_wrong"] == 1  # skipped, no gap
    assert bad_log([*recs[: k + 1], *recs[k:]])["received_wrong"] == 1  # repeated
    swapped = [*recs[:k], recs[k + 1], recs[k], *recs[k + 2 :]]
    assert bad_log(swapped)["received_wrong"] >= 1
    foreign = recs[k][:8] + (10**12).to_bytes(8) + recs[k][16:]  # no such message
    if mode == 0:
        foreign = recs[k][:32] + (10**12).to_bytes(8) + recs[k][40:]
    # Unplaceable, so the next record also looks skipped: replacement or insertion is ambiguous.
    assert bad_log([*recs[:k], foreign, *recs[k + 1 :]])["received_wrong"] >= 1
    # After a lost datagram, the next record going back to an earlier message is wrong too.
    j = lost[2] + 1
    while P.parse(dgs[j]).kind != P.parse(dgs[lost[2]]).kind:
        j += 1
    first = P.parse(dgs[j])
    back = P.packet(first.kind, first.pkt_seq, [recs[0], *first.records[1:]])
    s = run(tmp_path, pkts, [back if i == j else d for i, d in enumerate(dgs) if i not in lost])
    assert (s["received_wrong"], s["missing_records"]) == (1, n_lost + 1)  # + the replaced one
    # ... or repeating the last record received before the loss.
    before = next(P.parse(dgs[i]) for i in range(lost[2] - 1, 0, -1) if i in rd)
    rep = P.packet(first.kind, first.pkt_seq, [before.records[-1], *first.records])
    s = run(tmp_path, pkts, [rep if i == j else d for i, d in enumerate(dgs) if i not in lost])
    assert (s["received_wrong"], s["missing_records"]) == (1, n_lost)


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


def test_unflagged_wrong(tmp_path: Path) -> None:
    """A wrong BBO is acceptable only with the stale flag; positions shifted by a loss
    must not make correct records count as wrong."""
    # No unknown refs, so the book is never stale and no record is flagged to begin with.
    pkts, cfg, recs = setup(tmp_path, gen=generator.Config(p_unknown_ref=0.0))
    assert not any(int.from_bytes(r[2:4]) & P.STALE for r in recs)
    k = len(recs) // 2

    def stale(r: bytes) -> bytes:
        return r[:2] + (int.from_bytes(r[2:4]) | P.STALE).to_bytes(2) + r[4:]

    def wrong(r: bytes) -> bytes:
        return r[:4] + (int.from_bytes(r[4:8]) + 100).to_bytes(4) + r[8:]  # bid_px + 1 c

    # A record lost mid-run, everything after it wrong and flagged: 0.
    lossy = recs[:k] + [stale(wrong(r)) for r in recs[k + 1 :]]
    assert run(tmp_path, pkts, datagrams(cfg, lossy))["unflagged_wrong"] == 0
    # Records merely missing (output loss): still 0, positions do not matter.
    assert run(tmp_path, pkts, datagrams(cfg, recs[:k] + recs[k + 5 :]))["unflagged_wrong"] == 0
    # One wrong record without the flag, and one BBO the model never produced: 2.
    extra = wrong(recs[k])[:32] + (10**12).to_bytes(8) + recs[k][40:]
    bad = [*recs[:k], wrong(recs[k]), *recs[k + 1 :], extra]
    assert run(tmp_path, pkts, datagrams(cfg, bad))["unflagged_wrong"] == 2


def test_trace_mode_has_no_unflagged_count(tmp_path: Path) -> None:
    pkts, cfg, recs = setup(tmp_path, mode=1)
    assert "unflagged_wrong" not in run(tmp_path, pkts, datagrams(cfg, recs))


def test_cut_through_flag_follows_the_stated_build(tmp_path: Path) -> None:
    """A cut-through bitstream sets bit 4 on every BBO (ADR 0013). The build mode is the
    operator's, not read from the records: a log from either build matches only when
    compared as that build, so a missing or stray flag on the board still fails."""
    pkts, cfg, recs = setup(tmp_path)
    ct_recs, _ = P.expected(
        iter_raw(pkts.open("rb")), cfg, BoundedBook(base_ticks=BASE_TICKS), cut_through=True
    )
    assert all(int.from_bytes(r[2:4]) & P.CUT_THROUGH for r in ct_recs)
    assert run(tmp_path, pkts, datagrams(cfg, ct_recs), ct=True)["match"] == 1
    s = run(tmp_path, pkts, datagrams(cfg, ct_recs))
    assert (s["match"], s["mismatched_positions"]) == (0, len(recs))
    assert run(tmp_path, pkts, datagrams(cfg, recs), ct=True)["match"] == 0
    k = len(recs) // 2  # one record without the flag in a cut-through run
    one = [*ct_recs[:k], recs[k], *ct_recs[k + 1 :]]
    s = run(tmp_path, pkts, datagrams(cfg, one), ct=True)
    assert (s["match"], s["first_mismatch"], s["mismatched_positions"]) == (0, k, 1)


def stats_packet(pkt_seq: int, values: dict[str, int]) -> bytes:
    return P.packet(P.STATS, pkt_seq, [values.get(k, 0).to_bytes(8) for k in P.STATS_NAMES])


def test_feed_counters(tmp_path: Path) -> None:
    """The board's last stats snapshot must equal the model's feed counters: that is the
    hardware evidence for the locate filter on messages that make no record (a whole-day
    replay is mostly other symbols). The expected values are counted here from the feed as
    built, independently of model.protocol.feed_stats."""
    msgs = generator.generate(seed=11, count=2000)
    feed: list[bytes] = []
    other = 0
    unknown = sum(m[:1] not in b"SRAFECXDU" for m in msgs)  # the ITCH subset (ADR 0004)
    for k, m in enumerate(msgs):
        feed.append(m)
        if k % 5 == 1 and m[:1] in (b"A", b"D"):  # the same message for another stock
            feed.append(m[:1] + (7).to_bytes(2) + m[3:])
            other += 1
    assert other > 50 and unknown > 10
    pkts = list(packetize(feed, b"TESTSESS01", max_payload=600))
    pkt_path = write_framed(tmp_path / "pkts.bin", pkts)
    cfg = P.Config(symbol=generator.DEFAULT.stock, lo_price=P.window_lo_price(BASE_TICKS))
    recs, _ = P.expected(pkts, cfg, BoundedBook(base_ticks=BASE_TICKS))
    want = {
        "mold_packets": len(pkts),
        "itch_unknown_types": unknown,
        "itch_other_locate": other,
        "itch_directory_matches": 1,
        "locate": 1 << 16 | generator.DEFAULT.locate,
    }
    dgs = [d for d in datagrams(cfg, recs) if P.parse(d).kind != P.STATS]
    dgs = [P.packet(P.parse(d).kind, i, P.parse(d).records) for i, d in enumerate(dgs)]
    # No stats in the log: unknown, not a pass.
    assert run(tmp_path, pkt_path, dgs)["feed_counters_match"] == -1
    # The right snapshot last by pkt_seq wins over a wrong one that arrived after it.
    n = len(dgs)
    s = run(tmp_path, pkt_path, [*dgs, stats_packet(n + 1, want), stats_packet(n, {})])
    assert (s["match"], s["feed_counters_match"]) == (1, 1)
    # One counter off by one fails and is named; the records still match.
    for k in want:
        bad = want | {k: want[k] + 1}
        log = write_framed(tmp_path / "log.bin", [*dgs, stats_packet(n, bad)])
        summary, report = compare(log, pkt_path)
        assert (summary["match"], summary["feed_counters_match"]) == (1, 0)
        assert report == [f"counter {k}: fpga {want[k] + 1} model {want[k]}"]
