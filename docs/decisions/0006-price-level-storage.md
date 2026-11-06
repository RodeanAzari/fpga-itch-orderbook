# 0006: Price-level storage and best-price search

**Status: Accepted 2026-11-06.** Implements buildplan §6.4's chosen option and fixes the
details it leaves open: level word width, bitmap storage, sub-penny prices, and when a BBO
is evaluated.

## Context

Per side, the book must hold aggregate shares at every in-window price (4,096 ticks around
the ADR 0002 base) and always know the best one. When the best level empties, the next best
must be found before the next message's BBO is due. Measured on 2019-10-18 AAPL
(`data/results/2019-10-18_AAPL_levels.csv`):

| | Bid | Ask |
|---|---|---|
| Max aggregate shares on one level | 44,920 | 294,065 (19 bits) |
| Max live levels at once | 922 | 843 |
| Best emptied, other levels live (search needed) | 47,197 | 49,571 |
| Max ticks from emptied best to next best | 188 | 108 |

## Options considered

| Option | Best-price update | Worst case | Verdict |
|---|---|---|---|
| **Price-indexed arrays + two-level occupancy bitmap** | O(1) on improve; 2–3 cycles to find the next best | Bounded, data-independent | **Chosen** |
| Price-indexed arrays, linear scan from the old best | O(1) on improve; scan on empty | 4,096 cycles (~33 µs at 125 MHz), against ~2.9 µs between messages at line rate | Rejected: data-dependent stall. Fine on this day (≤ 188 ticks), unbounded in principle. |
| Sorted top-K register array | Single cycle | Levels below K are lost; book wrong once the top depletes | Rejected: the day has 900+ live levels per side. Needs a backing store anyway. |
| Single-level 4,096-bit bitmap, one priority encoder | 1 cycle in principle | A 4,096-input priority encoder will not close at 125 MHz | Rejected for timing. |

## Decision

**Level arrays.** Per side, one simple dual-port BRAM of 4,096 × `LEVEL_W` aggregate shares,
indexed by `idx = price / 100 − base + 2048` (ascending price on both sides). Best bid is the
highest occupied index, best ask the lowest.

- `LEVEL_W = SHARES_W + log2(TABLE_ENTRIES)` = 20 + 16 = **36**. A level is a sum over at
  most 65,536 tracked orders, each below 2^20, so it is below 2^36: **overflow is
  impossible by construction**. This is derived as a `localparam` and checked by an SVA on
  every write (sum stays in range, subtract never underflows). 4,096 × 36 is 4 BRAM36 in
  4K × 9 mode, so the extra width costs nothing over 32 bits.

**Occupancy bitmap, two levels, per side.** L1: 64 bits in registers, bit *g* set when
group *g* (64 levels) has any non-empty level. L2: 64 words × 64 bits in distributed RAM
(asynchronous read), word *g* holding that group's per-level bits. Find next best: priority
encode L1 (highest set bit for bids, lowest for asks) → read L2 word → priority encode →
index. Two 64-input encoders per side, 2–3 cycles; exact count in the module header.

**Updates are one level delta at a time.** The order table emits deltas `(side, idx,
±shares, last)`. Read-modify-write the level; set the L2 bit on 0 → non-zero, clear it on
→ 0, and clear the L1 bit when the L2 word becomes zero. No forwarding between deltas: the
block deasserts `tready` until a delta is fully written. At line rate there are ~350 cycles
per message.

**BBO is evaluated once per message, after its last delta.** A Replace is a remove plus an
insert (two deltas) and may move the best transiently; the golden model publishes one BBO
per message, so the RTL compares and emits only on `last`.

**Out-of-window orders** (ADR 0002) send a delta with an out-of-window bit and the edge
instead of an index; it updates the four live counters, never the arrays.

**Sub-penny prices inside the window** cannot be stored in a 1-tick array without merging
into a penny level. Such an order stays in the table, never touches the arrays, increments
`price_subpenny`, and marks the book stale (sticky). Measured: 0 on 2019-10-18 (10
sub-penny prices, all far outside the window); Reg NMS Rule 612 bars sub-penny quotes on
stocks above $1.

## Consequences

- BRAM: 8 BRAM36 for both sides, as already budgeted in `design.md`. L2 bitmaps are
  ~2 × 64 × 64 bits of LUTRAM; L1 is 128 flip-flops.
- **Golden model first:** `BoundedBook` has `price_subpenny` and a sticky `stale` flag
  before any level RTL is written, so the scoreboard and the RTL share one rule.
- **Book stale** is set (sticky until reset) by every condition where the published book can
  differ from the feed's without a flag showing it: `table_overflow`, `ref_overflow`,
  `shares_overflow`, `price_subpenny`, `unknown_ref`, `duplicate_ref`, `over_reduce`.
  `price_oow` does not set it: ADR 0002's out-of-window flag covers those exactly. The
  BBO output's stale bit is book stale OR feed stale (ADR 0003). All seven were 0 on
  2019-10-18 (`_bounded.csv`, `book_stale` = 0).
- The single-delta, no-forwarding design is the simplest correct one. Phase E's injector
  measures whether it limits pipeline capacity; pipelining is a later ADR if it does.
- Depth is complete inside the window; nothing is lost when the top depletes.

## Measured evidence

- `data/results/2019-10-18_AAPL_levels.csv` and its sidecar: level size, depth, search
  frequency and distance.
- `data/results/2019-10-18_AAPL_bounded.csv`: `price_oow`, `bbo_oow` (ADR 0002).

## Implementation note (2026-11-06, `rtl/book/book_levels.sv`, `book_engine.sv`)

- One delta at a time. A message's BBO update is presented 8 cycles after its last level
  delta is taken, 14 when the best empties (L1 encode, L2 read, L2 encode, size read).
  From the record's handshake (order table included): 15 and 21 cycles, measured by
  `test_book.py latency_matches_header`.
- L2 maps to distributed RAM (128 × 64, 44 RAM64M); levels to 8 BRAM36 as budgeted.
- Verified against `BoundedBook`'s BBO stream, update for update: adversarial and directed
  streams at 512 and 8,192 sets, a whole-window stream with ~4,200 bitmap searches up to
  2,485 ticks, and the first 50,000 messages of 2019-10-18 (10,582 BBO updates, 0
  mismatches). 9 injected bugs in the level logic each caught.
- **Timing** (`docs/reports/ooc_book_engine/`, out of context, order table + levels):
  WNS +0.069 ns at 8.000 ns, 104 BRAM36, 10 DSP48, 3,172 LUTs, 3,528 FFs, 172 LUTs as
  memory, 0 critical warnings. Getting there took registering the L2 read address and the
  search's L2 word, a two-stage reciprocal multiply and a registered hash key in the
  order table. **The margin is under 1 %**: the full design may need another stage or a
  lower core clock (core-clock ADR still to write).
