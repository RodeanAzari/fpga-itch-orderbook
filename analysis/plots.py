"""Regenerate every plot in docs/media/ from data/results/. Run by `make plots`."""

from __future__ import annotations

import csv
from pathlib import Path

import matplotlib

from analysis.latency import nearest_rank

matplotlib.use("Agg")
import matplotlib.pyplot as plt  # noqa: E402

RESULTS = Path("data/results")
MEDIA = Path("docs/media")
BID, ASK = "#2a78d6", "#eb6834"  # dataviz categorical slots 1, 2 (validated)
INK, MUTED, GRID, SURFACE = "#0b0b0b", "#52514e", "#e4e3df", "#fcfcfb"
PRICE_SCALE = 10_000  # ITCH: 4 implied decimals
HALF_WINDOW = 2048


def _style(ax: plt.Axes) -> None:
    ax.set_facecolor(SURFACE)
    ax.grid(axis="y", color=GRID, linewidth=0.8)
    ax.set_axisbelow(True)
    for side in ("top", "right"):
        ax.spines[side].set_visible(False)
    for side in ("left", "bottom"):
        ax.spines[side].set_color(MUTED)
    ax.tick_params(colors=MUTED, labelcolor=MUTED)


def bbo_day(prefix: str, out: Path) -> None:
    with open(f"{prefix}_summary.csv") as f:
        summary = {row["metric"]: row["value"] for row in csv.DictReader(f)}
    hours, bids, asks, spreads = [], [], [], []
    with open(f"{prefix}_bbo_1min.csv") as f:
        for row in csv.DictReader(f):
            bid_size, ask_size = int(row["bid_size"]), int(row["ask_size"])
            if not (bid_size and ask_size):
                continue  # one-sided book: no meaningful bid/ask pair to draw
            bid, ask = int(row["bid_price"]), int(row["ask_price"])
            hours.append(int(row["minute"]) / 60)
            bids.append(bid / PRICE_SCALE)
            asks.append(ask / PRICE_SCALE)
            spreads.append((ask - bid) / 100)  # cents

    base = int(summary["window_base_ticks"]) / 100
    lo, hi = base - HALF_WINDOW / 100, base + HALF_WINDOW / 100
    fig, (top, bottom) = plt.subplots(
        2, 1, figsize=(10, 6.2), sharex=True, gridspec_kw={"height_ratios": [3, 1.3]}
    )
    fig.patch.set_facecolor(SURFACE)
    for ax in (top, bottom):
        _style(ax)
        for t in (9.5, 16):  # regular session
            ax.axvline(t, color=MUTED, linewidth=0.8, linestyle=(0, (2, 3)))

    top.step(hours, bids, where="post", color=BID, linewidth=1.6, label="Best bid")
    top.step(hours, asks, where="post", color=ASK, linewidth=1.6, label="Best ask")
    top.set_ylabel("Price (USD)", color=INK)
    lo_seen, hi_seen = min(bids), max(asks)
    pad = (hi_seen - lo_seen) * 0.08
    top.set_ylim(lo_seen - pad, hi_seen + pad)
    top.legend(loc="upper left", frameon=False, labelcolor=INK)
    top.text(9.55, hi_seen + pad * 0.4, "open 09:30", color=MUTED, fontsize=8)
    top.text(16.05, hi_seen + pad * 0.4, "close 16:00", color=MUTED, fontsize=8)
    top.set_title(
        f"{summary['symbol']} reconstructed best bid / ask, 2019-10-18 "
        f"(last BBO per minute, golden model)",
        color=INK,
        loc="left",
        fontsize=11,
    )
    window_note = f"Price window ${lo:.2f} to ${hi:.2f} (base ${base:.2f}, ±2,048 ticks)"
    top.text(0.99, 0.03, window_note, transform=top.transAxes, ha="right", color=MUTED, fontsize=8)

    bottom.step(hours, spreads, where="post", color=MUTED, linewidth=1.2)
    bottom.set_yscale("log")
    bottom.set_ylabel("Spread (¢, log)", color=INK)
    bottom.set_xlabel("Time of day (ET, hours)", color=INK)
    bottom.set_xlim(4, 20)
    bottom.set_xticks(range(4, 21, 2))
    bottom.set_xticklabels([f"{h:02d}:00" for h in range(4, 21, 2)])

    fig.tight_layout()
    out.parent.mkdir(parents=True, exist_ok=True)
    fig.savefig(out, dpi=150)
    plt.close(fig)
    print(f"wrote {out}")


