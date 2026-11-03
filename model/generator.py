"""Seeded adversarial ITCH generator. Same seed, same bytes, so any failure reproduces.

MoldUDP64-level cases (gaps, duplicates, heartbeats) belong to the Phase C framing layer.
"""

from __future__ import annotations

import random
import struct
from dataclasses import dataclass

TICK = 100


@dataclass(frozen=True, slots=True)
class Config:
    locate: int = 1
    stock: str = "TEST"
    base_price: int = 1_000_000  # $100.00. Resting bids sit below it, resting asks above it.
    sets: int = 512  # bucket collisions are aimed at this table geometry
    p_collide: float = 0.05  # new ref lands in one crowded bucket
    p_far_price: float = 0.05  # price far outside any window
    p_episode: float = 0.01  # brief locked or crossed book around base_price
    p_noise: float = 0.03  # non-subset message that must be skipped by length
    p_unknown_ref: float = 0.01  # message for an order that does not exist


DEFAULT = Config()


def _header(kind: bytes, locate: int, ts: int) -> bytes:
    return kind + struct.pack(">HH", locate, 0) + ts.to_bytes(6)


def generate(seed: int, count: int, cfg: Config = DEFAULT) -> list[bytes]:
    """Return at least count raw messages, starting with the R message that teaches the locate."""
    rng = random.Random(seed)
    stock = cfg.stock.ljust(8).encode()
    ts = 1
    out = [_header(b"R", cfg.locate, ts) + stock + bytes(20)]
    live: dict[int, tuple[int, bytes]] = {}  # ref -> remaining shares, side
    next_ref = 1

    def plain_ref() -> int:
        nonlocal next_ref
        next_ref += 1
        return next_ref

    def new_ref() -> int:
        ref = plain_ref()
        # Low bits all zero: every such ref lands in one bucket.
        return ref * cfg.sets if rng.random() < cfg.p_collide else ref

    def price(side: bytes) -> int:
        sign = 1 if side == b"S" else -1
        if rng.random() < cfg.p_far_price:
            return cfg.base_price + sign * 500_000
        return cfg.base_price + sign * rng.randrange(1, 40) * TICK

    def add(ref: int, side: bytes, shares: int, at: int) -> None:
        body = struct.pack(">QcI8sI", ref, side, shares, stock, at)
        if rng.random() < 0.5:
            out.append(_header(b"A", cfg.locate, ts) + body)
        else:
            out.append(_header(b"F", cfg.locate, ts) + body + b"MPID")

    while len(out) < count:
        ts += rng.randrange(1, 1000)
        roll = rng.random()
        if roll < cfg.p_noise:
            length = rng.randrange(12, 60)
            kind = rng.choice((b"Q", b"P", b"I", b"L"))
            out.append(_header(kind, cfg.locate, ts) + rng.randbytes(length - 11))
        elif roll < cfg.p_noise + cfg.p_episode:
            # Bid at base, ask at base (locked) or one tick under (crossed), then both leave.
            bid, ask = plain_ref(), plain_ref()
            add(bid, b"B", 100, cfg.base_price)
            add(ask, b"S", 100, cfg.base_price - rng.randrange(2) * TICK)
            out.append(_header(b"D", cfg.locate, ts) + struct.pack(">Q", ask))
            out.append(_header(b"D", cfg.locate, ts) + struct.pack(">Q", bid))
        elif roll < 0.45 or not live:
            ref, side = new_ref(), rng.choice((b"B", b"S"))
            shares = rng.randrange(1, 100) * 100
            add(ref, side, shares, price(side))
            live[ref] = (shares, side)
        else:
            unknown = rng.random() < cfg.p_unknown_ref
            ref = 1 << 60 | rng.getrandbits(32) if unknown else rng.choice(list(live))
            shares, side = live.get(ref, (100, b"B"))
            cut = rng.randrange(1, shares + 1)
            action = rng.choice((b"E", b"C", b"X", b"D", b"U"))
            match action:
                case b"E":
                    body = struct.pack(">QIQ", ref, cut, ts)
                case b"C":
                    # Execution price deliberately differs from the resting price.
                    body = struct.pack(">QIQcI", ref, cut, ts, b"Y", cfg.base_price + 12_300)
                case b"X":
                    body = struct.pack(">QI", ref, cut)
                case b"D":
                    body, cut = struct.pack(">Q", ref), shares
                case _:
                    fresh, cut = new_ref(), shares
                    resize = rng.randrange(1, 100) * 100
                    body = struct.pack(">QQII", ref, fresh, resize, price(side))
                    if ref in live:
                        live[fresh] = (resize, side)
            out.append(_header(action, cfg.locate, ts) + body)
            if ref in live:
                if shares - cut > 0:
                    live[ref] = (shares - cut, side)
                else:
                    del live[ref]
    return out
