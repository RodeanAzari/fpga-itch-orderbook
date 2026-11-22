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


def capacity(w8: Path, w64: Path, out: Path) -> None:
    """Injector capacity on the board (ADR 0014, 0015): messages/s at DATA_W 8 and 64 on
    the same ROM image, against the 100 Mb/s wire limit for those same packets."""
    from analysis.injector_image import image  # the committed fixture, deterministic

    ns = 8  # 125 MHz core clock
    im = image()
    payload = sum(len(p) for p in im.pkts)
    # per frame on the wire: preamble+SFD 8, Ethernet 14, IPv4 20, UDP 8, FCS 4, IFG 12
    wire_bits = sum(8 * (max(len(p) + 42, 60) + 4 + 8 + 12) for p in im.pkts)
    wire_mps = im.messages / (wire_bits / 100e6) / 1e6

    def cycles(path: Path) -> int:
        with open(path) as f:
            runs = {int(r["inj_cycles"]) for r in csv.DictReader(f) if r["build"].startswith("sf")}
        if len(runs) != 1:
            raise ValueError(f"{path}: runs disagree on inj_cycles: {sorted(runs)}")
        return runs.pop()

    bars = [("DATA_W 8", cycles(w8)), ("DATA_W 64", cycles(w64))]
    mps = [im.messages / (c * ns * 1e-9) / 1e6 for _, c in bars]
    fig, ax = plt.subplots(figsize=(8, 4.6))
    fig.patch.set_facecolor(SURFACE)
    _style(ax)
    xs = range(len(bars))
    ax.bar(xs, mps, color=BID, width=0.5, edgecolor=SURFACE, linewidth=2)
    for x, (_, c), m in zip(xs, bars, mps, strict=True):
        note = f"{m:.2f} M msg/s\n{c:,} cycles, {payload / c:.2f} B/cycle"
        ax.text(x, m + 0.3, note, ha="center", va="bottom", color=INK, fontsize=9)
    ax.axhline(wire_mps, color=MUTED, linewidth=1.2, linestyle=(0, (4, 3)))
    ax.text(0.5, wire_mps + 0.25, f"100 Mb/s wire limit, same packets:\n"
            f"{wire_mps:.2f} M msg/s (computed)", ha="center", color=MUTED, fontsize=8)  # fmt: skip
    ax.set_xticks(list(xs))
    ax.set_xticklabels([f"{name}\n(feed path width, bits)" for name, _ in bars], color=INK)
    ax.set_ylim(0, max(mps) * 1.25)
    ax.set_ylabel("Messages per second (millions)", color=INK)
    title = (f"Pipeline capacity on the Arty A7: on-chip injector, {im.messages:,} AAPL "
             f"messages ({payload:,} B), store-and-forward")  # fmt: skip
    ax.set_title(title, color=INK, loc="left", fontsize=10)
    fig.tight_layout()
    fig.savefig(out, dpi=150)
    plt.close(fig)
    print(f"wrote {out}")


