"""MoldUDP64 stimulus shared by test_feed and test_deframer."""

from __future__ import annotations

import struct

from model import generator
from model.moldudp64 import end_of_session, heartbeat, pack, packetize

SYMBOL = generator.DEFAULT.stock


def adversarial_packets(rng, n_msgs: int) -> list[bytes]:
    """Generator messages (already ITCH-adversarial), packetized, then perturbed."""
    msgs = generator.generate(seed=rng.randrange(1 << 30), count=n_msgs)
    for _ in range(max(1, n_msgs // 400)):  # ITCH-level damage
        k = rng.randrange(20, len(msgs))
        how = rng.choice(["long", "short", "side", "empty"])
        m = msgs[k]
        if how == "long":
            msgs[k] = m + b"\x00"
        elif how == "short" and len(m) > 1:
            msgs[k] = m[:-1]
        elif how == "side" and m[:1] in (b"A", b"F"):
            msgs[k] = m[:19] + b"Q" + m[20:]
        elif how == "empty":
            msgs[k] = b""
    # Other stocks: copies of book messages with another locate, and their R.
    other = 900
    msgs.insert(5, b"R" + struct.pack(">HH", other, 0) + bytes(6) + b"OTHER   " + bytes(20))
    for _ in range(max(1, n_msgs // 100)):
        k = rng.randrange(10, len(msgs))
        m = msgs[k]
        if m[:1] in b"AFECXDU" and len(m) > 3:
            msgs.insert(k, m[:1] + struct.pack(">H", other) + m[3:])
    # Near the end, our symbol's locate changes: later messages use the new one.
    new_loc = generator.DEFAULT.locate + 1
    cut = len(msgs) - 200
    msgs.insert(
        cut, b"R" + struct.pack(">HH", new_loc, 0) + bytes(6) + SYMBOL.ljust(8).encode() + bytes(20)
    )
    msgs[cut + 1 :] = [
        m[:1] + struct.pack(">H", new_loc) + m[3:] if len(m) > 3 else m for m in msgs[cut + 1 :]
    ]
    sess = [b"SESSIONAAA", b"SESSIONBBB"]
    pkts = list(packetize(msgs, sess[0], max_payload=rng.choice([84, 200, 1472])))
    out: list[bytes] = []
    i = 0
    while i < len(pkts):
        p = pkts[i]
        seq, count = struct.unpack_from(">QH", p, 10)
        r = rng.random()
        if r < 0.02:
            pass  # lost
        elif r < 0.04:
            out += [p, p]  # duplicate
        elif r < 0.06 and i + 1 < len(pkts):
            out += [pkts[i + 1], p]  # reordered
            i += 1
        elif r < 0.08:
            out += [p, heartbeat(sess[0], seq + count)]
        elif r < 0.09:
            out.append(p[: rng.randrange(1, len(p))])  # truncated
        elif r < 0.10:
            out.append(p + bytes(rng.randrange(1, 5)))  # trailing bytes
        elif r < 0.11 and count > 2:
            out.append(pack(sess[0], seq + 1, []) + p[22:])  # overlap-ish / malformed mix
        else:
            out.append(p)
        i += 1
    out.append(end_of_session(sess[0], 1 << 40))
    out.append(pack(sess[1], 1, msgs[:50]))  # a new session: tracking restarts
    return out