def board_vs_model(
    csv_path: Path, runs_csv: Path, run_name: str, out: Path, day: str = "2019-10-18"
) -> None:
    """The board's last BBO per minute drawn over the golden model's, and their difference.
    Minutes with an empty side on either source are left out (no bid/ask pair to draw).
    The record-level totals come from run `run_name` in `runs_csv`."""
    with open(runs_csv) as f:
        run = next(r for r in csv.DictReader(f) if r["run"] == run_name)
    if run["match"] != "1":
        raise ValueError(f"{run_name}: not a matching run")
    hours, mb, ma, bb, ba, db, da = [], [], [], [], [], [], []
    with open(csv_path) as f:
        for row in csv.DictReader(f):
            v = {k: int(x) for k, x in row.items()}
            if not all(v[f"{s}_{k}_size"] for s in ("board", "model") for k in ("bid", "ask")):
                continue
            hours.append(v["minute"] / 60)
            mb.append(v["model_bid_price"] / PRICE_SCALE)
            ma.append(v["model_ask_price"] / PRICE_SCALE)
            bb.append(v["board_bid_price"] / PRICE_SCALE)
            ba.append(v["board_ask_price"] / PRICE_SCALE)
            db.append((v["board_bid_price"] - v["model_bid_price"]) / 100)  # cents
            da.append((v["board_ask_price"] - v["model_ask_price"]) / 100)

    fig, (top, bottom) = plt.subplots(
        2, 1, figsize=(10, 6.2), sharex=True, gridspec_kw={"height_ratios": [3, 1.1]}
    )
    fig.patch.set_facecolor(SURFACE)
    for ax in (top, bottom):
        _style(ax)
        for t in (9.5, 16):
            ax.axvline(t, color=MUTED, linewidth=0.8, linestyle=(0, (2, 3)))
    # golden model: wide, pale; the board on top: thin, full colour. Where they agree the
    # thin line sits inside the wide one.
    top.step(hours, mb, where="post", color=BID, alpha=0.25, linewidth=5, label="Bid, golden model")
    top.step(hours, ma, where="post", color=ASK, alpha=0.25, linewidth=5, label="Ask, golden model")
    top.step(hours, bb, where="post", color=BID, linewidth=1.2, label="Bid, FPGA")
    top.step(hours, ba, where="post", color=ASK, linewidth=1.2, label="Ask, FPGA")
    top.set_ylabel("Price (USD)", color=INK)
    top.legend(loc="upper left", frameon=False, labelcolor=INK, ncol=2, fontsize=9)
    top.set_title(
        f"AAPL best bid / ask, {day}: FPGA on the board vs golden model (last BBO per minute)",
        color=INK,
        loc="left",
        fontsize=11,
    )
    bottom.step(hours, db, where="post", color=BID, linewidth=1.2)
    bottom.step(hours, da, where="post", color=ASK, linewidth=1.2, linestyle=(0, (4, 2)))
    worst = max(map(abs, db + da))
    bottom.set_ylim(-1, 1)
    bottom.set_ylabel("FPGA − model (¢)", color=INK)
    bottom.text(
        0.99, 0.75, f"max |difference| = {worst:g}¢ over {len(hours)} minutes; record "
        f"level: {int(run['fpga_records']):,} / {int(run['expected_records']):,} BBOs identical",
        transform=bottom.transAxes, ha="right", color=MUTED, fontsize=8,
    )  # fmt: skip
    bottom.set_xlabel("Time of day (ET, hours)", color=INK)
    bottom.set_xlim(4, 20)
    bottom.set_xticks(range(4, 21, 2))
    bottom.set_xticklabels([f"{h:02d}:00" for h in range(4, 21, 2)])
    fig.tight_layout()
    fig.savefig(out, dpi=150)
    plt.close(fig)
    print(f"wrote {out}")


