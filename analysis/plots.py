"""Regenerate every plot in docs/media/ from data/results/. Run by `make plots`."""

from __future__ import annotations

import csv
from pathlib import Path

import matplotlib

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


def board_vs_model(csv_path: Path, runs_csv: Path, run_name: str, out: Path) -> None:
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
        "AAPL best bid / ask, 2019-10-18: FPGA on the board vs golden model (last BBO per minute)",
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


if __name__ == "__main__":
    main()
