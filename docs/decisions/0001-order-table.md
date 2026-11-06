# 0001: Order-table structure and sizing

**Status: Accepted 2026-11-03.** Supersedes the original CLAUDE.md §6.3 design (low-bits
hash, one table), which is now updated to match.

## Context

`E`, `C`, `X`, `D` and `U` carry only an order reference, so the book needs a ref → order
table in BRAM. §6.3 specified one 4-way set-associative table indexed by the low bits of the
ref, sized at ≥ 2× peak live orders, with `table_overflow == 0` required on real data.

Phase A measured AAPL on 2019-10-18: 27,814 peak live orders, max ref 287,271,257 (29 bits),
max order 82,045 shares. Source: `data/results/2019-10-18_AAPL*.csv`.

## Options considered

Each table was replayed against the day's real refs (`analysis/sizing.py`).

| Option | Entries | Worst bucket | Overflow inserts | BRAM36 |
|---|---|---|---|---|
| 1 table, low-bits hash, 16,384 × 4 | 65,536 | 18 | not run (worst bucket 18 > 4 ways) | 96 |
| 1 table, multiplicative hash, 16,384 × 4 | 65,536 | 8 | 36,545 | 96 |
| 2-bank d-left, 2 × 4,096 × 4 | 32,768 | n/a | 55,626 | 48 |
| **2-bank d-left, 2 × 8,192 × 4** | **65,536** | **≤ 4 (never full)** | **0** | **96** |
| 1 table + CAM stash | 65,536 | 8 | needs a 513-entry stash | 96 + large CAM |

- **Low bits are not well distributed.** At 16,384 sets the worst bucket held 18 live orders,
  versus about 8 for a good hash. The §6.3 assumption does not hold.
- **No single hash works with 4 ways.** At peak, 27,814 orders over 16,384 buckets is a
  Poisson load of 1.7, and somewhere a bucket reaches 8. Zero overflow would need about
  ten times the sets, which the device doesn't have.
- **d-left hashing** reads one bucket in each of two banks in the same cycle (two BRAM
  groups with independent addresses) and inserts into the emptier one, ties going to bank 0.
  Worst-case load drops from about log n / log log n to about log log n.

## Decision

2-bank d-left, 8,192 sets per bank, 4 ways: 65,536 entries, 2.36× the measured peak.

- **Hash:** bank *b* index = top 13 bits of `(ref[31:0] × M_b) mod 2^32`, with odd
  multipliers `M_0 = 0x9E3779B1` and `M_1 = 0x85EBCA6B`. Odd multipliers make it a
  bijection, so the remaining 19 product bits are a complete tag and refs never alias.
  Cost: a 32 × 32 multiply per bank, pipelined (3 DSP48 each; see the implementation note).
- **Entry (54 bits):** tag 19, price index 12, out-of-window 1, shares 20, side 1, valid 1.
  - For an out-of-window order the price index is unused, so its top bit records which edge
    it fell past. That is enough for ADR 0002's flag at no extra width.
  - Four ways make 216 bits per set: 24 BRAM36 in 4K × 9 mode per 4,096 sets.
  - Each bank is 2 halves × 24 = 48 BRAM36, so 96 for both.
- **New counted limits:**
  - `ref_overflow`: ref ≥ 2^32.
  - `shares_overflow`: order > 1,048,575 shares (today's max is 82,045).
  - Either one leaves the order untracked and marks the book stale, never silently truncated.

## Consequences

- 96 of 135 BRAM36 go to the table, leaving 39 for the price levels (8), FIFOs, histogram
  and injector. The injector's payload store will be small. Watch this budget.
- The lookup is one cycle wider (two banks plus hash pipeline). Its latency goes into
  `latency.md`.
- The golden model's `BoundedBook` must implement the same d-left placement, so table
  overflow behaviour matches the RTL exactly.
- Single-symbol only. More symbols share this table and need re-measuring.

## Measured evidence

- `data/results/2019-10-18_AAPL_table_fill.csv`: worst bucket per hash and set count.
- `data/results/2019-10-18_AAPL_table_dleft.csv`: overflow and stash peak per geometry.
- `data/results/2019-10-18_AAPL_bounded.csv`: the golden model at this exact geometry has
  zero `table_overflow`, `ref_overflow`, `shares_overflow` and `unknown_ref` over the day.
- **Limitation:** one trading day. A second, busier day should be checked before RTL is tagged.

## Implementation note (2026-11-06, `rtl/book/order_table.sv`)

- **BRAM mapping.** One 8,192 × 216 RAM per bank was mapped by Vivado as 8K × 4 tiles: 54
  per bank, 108 total. Each bank is now two 4,096-deep halves selected by the set's top
  bit; Vivado maps each half as 4K × 9 tiles (24), giving the budgeted 96.
- **Timing at 125 MHz** took three changes, each from the worst path of an OOC run:
  the BRAM read address comes from its own registers (it fans out to 48 BRAMs), the
  price-to-index divide is spread over three lookup states, the reduce compare and
  subtract are precomputed for every way during the match, and the BRAM output register
  is used. Lookup latency is 7 cycles (measured by `latency_matches_header`).
- **Result** (`docs/reports/ooc_book/`, out of context, xc7a100tcsg324-1, Vivado 2025.2):
  WNS +0.294 ns at 8.000 ns, 96 BRAM36, 8 DSP48, 1,973 LUTs, 2,446 FFs, 0 critical warnings.