def ila_frame(mii_csv: Path, core_csv: Path, out: Path) -> None:
    """One feed frame on the board through MII -> eth_rx -> udp_rx -> decoder (two ILA
    cores, separate clocks, so separate time axes): the SFD on the MII nibbles, the whole
    frame through the core, and the header-to-payload boundary byte by byte."""
    from analysis import ila_frame as F

    mii, core = F.load(mii_csv, F.MII_ROLES), F.load(core_csv, F.CORE_ROLES)
    _, sfd, _ = F.mii_frame(mii)
    _, f0, f1 = F.stream(core, "f", 0)
    _, u0, u1 = F.stream(core, "u", f0)
    c = core.cols
    rec = [j for j in range(u0, len(c["r_valid"])) if c["r_valid"][j] and c["r_ready"][j]]

    fig, axes = plt.subplots(3, 1, figsize=(10, 7.6), gridspec_kw={"height_ratios": [1, 1, 1.2]})
    fig.patch.set_facecolor(SURFACE)

    def lanes(ax: plt.Axes, names: list[str]) -> None:
        _style(ax)
        ax.grid(False)
        ax.set_yticks(range(len(names)))
        ax.set_yticklabels(names, color=INK, fontsize=8)
        ax.set_ylim(len(names) - 0.4, -0.6)
        ax.spines["left"].set_visible(False)
        ax.tick_params(axis="y", length=0)

    def bus(ax: plt.Axes, y: int, xs: list[float], vals: list[str], w: float, color: str) -> None:
        for x, v in zip(xs, vals, strict=True):
            ax.add_patch(plt.Rectangle((x, y - 0.3), w, 0.6, facecolor=color, alpha=0.18,
                                       edgecolor=color, linewidth=1))  # fmt: skip
            ax.text(x + w / 2, y, v, ha="center", va="center", fontsize=7, color=INK)

    def bit(ax: plt.Axes, y: int, xs: list[float], vals: list[int], color: str) -> None:
        ax.step(xs, [y + 0.3 - 0.6 * v for v in vals], where="post", color=color, linewidth=2)

    # (a) MII: preamble -> SFD -> first destination-MAC nibbles, 40 ns per sample
    ax = axes[0]
    lanes(ax, ["rx_dv", "rxd (nibble)"])
    js = list(range(sfd - 10, sfd + 14))
    t = [(j - sfd) * 40 for j in js]
    bit(ax, 0, t + [t[-1] + 40], [mii.cols["dv"][j] for j in js] + [mii.cols["dv"][js[-1]]], BID)
    bus(ax, 1, t, [f"{mii.cols['rxd'][j]:X}" for j in js], 40, BID)
    ax.axvspan(0, 80, color=ASK, alpha=0.12, linewidth=0)
    ax.set_ylim(1.5, -1.0)
    ax.text(40, -0.6, "SFD 0xD5 (5, D: low nibble first)", ha="center", fontsize=8, color=INK)
    ax.set_xlim(t[0], t[-1] + 40)
    ax.set_xlabel("ns from the SFD's first nibble (MII rx_clk, 25 MHz)", color=INK, fontsize=9)
    ax.set_title("(a) MII receive: preamble, SFD, then the destination MAC 01:00:5e:01:01:01",
                 color=INK, loc="left", fontsize=10)  # fmt: skip

    # (b) the whole frame through the core, 8 ns per sample
    ax = axes[1]
    lanes(ax, ["eth_rx out (FIFO)", "udp_rx payload", "decoder records"])
    us = 8 / 1000
    ax.broken_barh([((f0 - f0) * us, (f1 - f0 + 1) * us)], (-0.3, 0.6), color=BID, alpha=0.8)
    ax.broken_barh([((u0 - f0) * us, (u1 - u0 + 1) * us)], (0.7, 0.6), color=ASK, alpha=0.8)
    ax.vlines([(j - f0) * us for j in rec], 1.7, 2.3, color=INK, linewidth=1)
    ax.text((f1 - f0) * us, -0.45, f"{f1 - f0 + 1:,} bytes, one per cycle", ha="right",
            fontsize=8, color=INK)  # fmt: skip
    ax.text((u1 - f0) * us, 0.55, f"{u1 - u0 + 1:,} bytes (MoldUDP64 packet)", ha="right",
            fontsize=8, color=INK)  # fmt: skip
    ax.text((rec[-1] - f0) * us, 1.55, f"{len(rec)} records", ha="right", fontsize=8,
            color=INK)  # fmt: skip
    ax.set_xlim(-0.3, (max(f1, u1, rec[-1]) - f0) * us + 0.5)
    ax.set_xlabel("µs from eth_rx's first byte (core clock, 125 MHz)", color=INK, fontsize=9)
    ax.set_title("(b) The frame through the core, released by eth_rx after its FCS checked",
                 color=INK, loc="left", fontsize=10)  # fmt: skip

    # (c) headers -> payload, byte by byte: udp_rx passes the payload one cycle behind
    ax = axes[2]
    lanes(ax, ["eth_rx out", "udp_rx payload"])
    ax.set_ylim(1.5, -1.0)
    js = list(range(f0 + 36, f0 + 68))
    x = [(j - f0) * 8 for j in js]
    fv = [j for j in js if c["f_valid"][j] and c["f_ready"][j]]
    bus(ax, 0, [(j - f0) * 8 for j in fv], [f"{c['f_data'][j]:02X}" for j in fv], 8, BID)
    uv = [j for j in js if c["u_valid"][j] and c["u_ready"][j]]
    bus(ax, 1, [(j - f0) * 8 for j in uv], [f"{c['u_data'][j]:02X}" for j in uv], 8, ASK)
    ax.axvspan((f0 + 42 - f0) * 8, (f0 + 52 - f0) * 8, color=GRID, alpha=0.6, linewidth=0)
    ax.text(47 * 8, -0.6, 'MoldUDP64 session "20191018AA"', ha="center", fontsize=8, color=INK)
    ax.text(39 * 8, -0.6, "UDP header: not passed on", ha="center", fontsize=8, color=MUTED)
    ax.set_xlim(x[0], x[-1] + 8)
    ax.set_xlabel("ns from eth_rx's first byte (8 ns per byte)", color=INK, fontsize=9)
    ax.set_title("(c) Headers to payload: the MoldUDP64 header leaves udp_rx one cycle later",
                 color=INK, loc="left", fontsize=10)  # fmt: skip

    fig.suptitle("One AAPL feed frame on the Arty A7, captured by two ILA cores "
                 "(checked byte for byte: analysis/ila_frame.py)", color=INK, x=0.01,
                 ha="left", fontsize=11)  # fmt: skip
    fig.tight_layout()
    fig.savefig(out, dpi=150)
    plt.close(fig)
    print(f"wrote {out}")


