"""Build the injector ROM image (ADR 0014) and print what an inject run must report.

  python -m analysis.injector_image [--out data/samples/injector_aapl.mem]

Input: the committed AAPL fixture (data/samples/2019-10-18_AAPL_head50k.itch). The image
is deterministic, so the committed .mem file can be checked by rerunning this.
"""

from __future__ import annotations

import argparse
from collections import Counter
from pathlib import Path

from model.bounded import BoundedBook
from model.injector import Image, build_image, expected_run
from model.itch import iter_raw
from model.protocol import Config, window_lo_price

REPO = Path(__file__).resolve().parents[1]
FIXTURE = REPO / "data/samples/2019-10-18_AAPL_head50k.itch"
OUT = REPO / "data/samples/injector_aapl.mem"
SESSION = b"20191018AA"
BASE_TICKS = 23455  # day 1 window base (ADR 0002)
CONFIG = Config(symbol="AAPL", lo_price=window_lo_price(BASE_TICKS), mode=2)
PREFIX = 3000  # messages considered; the ROM holds about a third of them


def messages() -> list[bytes]:
    with FIXTURE.open("rb") as f:
        return [m for _, m in zip(range(PREFIX), iter_raw(f), strict=False)]


def image() -> Image:
    return build_image(messages(), SESSION, CONFIG, BoundedBook(base_ticks=BASE_TICKS))


def image_payloads() -> list[bytes]:
    return image().pkts


def image_text() -> tuple[str, str]:
    msgs = messages()
    img = image()
    mix = Counter(chr(m[0]) for m in msgs[: img.messages])
    lines = []
    for ct in (False, True):
        e = expected_run(img, CONFIG, BoundedBook(base_ticks=BASE_TICKS), cut_through=ct)
        lines.append(f"cut_through={int(ct)} inj_bbos={e.bbos} inj_digest={e.digest:#018x}")
    summary = (
        f"messages={img.messages} packets={len(img.pkts)} "
        f"payload_bytes={sum(map(len, img.pkts))} words={len(img.words)}\n"
        f"mix={dict(sorted(mix.items()))}\n" + "\n".join(lines)
    )
    return "".join(f"{w:03x}\n" for w in img.words), summary


def main(argv: list[str] | None = None) -> None:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--out", type=Path, default=OUT)
    args = ap.parse_args(argv)
    text, summary = image_text()
    args.out.write_text(text)
    print(summary)


if __name__ == "__main__":
    main()
