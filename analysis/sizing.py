"""Order-table and entry sizing evidence from a one-symbol extract (see itch_extract.py).

Usage: sizing.py <symbol.itch> <base_ticks> <out_prefix>
Writes <out_prefix>_table_fill.csv (worst live orders in one bucket, per set count).
"""

from __future__ import annotations

import csv
import sys
from collections import Counter

from model.book import OrderBook
from model.bounded import TICK, in_window
from model.itch import Add, Delete, Reduce, Replace, decode, iter_raw

SET_COUNTS = (2048, 4096, 8192, 16384, 32768)
REF_BITS = 32  # covers the day's max ref with margin


def low_bits(ref: int, bits: int) -> int:
    return ref & ((1 << bits) - 1)


def xor_fold(ref: int, bits: int) -> int:
    """XOR of the ref's bits-wide slices: a few LUTs, no pipeline stage."""
    out, ref = 0, ref & ((1 << REF_BITS) - 1)
    while ref:
        out ^= ref & ((1 << bits) - 1)
        ref >>= bits
    return out


def multiplicative(ref: int, bits: int) -> int:
    """Fibonacci hash of the low 32 bits: one DSP multiply, top bits of the product."""
    return ((ref & 0xFFFFFFFF) * 0x9E3779B1 & 0xFFFFFFFF) >> (32 - bits)


HASHES = {"low_bits": low_bits, "xor_fold": xor_fold, "multiplicative": multiplicative}
MULTIPLIERS = (0x9E3779B1, 0x85EBCA6B)  # odd, so each hash is a bijection on 32-bit refs


class Table:
    """d-left table: one bank per hash, insert into the emptiest candidate bucket.

    Overflow goes to a stash, so the run measures how big a stash zero-loss would need.
    """

    def __init__(self, banks: int, sets_per_bank: int, ways: int) -> None:
        self.bits = sets_per_bank.bit_length() - 1
        self.banks, self.ways = banks, ways
        self.fill: list[Counter[int]] = [Counter() for _ in range(banks)]
        self.where: dict[int, tuple[int, int] | None] = {}  # None: in the stash
        self.stash = self.peak_stash = self.stash_inserts = 0

    def _index(self, bank: int, ref: int) -> int:
        product = (ref & 0xFFFFFFFF) * MULTIPLIERS[bank] & 0xFFFFFFFF
        return product >> (32 - self.bits)

    def insert(self, ref: int) -> None:
        candidates = [(self.fill[b][self._index(b, ref)], b) for b in range(self.banks)]
        fill, bank = min(candidates)  # ties go to bank 0, as d-left specifies
        if fill >= self.ways:
            self.where[ref] = None
            self.stash += 1
            self.stash_inserts += 1
            self.peak_stash = max(self.peak_stash, self.stash)
            return
        index = self._index(bank, ref)
        self.fill[bank][index] += 1
        self.where[ref] = (bank, index)

    def remove(self, ref: int) -> None:
        place = self.where.pop(ref)
        if place is None:
            self.stash -= 1
        else:
            self.fill[place[0]][place[1]] -= 1


# (label, banks, sets per bank, ways): total entries 32,768 or 65,536.
TABLES = (
    ("1x8192x4", 1, 8192, 4),
    ("2x4096x4", 2, 4096, 4),
    ("1x16384x4", 1, 16384, 4),
    ("2x8192x4", 2, 8192, 4),
)
HALF_WINDOW = 2048


def main(path: str, base_ticks: str, prefix: str) -> None:
    base = int(base_ticks)
    book = OrderBook()
    keys = [(name, sets) for name in HASHES for sets in SET_COUNTS]
    fills = {key: Counter[int]() for key in keys}
    worst = dict.fromkeys(keys, 0)
    tables = {label: Table(banks, sets, ways) for label, banks, sets, ways in TABLES}
    max_order_shares = 0
    sub_penny: Counter[int] = Counter()
    oow_bbo = []

    with open(path, "rb") as f:
        for raw in iter_raw(f):
            msg = decode(raw)
            if not isinstance(msg, Add | Reduce | Delete | Replace):
                continue
            touched = [msg.ref] + ([msg.new_ref] if isinstance(msg, Replace) else [])
            before = [ref in book.orders for ref in touched]
            bbo = book.apply(msg)
            for ref, was in zip(touched, before, strict=True):
                now = ref in book.orders
                if was == now:
                    continue
                for table in tables.values():
                    (table.insert if now else table.remove)(ref)
                for (name, sets), fill in fills.items():
                    index = HASHES[name](ref, sets.bit_length() - 1)
                    key = (name, sets)
                    fill[index] += 1 if now else -1
                    worst[key] = max(worst[key], fill[index])
            if isinstance(msg, Add | Replace):
                max_order_shares = max(max_order_shares, msg.shares)
                if msg.price < TICK:
                    sub_penny[msg.price] += 1
            if bbo and bbo.bid_size and bbo.ask_size:
                inside = in_window(bbo.bid_price, base, HALF_WINDOW) and in_window(
                    bbo.ask_price, base, HALF_WINDOW
                )
                if not inside:
                    oow_bbo.append(bbo)

    with open(f"{prefix}_table_fill.csv", "w", newline="") as f:
        writer = csv.writer(f)
        writer.writerow(["hash", "sets", "worst_bucket_fill"])
        writer.writerows((name, sets, n) for (name, sets), n in worst.items())
    with open(f"{prefix}_table_dleft.csv", "w", newline="") as f:
        writer = csv.writer(f)
        writer.writerow(["table", "entries", "overflow_inserts", "peak_stash"])
        for label, banks, sets, ways in TABLES:
            t = tables[label]
            writer.writerow([label, banks * sets * ways, t.stash_inserts, t.peak_stash])
            print(f"{label}: overflow inserts {t.stash_inserts}, peak stash {t.peak_stash}")
    for name in HASHES:
        print(f"worst bucket fill, {name}:", {s: worst[(name, s)] for s in SET_COUNTS})
    print("max shares on one order:", max_order_shares)
    print("sub-penny add/replace prices (price units: count):", dict(sub_penny))
    for b in oow_bbo:
        print(
            f"out-of-window BBO at {b.ts / 3.6e12:.4f} h: "
            f"bid {b.bid_price / 1e4} x {b.bid_size}, ask {b.ask_price / 1e4} x {b.ask_size}"
        )


if __name__ == "__main__":
    main(*sys.argv[1:4])
