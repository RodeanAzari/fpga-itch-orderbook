# Latency: definitions, cycle budget, T0 calibration

Timestamps are core-clock cycle counts (125 MHz, 8 ns) from one free-running 64-bit counter
(ADR 0011). Every BBO record carries T0, T1, T2 (`docs/protocol.md`).

| Point | Event | Precision |
|---|---|---|
| T0 | the cause frame's SFD, synchronized into the core domain | ±40 ns (25 MHz nibble clock) + ±1 cycle (synchronizer phase), minus the constant below |
| T1 | `itch_decoder` accepts the cause message's last byte | exact |
| T2 | the BBO is valid at the book's output (first cycle; held while the output stalls) | exact |

**Headline: T2 − T1, the pipeline latency.** T2 − T0 includes the frame's serialization at
100 Mb/s (80 ns per byte) and the store-and-forward wait for its FCS, so it is the
line-rate bound, not the pipeline.

## Cycle budget, T1 → T2

Cycle 0 is T1. Each stage's latency is from its module header; each is measured by that
block's test (`latency_matches_header`).

| Stage | Cycles | Source |
|---|---|---|
| `itch_decoder`: last byte → record valid (type/length check, then locate/side check and record) | 2 | `rtl/feed/itch_decoder.sv` |
| `order_table`: record handshake → first delta valid (hash, address, BRAM read, BRAM register, match, act, beat); a Replace's second delta 7 later | 7 (+7 for U) | `rtl/book/order_table.sv` |
| `book_levels`: delta handshake → BBO valid | 8 normal; 14 when the best level empties; 9 when that leaves the side empty; 5 out of window; 4 no change. A Replace's first delta frees it after 6, or 12 if it empties the best | `rtl/book/book_levels.sv` |
| **T2 − T1 with no wait** | **17** normal; **23** best empties; **18** side empties; **14** out of window; **24** Replace (**21** if its new price is out of window; **29** if its old order was alone at the best) | sum |

Anything above these values is waiting: the book handles one message at a time (a U
takes two deltas), and its output waits when out_builder's record FIFO is full (ADR 0012:
512 + 32 BBOs queued behind the 100 Mb/s TX). Before ADR 0012 the byte writer took one
record per 65 cycles, which paced the book inside every packet (the board's 102/119/125
peaks, `data/results/2026-11-11_first_latency_board.md`).

Measured in simulation (`test_core`, 2026-11-11, one seeded run, 94 BBOs; `make sim` logs
it every run): every no-wait case lands exactly on its budget: 14 (Add out of window, 1),
17 (Add 34, Reduce 15, Delete 1), 21 (Replace 2), 23 (Delete emptying the best, 3), 24
(Replace 8). The rest (18 to 125 cycles) waited for the previous message or an output
stall; this test sends at line rate with heavy output, so it is not a distribution to
report.

Measured on the board (day 1, 831,842 BBOs, `data/results/2026-11-11_record_fifo_board.md`):
every BBO lands exactly on its no-wait budget (17: 86 %, 23: 11 %, 24: 2 %, 29: 0.2 %,
14 and 18: 2 each); T2−T1 p50 17 / p99 24 / max 29 cycles (136 / 192 / 232 ns).

## T0 calibration

From the SFD's last nibble on the pins:

| Step | Delay | Source |
|---|---|---|
| `mii_rx`: input register, SFD register → `sfd_o` | 2 rx_clk (80 ns = 10 cycles) | eth-core `rtl/mii_rx.sv` header |
| `cdc_pulse` + `t0_track` capture: `sfd_o` → counter captured | 8 cycles in simulation (fixed clock phases) | `test_core`, every BBO |
| **Total** | **18 cycles (144 ns)** in simulation; on hardware the synchronizer adds 0 or 1 cycle of phase, to be checked with the ILA | |

T0 as sent is the captured count. Subtracting the constant gives the SFD time.
