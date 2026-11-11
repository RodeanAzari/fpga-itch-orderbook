"""Render a command and its saved terminal output as a terminal-style PNG.

The text is the run's real output (e.g. `compare` as tee'd by the run), drawn verbatim; only
lines ending in `=1`/`=0` for `match`/`unflagged_wrong` get a colour. This is a rendering of
a log, not a screen grab: the sidecar of the run says which file it came from.
"""

from __future__ import annotations

import argparse
from pathlib import Path

from analysis.bbo_view import GIF_COLOURS


def style(line: str) -> str:
    if line in ("match=1", "unflagged_wrong=0", "mismatched_positions=0", "lost_datagrams=0"):
        return "ok"
    if line.startswith(("match=", "unflagged_wrong=")):
        return "bad"
    return "title"


def render(command: str, output: list[str], out: Path, prompt: str) -> None:
    from matplotlib import font_manager
    from PIL import Image, ImageDraw, ImageFont

    font = ImageFont.truetype(font_manager.findfont("DejaVu Sans Mono"), 15)
    _, top, _, bottom = font.getbbox("M")
    cw, ch = font.getlength("M"), int((bottom - top) * 1.5)
    lines = [(prompt, command)] + [("", s) for s in output]
    cols = max(len(p) + len(s) for p, s in lines)
    img = Image.new("RGB", (int(cw * cols) + 32, ch * len(lines) + 32), GIF_COLOURS["bg"])
    d = ImageDraw.Draw(img)
    for y, (p, s) in enumerate(lines):
        if p:
            d.text((16, 16 + y * ch), p, font=font, fill=GIF_COLOURS["ok"])
        d.text((16 + len(p) * cw, 16 + y * ch), s, font=font, fill=GIF_COLOURS[style(s)])
    img.save(out, optimize=True)


def main(argv: list[str] | None = None) -> None:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--text", type=Path, required=True, help="saved output of the command")
    ap.add_argument("--command", required=True, help="the command line shown above it")
    ap.add_argument("--prompt", default="$ ")
    ap.add_argument("--png", type=Path, required=True)
    a = ap.parse_args(argv)
    render(a.command, a.text.read_text().splitlines(), a.png, a.prompt)
    print(a.png)


if __name__ == "__main__":
    main()