def feed_wave(path: Path, out: Path, n_cycles: int = 30) -> None:
    """One real MoldUDP64 packet through feed_frontend at DATA_W 64 (simulation, checked
    against the model by tb/cocotb/test_feed_wave.py): its 64-bit words, the deframer's
    messages in the lanes they arrived in, the aligner's with every message in lane 0, and
    the decoder's records. Each byte is a cell: lane down, cycle across."""
    rows = list(csv.DictReader(path.open()))[:n_cycles]
    c0 = int(rows[0]["cycle"])
    pkt = b""  # the whole packet: accepted input beats up to its tlast
    with path.open() as f:
        for r in csv.DictReader(f):
            if r["s_tvalid"] == r["s_tready"] == "1":
                keep = bin(int(r["s_tkeep"], 16)).count("1")
                pkt += int(r["s_tdata"], 16).to_bytes(8, "little")[:keep]
                if r["s_tlast"] == "1":
                    break
    seq0 = int.from_bytes(pkt[10:18])
    owner: list[tuple[str, int]] = [("hdr", 0)] * 20  # packet byte -> (role, message seq)
    k, s = 20, seq0
    while k + 2 <= len(pkt) and len(owner) < len(pkt):
        n = int.from_bytes(pkt[k : k + 2])
        owner += [("len", s)] * 2 + [("msg", s)] * n
        k, s = k + 2 + n, s + 1

    def hue(seq: int) -> str:
        return BID if seq % 2 else ASK

    fig, axes = plt.subplots(
        4, 1, figsize=(11, 9.4), sharex=True, gridspec_kw={"height_ratios": [3, 3, 3, 1]}
    )
    fig.patch.set_facecolor(SURFACE)

    def cell(ax: plt.Axes, x: int, lane: int, byte: int, color: str | None, seq: int = -1) -> None:
        """seq >= 0: the message's first byte (its ITCH type), labelled with its sequence."""
        face, alpha = (color, 0.22) if color else (GRID, 0.9)
        ax.add_patch(plt.Rectangle((x - 0.48, lane - 0.45), 0.96, 0.9, facecolor=face,
                                   alpha=alpha if seq < 0 else 0.5, edgecolor="none"))  # fmt: skip
        if seq < 0:
            ax.text(x, lane, f"{byte:02x}", ha="center", va="center", fontsize=6, color=INK,
                    family="monospace")  # fmt: skip
            return
        ax.text(x - 0.22, lane, chr(byte), ha="center", va="center", fontsize=8, color=INK,
                fontweight="bold")  # fmt: skip
        ax.text(x + 0.2, lane + 0.05, str(seq), ha="center", va="center", fontsize=5, color=INK)

    def lanes_axis(ax: plt.Axes, title: str) -> None:
        _style(ax)
        ax.grid(False)
        ax.set_ylim(7.6, -0.6)
        ax.set_yticks(range(8))
        ax.set_yticklabels([f"lane {i}" for i in range(8)], fontsize=7)
        ax.tick_params(axis="y", length=0)
        ax.spines["left"].set_visible(False)
        ax.set_title(title, color=INK, loc="left", fontsize=10)

    def wait(ax: plt.Axes, x: int) -> None:
        dash = (0, (2, 2))
        ax.add_patch(plt.Rectangle((x - 0.48, -0.45), 0.96, 7.9, facecolor="none",
                                   edgecolor=MUTED, linestyle=dash, linewidth=0.8))  # fmt: skip
        ax.text(x, 3.5, "tready low: wait", ha="center", va="center", rotation=90, fontsize=6,
                color=MUTED)  # fmt: skip

    # (a) the packet's words; every byte attributed from the packet's own length prefixes
    ax = axes[0]
    lanes_axis(ax, "(a) feed_frontend input: the MoldUDP64 packet, 8 bytes per cycle "
               "(grey: header and length prefixes)")  # fmt: skip
    beat = 0
    for r in rows:
        x = int(r["cycle"]) - c0
        if r["s_tvalid"] == "1" and r["s_tready"] == "0":
            wait(ax, x)
            continue
        if r["s_tvalid"] != "1":
            continue
        for lane, byte in enumerate(int(r["s_tdata"], 16).to_bytes(8, "little")):
            i = 8 * beat + lane
            if i >= len(owner):
                break
            role, sq = owner[i]
            first = role == "msg" and owner[i - 1][0] == "len"
            cell(ax, x, lane, byte, hue(sq) if role == "msg" else None, sq if first else -1)
        beat += 1

    def stream(ax: plt.Axes, p: str) -> None:
        prev = seq0 - 1  # the window opens inside the previous packet's last message
        for r in rows:
            x = int(r["cycle"]) - c0
            if r[f"{p}_tvalid"] == "1" and r[f"{p}_tready"] == "0":
                wait(ax, x)
            if not (r[f"{p}_tvalid"] == r[f"{p}_tready"] == "1"):
                continue
            keep = int(r[f"{p}_tkeep"], 16)
            data = int(r[f"{p}_tdata"], 16).to_bytes(8, "little")
            sq = int(r[f"{p}_seq"])
            start, prev = sq != prev, sq
            lo = (keep & -keep).bit_length() - 1
            for lane in range(8):
                if keep >> lane & 1:
                    cell(ax, x, lane, data[lane], hue(sq), sq if start and lane == lo else -1)

    lanes_axis(axes[1], "(b) Deframer output: length prefixes gone, each message still in "
               "the lanes it arrived in")  # fmt: skip
    stream(axes[1], "g")
    lanes_axis(axes[2], "(c) Aligner output: every message starts in lane 0, so the decoder "
               "reads each field at a fixed offset")  # fmt: skip
    stream(axes[2], "a")

    ax = axes[3]
    _style(ax)
    ax.grid(False)
    ax.set_yticks([])
    ax.spines["left"].set_visible(False)
    ax.set_ylim(-0.6, 1)
    ax.set_title("(d) Decoder records (one per cycle at most; type and MoldUDP64 sequence)",
                 color=INK, loc="left", fontsize=10)  # fmt: skip
    for r in rows:
        if r["m_valid"] == r["m_ready"] == "1":
            x = int(r["cycle"]) - c0
            ax.vlines(x, 0, 0.6, color=hue(int(r["m_seq"])), linewidth=2.5)
            ax.text(x, -0.15, f"{r['m_type']} {r['m_seq']}", ha="center", va="top", fontsize=7,
                    color=INK)  # fmt: skip
    ax.set_xlim(-0.7, len(rows) - 0.3)
    ax.set_xlabel(f"cycle (8 ns, 125 MHz) from the packet's first word; the first {len(rows)} "
                  "cycles. Bold: a message's type byte, with its MoldUDP64 sequence",
                  color=INK, fontsize=9)  # fmt: skip
    n = int.from_bytes(pkt[18:20])
    fig.suptitle(f"One AAPL MoldUDP64 packet (sequence {seq0}, {n} messages) splitting into "
                 "messages across 64-bit words (simulation; every record = model)", color=INK,
                 x=0.01, ha="left", fontsize=11)  # fmt: skip
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
    capacity(
        RESULTS / "2026-11-13_injector_board.csv",
        RESULTS / "2026-11-15_injector_board_w64.csv",
        MEDIA / "2026-11-15_phase-e_board-capacity-w8-vs-w64.png",
    )
    ila_frame(
        RESULTS / "2026-11-21_ila_frame_board_mii.csv",
        RESULTS / "2026-11-21_ila_frame_board_core.csv",
        MEDIA / "2026-11-21_phase-b_ila-frame-mii-to-decoder.png",
    )
    feed_wave(
        RESULTS / "2026-11-22_feed_wave_w64.csv",
        MEDIA / "2026-11-22_phase-c_packet-to-messages-w64.png",
    )


if __name__ == "__main__":
    main()
