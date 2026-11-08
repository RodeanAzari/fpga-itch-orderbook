# Design

Architecture summary and sizing. The rationale for each choice lives in its ADR.

## Decisions

| ADR | Topic | Status |
|---|---|---|
| [0001](decisions/0001-order-table.md) | Order table: 2-bank d-left, 2 × 8,192 × 4 | Accepted |
| [0002](decisions/0002-price-window-base.md) | Window base: opening mid, ±2,048 ticks; out-of-window BBO flagged | Accepted |
| [0003](decisions/0003-gap-stale-semantics.md) | MoldUDP64 gaps: stale is sticky until reset; overlaps dropped whole; malformed packets deliver what they carry | Accepted |
| [0004](decisions/0004-locate-filter.md) | Locate learned from `R` for the configured symbol, optional host override | Accepted |
| [0005](decisions/0005-data-width.md) | `DATA_W = 8` default; 64-bit (message aligner) in Phase E for the capacity measurement | Accepted |
| [0006](decisions/0006-price-level-storage.md) | Price levels: 4,096 × 36-bit arrays per side + 64 × 64 two-level bitmap; BBO once per message; in-window sub-penny → stale | Accepted |
| [0007](decisions/0007-output-and-control.md) | Output: batched datagrams (flush on idle), decode-trace mode, UDP config packet with ack; `docs/protocol.md` | Accepted |
| [0008](decisions/0008-output-builder-buffering.md) | Output builder: byte FIFO + complete-record count; ack flushes at a datagram boundary and latches the mode | Accepted |
| [0009](decisions/0009-core-clock.md) | Core clock 125 MHz (full chip WNS +0.059 ns); fallback: one more stage at the price-index multiply | Accepted |

Still to write: cut-through vs. store-and-forward, timestamp design.

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
| Price levels | 2 sides × 4,096 levels × 36-bit aggregate shares (4K × 9 mode → 4 BRAM36 per side) | 8 |
| Occupancy bitmaps | 2 × (64-bit L1 registers + 64 × 64 L2 LUTRAM) | 0 |
| MII async FIFO, frame buffer, TX buffer | estimate, confirm at synthesis | ~4 |
| Latency histogram | estimate, confirm at synthesis | ~2 |
| **Committed so far** | | **~110** |
| Left for the injector and margin | | ~25 |

Rows marked "estimate" become measured utilization at Phase B/E synthesis.

Measured so far: order table + price levels (`book_engine`, out of context) use 104 BRAM36,
as budgeted (`docs/reports/ooc_book_engine/`).
Full chip (`arty_top`, `docs/reports/arty_top/`): 107.5 BRAM36 of 135 (80 %), 8,445 LUT,
13,259 FF, 10 DSP. The injector and histogram (Phase E) have ~27 BRAM36 left.

## CDC crossings

Three clocks: the core clock (MMCM), the PHY's `eth_rx_clk` and `eth_tx_clk` (25 MHz). The
datapath crosses only inside fpga-eth-core; everything of ours runs on the core clock.
Constraints: `constr/arty_timing.xdc` and eth-core's `constr/async_fifo.xdc`.

| # | Crossing | From → to | Synchronizer | Constraint |
|---|---|---|---|---|
| 1 | MII receive bytes (`eth_rx`) | rx_clk → core | eth-core `async_fifo`, Gray pointers | `set_max_delay -datapath_only` (async_fifo.xdc) |
| 2 | MII transmit bytes (`eth_tx`) | core → tx_clk | eth-core `async_fifo`, Gray pointers | same |
| 3 | `eth_tx` underrun flag | tx_clk → core | toggle into an ASYNC_REG chain (eth-core) | `set_max_delay -datapath_only` 8 ns |
| 4 | Reset requests (button, MMCM lock, MAC start) | any → core / rx_clk / tx_clk | `rtl/lib/cdc_reset_sync` (ASYNC_REG chain) | false path to stage 0 |

Not yet crossed: `eth_rx.sfd_o` (T0, rx_clk), unused until Phase E's timestamps.
