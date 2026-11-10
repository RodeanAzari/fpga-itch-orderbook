"""Live view of the board's order book for demos (buildplan §7, `bbo_view`).

Usage (in a real terminal; curses needs a TTY):
    python -m analysis.bbo_view --log data/runs/demo.log --packets data/runs/slice0.pkts

Follows the log host/capture is writing (it flushes per datagram) and shows the board's
best bid/offer as it arrives: the ITCH clock, bid/ask with sizes and spread, a scrolling
bid/ask chart, BBO updates per second, and every BBO checked against the golden model
(model.protocol.expected over the feed as sent, PKTS = `replay --out`), matched by its
cause message's MoldUDP64 sequence number. Losses come from the board's own stats.

The model's records are computed before the run (for --mode/--lo-price/--symbol, cached
next to PKTS) so the view is live from the first record; if the ack in the log shows
another config, they are recomputed. Start the viewer, then the run
(`scripts/board_run.sh demo bbo 500 0`): it waits for a log written after it started.

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

    @property
    def lost_datagrams(self) -> int:
        return self.max_pkt_seq - self.datagrams


def clock(ns: int) -> str:
    """ITCH timestamp (ns since midnight) as HH:MM:SS.mmm."""
    s, ms = divmod(ns // 1_000_000, 1000)
    return f"{s // 3600:02d}:{s // 60 % 60:02d}:{s % 60:02d}.{ms:03d}"


def price(p: int) -> str:
    return f"{p / 10_000:8.2f}" if p else "       -"


def chart(history: list[tuple[int, int]], rows: int, cols: int) -> list[str]:
    """Bid ('b') and ask ('a') over the last updates, one column per bucket of updates
    (bucket = its last update), rows scaled to the visible price range, top row = high.
    Empty sides (price 0) are not drawn. Returns `rows` strings of `cols` characters."""
    grid = [[" "] * cols for _ in range(rows)]
    if not history or rows < 1 or cols < 1:
        return ["".join(g) for g in grid]
    per = max(1, -(-len(history) // cols))  # ceil: the newest updates fill the right edge
    cols_data = [history[min(len(history) - 1, (i + 1) * per - 1)] for i in range(cols)]
    cols_data = cols_data[: -(-len(history) // per)]
    prices = [p for b, a in cols_data for p in (b, a) if p]
    if not prices:
        return ["".join(g) for g in grid]
    lo, hi = min(prices), max(prices)
    span = max(hi - lo, 1)
    off = cols - len(cols_data)
    for c, (b, a) in enumerate(cols_data):
        for p, ch in ((a, "a"), (b, "b")):
            if p:
                grid[rows - 1 - (p - lo) * (rows - 1) // span][off + c] = ch
    return ["".join(g) for g in grid]


def render(v: View, rate: float, width: int, height: int) -> list[Line]:
    """The screen as styled lines; at most `height` lines of at most `width` characters."""
    if v.cfg is None:
        return [[("waiting for the board's config ack in the capture log ...", "dim")]]
    sym = v.cfg.symbol
    lines: list[Line] = [
        [(f" {sym}  ITCH {clock(v.ts)}   order book on the FPGA (Arty A7-100T)", "title")],
        [
            ("  BID ", "dim"),
            (f"{price(v.bid)} x {v.bid_sz:<7,}", "bid"),
            ("   ASK ", "dim"),
            (f"{price(v.ask)} x {v.ask_sz:<7,}", "ask"),
            ("   spread ", "dim"),
            (price(v.ask - v.bid).strip() if v.bid and v.ask else "-", "title"),
        ],
        [],
    ]
    rows = max(0, height - 7)
    cols = max(0, width - 11)
    hist = list(v.history)
    grid = chart(hist, rows, cols)
    prices = [p for b, a in hist for p in (b, a) if p]
    for i, row in enumerate(grid):
        label = ""
        if prices and i in (0, rows - 1):
            label = price(max(prices) if i == 0 else min(prices))
        segs: Line = [(f"{label:>9} |", "dim")]
        for ch in row:
            segs.append(("•", "ask") if ch == "a" else ("•", "bid") if ch == "b" else (" ", "dim"))
        lines.append(segs)
    ok = v.matched == v.seen
    lost = v.stats.get("rx_fifo_overflow", 0) + v.stats.get("mold_gaps", 0)
    lines += [
        [],
        [
            (f"  BBO updates/s {rate:>8,.0f}    matched vs golden model ", "dim"),
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


def main(argv: list[str] | None = None) -> None:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--log", type=Path, required=True)
    ap.add_argument("--packets", type=Path, required=True)
    ap.add_argument("--mode", choices=["bbo"], default="bbo")
    ap.add_argument("--symbol", default="AAPL")
    ap.add_argument("--lo-price", type=int, default=P.window_lo_price(23455))  # ADR 0002
    ap.add_argument("--fps", type=float, default=15)
    a = ap.parse_args(argv)
    expect = expected_cached(a.packets)
    print("preparing the golden model's records (cached after the first time) ...", flush=True)
    expect(P.Config(symbol=a.symbol, lo_price=a.lo_price))
    print("ready: start the run now (q quits)", flush=True)
    time.sleep(1)
    os.environ.setdefault("ESCDELAY", "25")
    curses.wrapper(run, a.log, expect, a.fps)


if __name__ == "__main__":
    main()
