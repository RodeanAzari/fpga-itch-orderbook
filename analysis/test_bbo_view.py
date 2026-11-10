"""Tests for analysis/bbo_view.py's logic (the curses loop only paints what these return).

Why: the demo's headline is "matched vs golden model N/N". If the counter, the loss
counters or the log follower were wrong, the demo would show a false claim live. So the
follower must survive frames cut by the writer, the counter must count by cause message
(not arrival position), a mismatch or loss must show, and a new config must reset the view.
"""

from __future__ import annotations

import io
from pathlib import Path

import pytest

from analysis import bbo_view as V
from model import protocol as P
from model.book import Bbo


def bbo(seq: int, bid: int, ask: int, bsz: int = 100, asz: int = 200, stale: bool = False):
    b = Bbo(bid_price=bid, bid_size=bsz, ask_price=ask, ask_size=asz, ts=seq * 1000)
    return P.bbo_record(b, seq, stale)


CFG = P.Config(lo_price=P.window_lo_price(23455))
EXPECTED = [bbo(s, 2_340_000 + s * 100, 2_341_000 + s * 100) for s in range(1, 51)]


def view() -> V.View:
    v = V.View(lambda cfg: EXPECTED if cfg == CFG else [])
    v.apply(P.packet(P.CONFIG_ACK, 0, [CFG.record()]))
    return v


def framed(payloads: list[bytes]) -> bytes:
    return b"".join(len(p).to_bytes(2) + p for p in payloads)


def test_log_tail_survives_cut_frames() -> None:
    payloads = [P.packet(P.BBO, i, EXPECTED[i : i + 3]) for i in range(1, 6)]
    data = framed(payloads)
    f = io.BytesIO()
    tail = V.LogTail(f)
    got: list[bytes] = []
    for cut in (1, 2, 3, 150, 151, len(data) - 1, len(data)):  # inside prefixes and bodies
        pos = f.tell()
        f.seek(0, io.SEEK_END)
        f.write(data[f.tell() : cut])
        f.seek(pos)
        got += tail.poll()
    assert got == payloads and tail.buf == b""


def test_counts_match_by_cause_message_not_position() -> None:
    v = view()
    v.apply(P.packet(P.BBO, 2, EXPECTED[10:20]))  # arrives first
    v.apply(P.packet(P.BBO, 1, EXPECTED[0:10]))
    assert (v.seen, v.matched, v.lost_datagrams) == (20, 20, 0)
    assert (v.bid, v.ask) == (2_340_000 + 1000, 2_341_000 + 1000)  # last applied: seq 10


def test_mismatch_and_unknown_records_are_not_matched() -> None:
    v = view()
    wrong = EXPECTED[0][:4] + (1).to_bytes(4) + EXPECTED[0][8:]
    v.apply(P.packet(P.BBO, 1, [wrong, EXPECTED[1], bbo(999, 1, 2)]))
    assert (v.seen, v.matched) == (3, 1)


def test_losses_and_stale_show() -> None:
    v = view()
    v.apply(P.packet(P.BBO, 1, EXPECTED[:2]))
    v.apply(P.packet(P.BBO, 4, [bbo(3, 1, 2, stale=True)]))  # pkt_seq 2, 3 lost
    stats = [0] * P.N_STATS
    stats[P.STATS_NAMES.index("rx_fifo_overflow")] = 2
    stats[P.STATS_NAMES.index("mold_gaps")] = 1
    v.apply(P.packet(P.STATS, 5, [x.to_bytes(8) for x in stats]))
    assert v.lost_datagrams == 2 and v.stale
    text = "".join(t for line in V.render(v, 0, 100, 30) for t, _ in line)
    assert "STALE" in text and "input frames lost 3" in text and "output datagrams lost 2" in text


def test_new_config_resets_the_view() -> None:
    v = view()
    v.apply(P.packet(P.BBO, 1, EXPECTED[:5]))
    v.apply(P.packet(P.CONFIG_ACK, 0, [CFG.record()]))
    assert (v.seen, v.matched, v.max_pkt_seq, len(v.history)) == (0, 0, 0, 0)
    assert len(v.by_seq) == len(EXPECTED)


def test_chart_geometry() -> None:
    hist = [(100, 200), (150, 250), (100, 0)]  # last update: ask side empty
    rows, lo, hi = V.chart(hist, rows=5, cols=8)
    assert len(rows) == 5 and all(len(r) == 8 for r in rows) and (lo, hi) == (100, 250)
    # fewer updates than columns: right-aligned, oldest first
    assert rows[0][6] == "a" and rows[4][5] == "b"  # top = highest price, bottom = lowest
    assert "a" not in "".join(r[7] for r in rows)  # empty side not drawn
    assert V.chart([], 3, 4) == (["    "] * 3, 0, 0)
    many = [(1000 + i, 2000 + i) for i in range(1000)]
    rows, _, _ = V.chart(many, rows=10, cols=20)
    assert rows[0][19] == "a" and rows[-1][0] == "b"  # newest right, rising prices


def test_plot_marks_bid_and_ask_in_one_cell() -> None:
    rows, _, _ = V.plot([(100, 101), (100, 300)], rows=3)  # 1-tick spread on a 200 range
    assert rows[2][0] == "x" and rows[0][1] == "a" and rows[2][1] == "b"


