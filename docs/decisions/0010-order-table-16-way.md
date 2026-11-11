# 0010: Order table reshaped to 2 × 2,048 × 16 for a busier day

**Status: Accepted 2026-11-10.** Supersedes ADR 0001's geometry (2 × 8,192 × 4). The rest
of ADR 0001 still holds: d-left placement, hashes and multipliers, counted limits, the
BoundedBook mirror.

## Context

ADR 0001 sized the table on one day (2019-10-18 AAPL, peak 27,814 live orders) and asked for
a second, busier day before the RTL was tagged. The user chose 2025-12-12, the largest file
listed (12.55 GB). AAPL that day peaks at **54,981 live orders**, 84 % of the 65,536 entries.
On the 2 × 8,192 × 4 shape the bounded model overflows 14,717 inserts. The later messages of
those orders become 1,340,478 `unknown_ref`, and the book is stale from message 56,226
(pre-market) to the close. Every wrong BBO is flagged stale (`unflagged_wrong` 0), so the
book is never silently wrong. It is simply unusable all day.

Doubling the entries is not possible on this part: 131,072 × 56-bit entries is 7.3 Mb
against the device's 4.86 Mb of block RAM. The full chip already uses 107.5 of 135 BRAM36.

## Options considered

The golden model at each shape, with the same capacity (65,536 entries) unless noted.

| Option | Day 2 table_overflow | Day 2 stale | Day 1 | BRAM36 (table) |
|---|---|---|---|---|
| 2 × 8,192 × 4 (ADR 0001) | 14,717 | yes | 0 overflows | 96 |
| 2 × 4,096 × 8 | 529 | yes (from msg 3,363,848) | not run | not built |
| **2 × 2,048 × 16** | **0** | **no** | **0 overflows** | **100 (measured)** |
| 2 × 16,384 × 4 (131,072 entries) | 0 | no | n/a | ≥ 192 by arithmetic (2 × 96): does not fit |
| Keep 2 × 8,192 × 4 and document the limit | 14,717 | yes | 0 | 96 |
| Choose a quieter second day | n/a | n/a | n/a | 96 (cherry-picking) |
| 54-bit entries at 16 ways (shares 18 bits) | 0 | no | 0 | 96 (new 262,143-share limit) |

- **More ways tolerate a fuller table.** At 84 % load, a d-left bucket pair of 2 × 4 fills
  somewhere during the day; 2 × 16 does not. Same memory, same hash.
- **The cost is logic, not memory.** Each lookup compares 16 tags per bank and counts 16
  valid bits. The RTL was already generic in `SET_BITS` and `WAYS`, so only the defaults
  changed; timing closed without a new pipeline stage, and lookup latency stays 7 cycles.
- **Tags widen.** With 11 set bits the tag is 21 bits (32 − 11), so an entry is 56 bits and a
  set row 896 bits. Narrowing shares to 18 bits would keep 54-bit entries and 96 BRAM36,
  but adds a new, tighter counted limit to save 4 BRAM36 the budget can afford. Rejected.

## Decision

2-bank d-left, **2,048 sets per bank, 16 ways**, 65,536 entries. Hash, multipliers, tie rule
(bank 0) and lowest-free-way placement are unchanged from ADR 0001. Entry 56 bits: tag 21,
price index 12, out-of-window 1, shares 20, side 1, valid 1.

## Consequences

- **BRAM:** 100 BRAM36 for the table (was 96); `book_engine` 108. The full chip goes from
  107.5 to 111.5 of 135 (measured, `docs/reports/arty_top/`).
- **Logic:** `order_table` grows from 2,099 to 6,872 LUTs and 2,363 to 9,282 FFs (OOC).
- **Headroom is thin:** 84 % load at the busiest peak measured so far. A day with roughly 20 %
  more live AAPL orders may overflow again. If it does, the book is flagged stale, not wrong.
- **Clear after reset** is now 2,048 table sets, so the book clear (2^12 level indices) is
  dominated by `book_levels`. `test_core`'s straddle payload was retuned from the measured
  release time (730 → 325 bytes).
- Golden model defaults (`BoundedBook`) and the testbenches follow: tests run at 512 and
  2,048 sets per bank, both at 16 ways.

## Measured evidence

- `data/results/2025-12-12_AAPL.md` and its CSVs: peak live orders, every shape above
  (`_bounded*.csv`), `unflagged_wrong` 0 on all of them.
- `data/results/2019-10-18_AAPL_bounded_2x2048x16.csv`: day 1 on the new shape, no overflow,
  same counters as ADR 0001's run.
- `docs/reports/ooc_book_engine/`: out-of-context `book_engine` at 8.000 ns, WNS +0.128 ns,
  WHS +0.081 ns, 108 BRAM36 (table 100), Vivado 2025.2.
- `data/results/2026-11-10_sim_full_book.csv`: the RTL matches the model on both full days
  (916,429 and 831,842 BBO updates) and on 1,009,900 random messages.
- `data/results/2026-11-10_full_day_board_16way.md`: on the board, both full days byte for
  byte (916,429 / 916,429 and 831,842 / 831,842). Full chip at 125 MHz: WNS +0.004 ns, 111.5
  BRAM36. The worst path is the 16-way match (half-select bit → tag compare → hit way).
  Removing the half-depth split, which only the 8,192-set mapping needed, would take its
  output mux off that path if margin is needed later.
- Bug injection on `test_order_table` (2026-11-10): ties to bank 1, an off-by-one full check,
  and an insert that overwrites a valid way each made the tests fail (5, 2 and 6 failing
  tests). All reverted.
