"""Filter a day file down to one symbol, keeping its R message so the locate can be learned.

Usage: itch_extract.py <day.gz> <SYMBOL> <out.itch>   (output: same length-prefixed format)
"""

from __future__ import annotations

import gzip
import io
import struct
import sys

from model.itch import Directory, decode, iter_raw


def main(path: str, symbol: str, out_path: str) -> None:
    locate: bytes | None = None
    kept = 0
    with gzip.open(path, "rb") as raw, open(out_path, "wb") as out:
        for msg in iter_raw(io.BufferedReader(raw, 1 << 20)):  # type: ignore[arg-type]
            if locate is None:
                if msg[:1] == b"R":
                    directory = decode(msg)
                    if isinstance(directory, Directory) and directory.stock == symbol:
                        locate = msg[1:3]
                    else:
                        continue
                else:
                    continue
            elif msg[1:3] != locate:
                continue
            out.write(struct.pack(">H", len(msg)) + msg)
            kept += 1
    if locate is None:
        raise SystemExit(f"{symbol} has no R message")
    print(f"kept {kept} messages for {symbol} (locate {int.from_bytes(locate)})")


if __name__ == "__main__":
    main(*sys.argv[1:4])