def m(h: int, mi: int) -> int:
    return h * 60 + mi


def test_day_chart_puts_prices_at_their_time() -> None:
    cols = 160  # 16 h axis: one column per 6 minutes
    minutes = {m(4, 0): (100, 200), m(12, 0): (300, 310), m(19, 59): (200, 210)}
    rows, lo, hi = V.day_chart(minutes, rows=11, cols=cols)
    assert (lo, hi) == (100, 310) and all(len(r) == cols for r in rows)
    drawn = {c for c in range(cols) for r in rows if r[c] in "abx"}
    assert drawn == {0, 80, 159}  # 04:00, 12:00, 19:59
    assert rows[0][80] == "a" and rows[10][0] == "b"  # noon is the high, 04:00 the low
    # the open (09:30) and close (16:00) columns are marked where nothing is drawn
    assert rows[5][(m(9, 30) - m(4, 0)) // 6] == "|" and rows[5][(m(16, 0) - m(4, 0)) // 6] == "|"
    # the last BBO in a column's span wins
    rows, _, hi = V.day_chart({m(12, 0): (300, 310), m(12, 5): (500, 510)}, 11, cols)
    assert hi == 510 and rows[0][80] == "a"


def test_view_keeps_each_minutes_last_prices() -> None:
    v = view()
    t = 12 * 3600 * 10**9  # noon

    def rec(ns: int, bid: int, ask: int) -> bytes:
        return P.bbo_record(Bbo(ts=ns, bid_price=bid, bid_size=1 if bid else 0,
                                ask_price=ask, ask_size=1 if ask else 0), 1, False)  # fmt: skip

    v.apply(
        P.packet(P.BBO, 1, [rec(t, 100, 200), rec(t + 10**9, 0, 210), rec(t + 61 * 10**9, 90, 0)])
    )
    assert v.minutes == {720: (100, 210), 721: (90, 0)}  # an empty side keeps the minute's last


def test_time_axis_labels() -> None:
    ax = V.time_axis(160)
    assert len(ax) == 160 and ax[0:2] == "04" and ax[80:82] == "12" and "20" in ax[-3:]


def test_render_fits_the_terminal() -> None:
    v = view()
    for i in range(0, len(EXPECTED), 22):  # at most 22 BBO records per datagram
        v.apply(P.packet(P.BBO, 1 + i // 22, EXPECTED[i : i + 22]))
    for w, h in [(120, 40), (60, 12), (20, 5), (5, 2)]:
        lines = V.render(v, 1234.0, w, h)
        assert len(lines) <= h
        assert all(sum(len(t) for t, _ in line) < w for line in lines)
    text = "".join(t for line in V.render(v, 0, 120, 40) for t, _ in line)
    assert "50/50" in text and "234.50" in text  # bid of the last record: 2,345,000 / 10^4


def test_waiting_before_ack() -> None:
    assert "waiting" in V.render(V.View(lambda c: []), 0, 80, 24)[0][0][0]


@pytest.mark.parametrize(
    ("ns", "s"),
    [
        (0, "00:00:00.000"),
        (34_200_000_000_001, "09:30:00.000"),
        (57_599_999_000_000, "15:59:59.999"),
    ],
)
def test_clock(ns: int, s: str) -> None:
    assert V.clock(ns) == s


def test_expected_is_cached(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    from model import generator
    from model.moldudp64 import packetize

    pk = tmp_path / "p.pkts"
    pk.write_bytes(framed(list(packetize(generator.generate(seed=3, count=500), b"S" * 10))))
    cfg = P.Config(symbol=generator.DEFAULT.stock, lo_price=P.window_lo_price(10_000))
    calls = []
    real = P.expected
    monkeypatch.setattr(P, "expected", lambda *a: calls.append(1) or real(*a))
    first = V.expected_cached(pk)(cfg)
    second = V.expected_cached(pk)(cfg)
    assert first == second and len(first) > 0 and len(calls) == 1


def test_empty_side_reads_empty() -> None:
    v = view()
    v.apply(P.packet(P.BBO, 1, [bbo(1, 0, 2_341_000, bsz=0)]))
    quote_line = "".join(t for t, _ in V.render(v, None, 100, 30)[1])
    assert "BID    empty" in quote_line and "234.10 x 200" in quote_line
    assert "spread -" in quote_line
    assert len(V.quote(0, 0)) == len(V.quote(2_341_000, 200))  # the layout does not jump


def test_charts_use_the_full_width() -> None:
    """Every chart row and the time axis end exactly one column short of the terminal
    width (the last column is never written), so nothing is cut at the right edge."""
    v = view()
    v.apply(P.packet(P.BBO, 1, EXPECTED[:22]))
    lines = V.render(v, None, 120, 40)
    chart_rows = [ln for ln in lines if ln and ln[0][0].endswith("|")]
    assert chart_rows and all(sum(len(t) for t, _ in ln) == 119 for ln in chart_rows)
    axis = next("".join(t for t, _ in ln) for ln in lines if ln and "04" in ln[0][0])
    assert len(axis) == 119 and axis.rstrip().endswith("20")
