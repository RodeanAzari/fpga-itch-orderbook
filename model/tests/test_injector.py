"""model/injector.py against ADR 0014.

Why these matter: the capacity number is only as good as two things. The ROM image must
be exactly the packets the model scores (one wrong last-bit and the deframer sees other
packets), and the digest must change whenever the BBO stream does (else "digest matches"
proves nothing). The image must also end on a BBO, or the meter's span misses work.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from model.bounded import BoundedBook
from model.injector import (
    DEPTH,
    bbo_seqs,
    build_image,
    digest,
    expected_run,
    rom_words,
)
from model.itch import iter_raw
from model.moldudp64 import packetize
from model.protocol import Config, window_lo_price

FIXTURE = Path(__file__).resolve().parents[2] / "data/samples/2019-10-18_AAPL_head50k.itch"
BASE = 23455  # day 1 window base (ADR 0002)
CFG = Config(symbol="AAPL", lo_price=window_lo_price(BASE), mode=2)
SESSION = b"20191018AA"


@pytest.fixture(scope="module")
def msgs() -> list[bytes]:
    with FIXTURE.open("rb") as f:
        return [m for _, m in zip(range(3000), iter_raw(f), strict=False)]


def test_rom_words_layout() -> None:
    pkts = [b"\x01\x02\x03", b"\xff", b"\x00\x10"]
    w = rom_words(pkts)
    assert w[:2] == [0, 6]  # big-endian byte count
    assert w[2:] == [0x01, 0x02, 0x103, 0x1FF, 0x00, 0x110]
    big = rom_words([bytes(300)])
    assert big[:2] == [1, 44]


def test_rom_words_limits() -> None:
    assert len(rom_words([bytes(DEPTH - 2)])) == DEPTH
    with pytest.raises(ValueError):
        rom_words([bytes(DEPTH - 1)])
    with pytest.raises(ValueError):
        rom_words([b""])


def _rec(i: int) -> bytes:
    return bytes((i * 7 + k) & 0xFF for k in range(64))


def test_digest_reference_value() -> None:
    """Two records by hand: d1 = fold(r0); d2 = rotl(d1, 1) ^ fold(r1)."""
    r0 = bytes(range(8)) + bytes(56)
    r1 = bytes(8) + b"\x80" + bytes(7) + bytes(48)
    f0 = 0x0001020304050607
    f1 = 0x8000000000000000
    want = (((f0 << 1) | (f0 >> 63)) & (2**64 - 1)) ^ f1
    assert digest([r0]) == f0
    assert digest([r0, r1]) == want
    assert digest([]) == 0


def test_digest_ignores_times_only() -> None:
    recs = [_rec(i) for i in range(5)]
    d = digest(recs)
    timed = [r[:40] + bytes([0xAB] * 24) for r in recs]
    assert digest(timed) == digest([r[:40] + bytes(24) for r in recs])
    for byte in (0, 1, 13, 32, 39):  # any field before t0
        bad = list(recs)
        bad[2] = bad[2][:byte] + bytes([bad[2][byte] ^ 0x04]) + bad[2][byte + 1 :]
        assert digest(bad) != d, byte


def test_digest_sees_order_loss_and_duplication() -> None:
    recs = [_rec(i) for i in range(6)]
    d = digest(recs)
    assert digest(recs[:2] + [recs[3], recs[2]] + recs[4:]) != d  # swapped
    assert digest(recs[:3] + recs[4:]) != d  # lost
    assert digest(recs[:3] + [recs[3]] + recs[3:]) != d  # duplicated
    with pytest.raises(ValueError):
        digest([bytes(40)])


def test_image_is_the_packetized_prefix_and_ends_on_a_bbo(msgs: list[bytes]) -> None:
    img = build_image(msgs, SESSION, CFG, BoundedBook(base_ticks=BASE))
    assert img.pkts == list(packetize(msgs[: img.messages], SESSION))
    assert img.words == rom_words(img.pkts) and len(img.words) <= DEPTH
    seqs = bbo_seqs(msgs, SESSION, CFG, BoundedBook(base_ticks=BASE))
    assert img.messages in seqs  # the last message publishes a BBO
    # the longest such prefix: the next BBO-publishing message no longer fits
    nxt = min(s for s in seqs if s > img.messages)
    assert sum(map(len, packetize(msgs[:nxt], SESSION))) > DEPTH - 2
    assert img.messages > 900  # most of the ROM is used


def test_expected_run_matches_bbo_mode(msgs: list[bytes]) -> None:
    img = build_image(msgs, SESSION, CFG, BoundedBook(base_ticks=BASE))
    e = expected_run(img, CFG, BoundedBook(base_ticks=BASE), cut_through=False)
    assert e.bbos == len(e.records) > 100
    assert e.digest == digest(e.records)
    ct = expected_run(img, CFG, BoundedBook(base_ticks=BASE), cut_through=True)
    assert ct.bbos == e.bbos and ct.digest != e.digest  # the flag is part of the record
    with pytest.raises(ValueError):
        expected_run(img, Config(symbol="AAPL"), BoundedBook(base_ticks=BASE), False)