def _hist(path: Path, metric: str) -> list[tuple[int, int]]:
    with open(path) as f:
        return [
            (int(r["cycles"]), int(r["count"])) for r in csv.DictReader(f) if r["metric"] == metric
        ]


def latency(before: Path, after: Path, causes: Path, out: Path) -> None:
    """T2-T1 on the board, day 1: CCDF before and after ADR 0012, and the after histogram
    by cause (docs/latency.md budget cases)."""
    ns = 8  # 125 MHz core clock
    fig, (left, right) = plt.subplots(
        1, 2, figsize=(11, 4.6), gridspec_kw={"width_ratios": [1.35, 1]}
    )
    fig.patch.set_facecolor(SURFACE)
    for ax in (left, right):
        _style(ax)
    for path, color, name in ((before, ASK, "before (byte writer paces the book)"),
                              (after, BID, "after ADR 0012 (record FIFO)")):  # fmt: skip
        h = _hist(path, "T2-T1")
        n = sum(c for _, c in h)
        xs, ys, left_over = [], [], n
        for cyc, c in h:
            xs.append(cyc * ns)
            ys.append(left_over / n)  # fraction of BBOs taking >= this value
            left_over -= c
        left.step(xs, ys, where="post", color=color, linewidth=2, label=name)
        left.plot(xs[-1], ys[-1], "o", color=color, markersize=6)
        left.annotate(f"max {xs[-1]:,} ns", (xs[-1], ys[-1]), xytext=(6, 6),
                      textcoords="offset points", color=INK, fontsize=8)  # fmt: skip
    left.set_xscale("log")
    left.set_yscale("log")
    left.set_ylim(5e-7, 2)
    left.set_xlabel("T2 − T1, pipeline latency (ns, log)", color=INK)
    left.set_ylabel("Fraction of BBOs at or above (log)", color=INK)
    left.legend(loc="lower left", frameon=False, labelcolor=INK, fontsize=9)
    left.set_title("Tail, 831,842 BBOs per run", color=INK, loc="left", fontsize=10)

    with open(causes) as f:
        rows = [
            {k: int(v) if k != "msg_type" else v for k, v in r.items()} for r in csv.DictReader(f)
        ]
    label = {14: "out of\nwindow", 17: "best\nstays", 18: "side\nempties", 23: "best\nempties",
             24: "Replace", 29: "Replace,\nbest\nempties"}  # fmt: skip
    per: dict[int, int] = {}
    for r in rows:
        per[r["t2_minus_t1"]] = per.get(r["t2_minus_t1"], 0) + r["count"]
    cyc = sorted(per)
    xs = range(len(cyc))
    right.bar(xs, [per[c] for c in cyc], color=BID, width=0.6, edgecolor=SURFACE, linewidth=2)
    right.set_yscale("log")
    right.set_ylim(1, 3e7)
    for x, c in zip(xs, cyc, strict=True):
        right.text(x, per[c] * 1.4, f"{per[c]:,}", ha="center", color=INK, fontsize=8)
    # percentile markers (nearest rank, as analysis/latency.py and the results CSV)
    vals = sorted(c for c in cyc for _ in range(per[c]))
    marks: dict[int, list[str]] = {}
    for name, pct in (("p50", 50.0), ("p99", 99.0), ("p99.9", 99.9), ("max", 100.0)):
        marks.setdefault(nearest_rank(vals, pct), []).append(name)
    for c, names in marks.items():
        x = cyc.index(c)
        right.annotate(" = ".join(names), (x, per[c] * 4.5), ha="center", color=MUTED,
                       fontsize=8, fontweight="bold")  # fmt: skip
    right.set_xticks(list(xs))
    right.set_xticklabels([f"{c * ns} ns\n{label.get(c, '')}" for c in cyc], fontsize=7.5)
    right.set_ylabel("BBOs (log)", color=INK)
    right.set_title("After: every BBO on its no-wait budget", color=INK, loc="left", fontsize=10)
    title = "AAPL 2019-10-18 on the Arty A7: T2 − T1, message decoded → BBO at the book's output"
    fig.suptitle(title, color=INK, x=0.01, ha="left", fontsize=11)
    fig.tight_layout()
    fig.savefig(out, dpi=150)
    plt.close(fig)
    print(f"wrote {out}")


