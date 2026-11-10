"""Live view of the board's order book for demos (buildplan §7, `bbo_view`).

Usage (in a real terminal; curses needs a TTY):
    python -m analysis.bbo_view --log data/runs/demo.log --packets data/runs/slice0.pkts

Follows the log host/capture is writing (it flushes per datagram) and shows the board's
best bid/offer as it arrives: the ITCH clock, bid/ask with sizes and spread, the whole day
so far on a fixed 04:00-20:00 time axis, the last updates in a scrolling chart, BBO
updates per second, and every BBO checked against the golden model
(model.protocol.expected over the feed as sent, PKTS = `replay --out`), matched by its
cause message's MoldUDP64 sequence number. Losses come from the board's own stats.

The model's records are computed before the run (for --mode/--lo-price/--symbol, cached
next to PKTS) so the view is live from the first record; if the ack in the log shows
another config, they are recomputed. Start the viewer, then the run
(`scripts/board_run.sh demo bbo 200 0`, ~3 min for the day): it waits for a log written
after it started.

--gif OUT renders a finished log to an animated GIF instead (for the README; the day in
--frames / --fps seconds), with no terminal needed.

Latency (T2-T1) joins the view in Phase E; until then the records' timestamps are 0.
"""

from __future__ import annotations

import argparse
import curses
import hashlib
import os
import time
from collections import deque
from collections.abc import Callable
from dataclasses import dataclass, field
from pathlib import Path
from typing import BinaryIO

from model import protocol as P
from model.bounded import BoundedBook
from model.itch import iter_raw

Expect = Callable[[P.Config], list[bytes]]
Line = list[tuple[str, str]]  # (text, style) segments; styles: see STYLES


class LogTail:
    """Complete u16-BE-length-framed payloads from a file that is still being written;
    a frame cut by the writer's progress stays buffered until the rest arrives."""

    def __init__(self, f: BinaryIO) -> None:
        self.f = f
        self.buf = b""

    def poll(self) -> list[bytes]:
        self.buf += self.f.read()
        out, i, n = [], 0, len(self.buf)
        while n - i >= 2:
            size = int.from_bytes(self.buf[i : i + 2])
            if n - i - 2 < size:
                break
            out.append(self.buf[i + 2 : i + 2 + size])
            i += 2 + size
        self.buf = self.buf[i:]
        return out


def _u48(b: bytes) -> int:
    return int.from_bytes(b)


@dataclass
class View:
    """Everything the screen shows, updated datagram by datagram. Pure: no I/O."""

    expect: Expect
    cfg: P.Config | None = None
    by_seq: dict[int, bytes] = field(default_factory=dict)
    seen: int = 0
    matched: int = 0
    bid: int = 0
    ask: int = 0
    bid_sz: int = 0
    ask_sz: int = 0
    ts: int = 0
    stale: bool = False
    history: deque[tuple[int, int]] = field(default_factory=lambda: deque(maxlen=4000))
    minutes: dict[int, tuple[int, int]] = field(default_factory=dict)  # ITCH minute -> last
    max_pkt_seq: int = 0
    datagrams: int = 0
    stats: dict[str, int] = field(default_factory=dict)

    def apply(self, payload: bytes) -> None:
        d = P.parse(payload)
        if d.kind == P.CONFIG_ACK:
            cfg = P.parse_config(P.packet(P.CONFIG, 0, d.records))
            assert cfg is not None
            self.__init__(self.expect)  # type: ignore[misc]  # a new run starts here
            self.cfg = cfg
            self.by_seq = {int.from_bytes(r[32:40]): r for r in self.expect(cfg)}
            return
        self.datagrams += 1
        self.max_pkt_seq = max(self.max_pkt_seq, d.pkt_seq)
        if d.kind == P.STATS:
            self.stats = {
                k: int.from_bytes(v) for k, v in zip(P.STATS_NAMES, d.records, strict=True)
            }
        elif d.kind == P.BBO:
            for r in d.records:
                self._bbo(r)

    def _bbo(self, r: bytes) -> None:
        self.seen += 1
        self.matched += self.by_seq.get(int.from_bytes(r[32:40])) == r
        flags = int.from_bytes(r[2:4])
        self.stale = bool(flags & P.STALE)
        self.bid, self.ask = int.from_bytes(r[4:8]), int.from_bytes(r[8:12])
        self.bid_sz, self.ask_sz = _u48(r[12:18]), _u48(r[18:24])
        self.ts = _u48(r[24:30])
        self.history.append((self.bid, self.ask))
        m = self.ts // 60_000_000_000
        pb, pa = self.minutes.get(m, (0, 0))  # an empty side keeps the minute's last price
        self.minutes[m] = (self.bid or pb, self.ask or pa)

    @property
    def lost_datagrams(self) -> int:
        return self.max_pkt_seq - self.datagrams


