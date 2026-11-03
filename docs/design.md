# Design

Architecture summary and sizing. The rationale for each choice lives in its ADR.

## Decisions

| ADR | Topic | Status |
|---|---|---|
| [0001](decisions/0001-order-table.md) | Order table: 2-bank d-left, 2 × 8,192 × 4 | Proposed |
| [0002](decisions/0002-price-window-base.md) | Window base: opening mid, ±2,048 ticks | Proposed |

Still to write: price-level storage, cut-through vs. store-and-forward, core clock, timestamp
design, `DATA_W` default, locate-filter population, gap/stale semantics.

## Sizing from Phase A data

From AAPL on 2019-10-18 (`data/results/2019-10-18_AAPL.md`):

| Input | Value |
|---|---|
| Peak concurrent live orders | 27,814 |
| Max order ref | 287,271,257 (29 bits) |
| Max shares on one order | 82,045 |
| Window base | 23,455 ticks ($234.55) |
| Two-sided BBOs inside ±2,048 ticks | 831,837 of 831,839 |

### BRAM budget (xc7a100t: 135 × BRAM36)

| Block | Arithmetic | BRAM36 |
|---|---|---|
| Order table | 2 banks × (8,192 sets / 512 deep) × 3 lanes of 72 bits (4 × 54-bit entries) | 96 |
| Price levels | 2 sides × 4,096 levels × 32-bit aggregate shares (4K × 36 → 4 BRAM36 per side) | 8 |
| Occupancy bitmaps | 2 × 64 × 64 bits in registers / LUTRAM | 0 |
| MII async FIFO, frame buffer, TX buffer | estimate, confirm at synthesis | ~4 |
| Latency histogram | estimate, confirm at synthesis | ~2 |
| **Committed so far** | | **~110** |
| Left for the injector and margin | | ~25 |

Rows marked "estimate" become measured utilization at Phase B/E synthesis.

## CDC crossings

None yet (no RTL).