def ct_vs_sf(hist: Path, out: Path) -> None:
    """T2-T0 on the board, same day on both eth_rx builds (ADR 0013): CCDF per build."""
    ns = 8  # 125 MHz core clock
    fig, ax = plt.subplots(figsize=(9, 4.6))
    fig.patch.set_facecolor(SURFACE)
    _style(ax)
    with open(hist) as f:
        rows = [r for r in csv.DictReader(f) if r["metric"] == "T2-T0"]
    for build, color, name in (("sf", ASK, "store-and-forward (waits for the FCS)"),
                               ("ct", BID, "cut-through")):  # fmt: skip
        h = [(int(r["cycles"]), int(r["count"])) for r in rows if r["build"] == build]
        n = sum(c for _, c in h)
        xs, ys, left_over = [], [], n
        for cyc, c in h:
            xs.append(cyc * ns / 1000)
            ys.append(left_over / n)  # fraction of BBOs taking >= this value
            left_over -= c
        ax.step(xs, ys, where="post", color=color, linewidth=2, label=name)
        p50 = nearest_rank(sorted(c for c, k in h for _ in range(k)), 50.0) * ns / 1000
        ax.axvline(p50, color=color, linewidth=1, linestyle=":")
        ax.annotate(f"p50 {p50:.1f} µs", (p50, 1.3), xytext=(4, 0), textcoords="offset points",
                    color=INK, fontsize=8)  # fmt: skip
    ax.set_yscale("log")
    ax.set_ylim(5e-6, 3)
    ax.set_xlim(0, None)
    ax.set_xlabel("T2 − T0, SFD on the wire → BBO at the book's output (µs)", color=INK)
    ax.set_ylabel("Fraction of BBOs at or above (log)", color=INK)
    ax.legend(loc="lower left", frameon=False, labelcolor=INK, fontsize=9)
    title = ("AAPL 2019-10-18 on the Arty A7, 831,842 BBOs per build: T2 − T0 "
             "(includes 100 Mb/s serialization)")  # fmt: skip
    ax.set_title(title, color=INK, loc="left", fontsize=10)
    fig.tight_layout()
    fig.savefig(out, dpi=150)
    plt.close(fig)
    print(f"wrote {out}")


def main() -> None:
    bbo_day(
        str(RESULTS / "2019-10-18_AAPL"),
        MEDIA / "2026-11-02_phase-a_aapl-bbo-day.png",
    )
    board_vs_model(
        RESULTS / "2026-11-09_board_vs_model_bbo_1min.csv",
        RESULTS / "2026-11-09_full_day_board.csv",
        "day500c",
        MEDIA / "2026-11-09_phase-d_board-vs-model-bbo-day.png",
    )
    board_vs_model(
        RESULTS / "2026-11-10_board_vs_model_bbo_1min_day2.csv",
        RESULTS / "2026-11-10_full_day_board_16way.csv",
        "d2_16way",
        MEDIA / "2026-11-10_phase-d_board-vs-model-bbo-day2.png",
        day="2025-12-12",
    )
    latency(
        RESULTS / "2026-11-11_first_latency_board_hist.csv",
        RESULTS / "2026-11-11_record_fifo_board_hist.csv",
        RESULTS / "2026-11-11_record_fifo_board_causes.csv",
        MEDIA / "2026-11-11_phase-e_board-latency-t2-t1.png",
    )
    ct_vs_sf(
        RESULTS / "2026-11-13_ct_vs_sf_board_hist.csv",
        MEDIA / "2026-11-13_phase-e_board-t2-t0-ct-vs-sf.png",
    )


if __name__ == "__main__":
    main()