def clock(ns: int) -> str:
    """ITCH timestamp (ns since midnight) as HH:MM:SS.mmm."""
    s, ms = divmod(ns // 1_000_000, 1000)
    return f"{s // 3600:02d}:{s // 60 % 60:02d}:{s % 60:02d}.{ms:03d}"


def price(p: int) -> str:
    return f"{p / 10_000:8.2f}" if p else "       -"


def quote(p: int, size: int) -> str:
    return f"{price(p)} x {size:<7,}" if p else f"{'empty':>8}{'':10}"


def plot(points: list[tuple[int, int] | None], rows: int) -> tuple[list[str], int, int]:
    """One column per point (bid, ask), or None for an empty column. Rows are scaled to the
    drawn prices, top row = highest. Cells: 'b' bid, 'a' ask, 'x' both in one cell (the
    spread is below one row), ' ' empty. Empty sides (price 0) are not drawn. Returns the
    rows and the price range they span (0, 0 if nothing is drawn)."""
    grid = [[" "] * len(points) for _ in range(rows)]
    prices = [p for pt in points if pt for p in pt if p]
    if not prices or rows < 1:
        return ["".join(g) for g in grid], 0, 0
    lo, hi = min(prices), max(prices)
    span = max(hi - lo, 1)
    for c, pt in enumerate(points):
        for p, ch in zip(pt or (0, 0), "ba", strict=True):
            if p:
                cell = grid[rows - 1 - (p - lo) * (rows - 1) // span]
                cell[c] = "x" if cell[c] not in (" ", ch) else ch
    return ["".join(g) for g in grid], lo, hi


def chart(history: list[tuple[int, int]], rows: int, cols: int) -> tuple[list[str], int, int]:
    """The last updates, one column per bucket of updates (its last update), the newest at
    the right edge."""
    if not history or cols < 1:
        return plot([None] * max(cols, 0), rows)
    per = max(1, -(-len(history) // cols))  # ceil
    n = -(-len(history) // per)
    pts = [history[min(len(history) - 1, (i + 1) * per - 1)] for i in range(n)]
    return plot([None] * (cols - n) + pts, rows)


DAY = (4 * 60, 20 * 60)  # ITCH minutes on the day chart's x axis: 04:00 to 20:00
SESSION = (9 * 60 + 30, 16 * 60)  # regular trading hours, marked on the axis


def day_chart(
    minutes: dict[int, tuple[int, int]], rows: int, cols: int
) -> tuple[list[str], int, int]:
    """The day so far on a fixed time axis (DAY): column c is the last BBO in its time
    span, empty if the span has none. Columns holding the session's open and close are
    marked '|' where nothing is drawn."""
    span = DAY[1] - DAY[0]
    pts: list[tuple[int, int] | None] = []
    for c in range(cols):
        a, b = DAY[0] + c * span // cols, DAY[0] + (c + 1) * span // cols
        last = max((m for m in minutes if a <= m < b), default=None)
        pts.append(minutes[last] if last is not None else None)
    grid, lo, hi = plot(pts, rows)
    marks = {c for c in range(cols) for t in SESSION if DAY[0] + c * span // cols <= t
             < DAY[0] + (c + 1) * span // cols}  # fmt: skip
    grid = [
        "".join("|" if c in marks and ch == " " else ch for c, ch in enumerate(r)) for r in grid
    ]
    return grid, lo, hi


def time_axis(cols: int) -> str:
    """Hour labels under the day chart, each at its column."""
    out = [" "] * cols
    span = DAY[1] - DAY[0]
    for h in range(DAY[0] // 60, DAY[1] // 60 + 1, 2):
        c = min(cols - 2, (h * 60 - DAY[0]) * cols // span)
        if c >= 0 and all(ch == " " for ch in out[max(0, c - 1) : c + 2]):
            out[c : c + 2] = f"{h:02d}"
    return "".join(out[:cols])


CELL = {"a": ("•", "ask"), "b": ("•", "bid"), "x": ("•", "title"), "|": ("┊", "dim")}


def _chart_lines(grid: list[str], lo: int, hi: int) -> list[Line]:
    out: list[Line] = []
    for i, row in enumerate(grid):
        label = price(hi) if i == 0 and hi else price(lo) if i == len(grid) - 1 and lo else ""
        segs: Line = [(f"{label:>9} |", "dim")]
        segs += [CELL.get(ch, (" ", "dim")) for ch in row]
        out.append(segs)
    return out


def render(v: View, rate: float | None, width: int, height: int) -> list[Line]:
    """The screen as styled lines; at most `height` lines of at most `width` characters.
    rate None (a rendered GIF, where wall-clock rate means nothing): the rate is left out."""
    if v.cfg is None:
        return [[("waiting for the board's config ack in the capture log ...", "dim")]]
    sym = v.cfg.symbol
    lines: list[Line] = [
        [(f" {sym}  ITCH {clock(v.ts)}   order book on the FPGA (Arty A7-100T)", "title")],
        [
            ("  BID ", "dim"),
            (quote(v.bid, v.bid_sz), "bid"),
            ("   ASK ", "dim"),
            (quote(v.ask, v.ask_sz), "ask"),
            ("   spread ", "dim"),
            (price(v.ask - v.bid).strip() if v.bid and v.ask else "-", "title"),
        ],
        [],
    ]
    cols = max(0, width - 12)  # 11 for the price label; lines stay under width
    free = max(0, height - 10)  # header 3, two chart titles, time axis, footer 4
    day_rows = max(0, free * 2 // 3)
    recent_rows = max(0, free - day_rows)
    lines.append([("  the day so far (ITCH time; ┊ = 09:30 open, 16:00 close)", "dim")])
    lines += _chart_lines(*day_chart(v.minutes, day_rows, cols))
    lines.append([(" " * 11 + time_axis(cols), "dim")])
    lines.append([(f"  the last {len(v.history):,} BBO updates", "dim")])
    lines += _chart_lines(*chart(list(v.history), recent_rows, cols))
    ok = v.matched == v.seen
    lost = v.stats.get("rx_fifo_overflow", 0) + v.stats.get("mold_gaps", 0)
    lines += [
        [],
        [
            (f"  BBO updates/s {rate:>8,.0f}    " if rate is not None else "  ", "dim"),
            ("matched vs golden model ", "dim"),
            (f"{v.matched:,}/{v.seen:,}", "ok" if ok else "bad"),
        ],
        [
            ("  book ", "dim"),
            ("STALE" if v.stale else "live", "bad" if v.stale else "ok"),
            ("    input frames lost ", "dim"),
            (f"{lost:,}", "bad" if lost else "ok"),
            ("    output datagrams lost ", "dim"),
            (f"{v.lost_datagrams:,}", "bad" if v.lost_datagrams else "ok"),
        ],
    ]
    out: list[Line] = []
    for line in lines[:height]:
        cut, n = [], 0
        for text, style in line:
            text = text[: max(0, width - 1 - n)]
            n += len(text)
            cut.append((text, style))
        out.append(cut)
    return out


def expected_cached(packets: Path) -> Expect:
    """model.protocol.expected's records for a config, cached next to the packets file."""

    def expect(cfg: P.Config) -> list[bytes]:
        key = hashlib.sha1(cfg.record()).hexdigest()[:10]
        cache = packets.with_name(f"{packets.name}.{key}.expected")
        n = 64 if cfg.mode == 0 else 40
        if cache.exists() and cache.stat().st_mtime >= packets.stat().st_mtime:
            data = cache.read_bytes()
        else:
            book = BoundedBook(base_ticks=cfg.lo_price // 100 + 2048)
            recs, _ = P.expected(iter_raw(packets.open("rb")), cfg, book)
            data = b"".join(recs)
            cache.write_bytes(data)
        return [data[i : i + n] for i in range(0, len(data), n)]

    return expect


STYLES = {"title": 1, "bid": 2, "ask": 3, "ok": 4, "bad": 5, "dim": 6}


def run(scr: curses.window, log: Path, expect: Expect, fps: float) -> None:
    curses.curs_set(0)
    curses.use_default_colors()
    for n, fg in zip(STYLES.values(), [7, 2, 1, 2, 1, 8], strict=True):
        curses.init_pair(n, fg if fg < curses.COLORS else 7, -1)
    scr.nodelay(True)
    started = time.time()
    view = View(expect)
    tail: LogTail | None = None
    rates: deque[tuple[float, int]] = deque()
    while scr.getch() not in (ord("q"), 27):
        if tail is None and log.exists() and log.stat().st_mtime >= started:
            tail = LogTail(log.open("rb"))
        for payload in tail.poll() if tail else []:
            view.apply(payload)
        now = time.monotonic()
        rates.append((now, view.seen))
        while rates and now - rates[0][0] > 1.0:
            rates.popleft()
        rate = (view.seen - rates[0][1]) / max(now - rates[0][0], 1e-3) if rates else 0.0
        h, w = scr.getmaxyx()
        scr.erase()
        for y, line in enumerate(render(view, rate, w, h)):
            x = 0
            for text, style in line:
                attr = curses.color_pair(STYLES[style])
                if style in ("title", "ok", "bad"):
                    attr |= curses.A_BOLD
                scr.addstr(y, x, text, attr)
                x += len(text)
        scr.refresh()
        time.sleep(1 / fps)


# GIF colours (GitHub dark theme) per style.
GIF_COLOURS = {
    "bg": (13, 17, 23),
    "title": (230, 237, 243),
    "bid": (63, 185, 80),
    "ask": (248, 81, 73),
    "ok": (63, 185, 80),
    "bad": (248, 81, 73),
    "dim": (139, 148, 158),
}


def gif(
    log: Path, expect: Expect, out: Path, frames: int, fps: float, size: tuple[int, int]
) -> int:
    """Render a recorded run as an animated GIF: `frames` snapshots spaced evenly by BBO
    count (busy hours get more frames), then the final state held for 4 s. Returns the
    file size in bytes."""
    from matplotlib import font_manager
    from PIL import Image, ImageDraw, ImageFont

    font = ImageFont.truetype(font_manager.findfont("DejaVu Sans Mono"), 14)
    _, top, _, bottom = font.getbbox("M")
    cw, ch = font.getlength("M"), int((bottom - top) * 1.45)  # advance width, not ink
    width, height = size
    payloads = LogTail(log.open("rb")).poll()
    total = sum(len(P.parse(p).records) for p in payloads if P.parse(p).kind == P.BBO)
    marks = {total * (i + 1) // frames for i in range(frames)}
    view = View(expect)
    images: list[Image.Image] = []

    def snap() -> None:
        img = Image.new("RGB", (int(cw * width) + 16, ch * height + 16), GIF_COLOURS["bg"])
        d = ImageDraw.Draw(img)
        for y, line in enumerate(render(view, None, width, height)):
            x = 0
            for text, style in line:
                d.text((8 + x * cw, 8 + y * ch), text, font=font, fill=GIF_COLOURS[style])
                x += len(text)
        images.append(img.convert("P", palette=Image.Palette.ADAPTIVE, colors=16))

    for p in payloads:
        before = view.seen
        view.apply(p)
        if any(before < m <= view.seen for m in marks):
            snap()
    snap()
    durations = [int(1000 / fps)] * (len(images) - 1) + [4000]
    images[0].save(
        out, save_all=True, append_images=images[1:], duration=durations, loop=0, optimize=True
    )
    return out.stat().st_size


def main(argv: list[str] | None = None) -> None:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--log", type=Path, required=True)
    ap.add_argument("--packets", type=Path, required=True)
    ap.add_argument("--mode", choices=["bbo"], default="bbo")
    ap.add_argument("--symbol", default="AAPL")
    ap.add_argument("--lo-price", type=int, default=P.window_lo_price(23455))  # ADR 0002
    ap.add_argument("--fps", type=float, default=15)
    ap.add_argument("--gif", type=Path, help="render the (finished) log to this GIF instead")
    ap.add_argument("--frames", type=int, default=200)
    ap.add_argument("--size", type=int, nargs=2, default=(110, 34), metavar=("COLS", "ROWS"))
    a = ap.parse_args(argv)
    expect = expected_cached(a.packets)
    if a.gif:
        n = gif(a.log, expect, a.gif, a.frames, a.fps, tuple(a.size))
        print(f"{a.gif}: {n / 1e6:.2f} MB")
        return
    print("preparing the golden model's records (cached after the first time) ...", flush=True)
    expect(P.Config(symbol=a.symbol, lo_price=a.lo_price))
    print("ready: start the run now (q quits)", flush=True)
    time.sleep(1)
    os.environ.setdefault("ESCDELAY", "25")
    curses.wrapper(run, a.log, expect, a.fps)


if __name__ == "__main__":
    main()
