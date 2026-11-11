# Board, record FIFO (ADR 0012), 2026-11-11: T2−T1 is the cycle budget on every BBO

CSVs: `2026-11-11_record_fifo_board.csv` (percentiles), `_hist.csv` (count per cycle
value), `_t2gaps.csv` (waits above 24 cycles; only the 29s, see below), `_causes.csv`
(T2−T1 per cause message type and whether it emptied the best).

| | |
|---|---|
| Commit | RTL `c1dc2cb` (ADR 0012); bitstream `build/arty_top/arty_top.bit` SHA-256 prefix `ca05cc19423ecb43`, reports `docs/reports/arty_top/` (WNS +0.015 ns, 119 BRAM36) |
| Host | WSL2, ASIX AX88179 via usbipd, `pfifo` qdisc; load average 11.8 at the start |
| Input | `~/itch-cache/2019-10-18_AAPL.itch` (MANIFEST), 1,669,592 messages → 35,313 packets at 500 pps (replay max lag 10.2 ms, 6.09 Mb/s on the wire) |
| Commands | `make prog`; `ITCH=~/itch-cache/2019-10-18_AAPL.itch LO_PRICE=2140700 SESSION=20191018AA scripts/board_run.sh e2_rf bbo 500 0`; `python -m analysis.latency --log data/runs/e2_rf.log --out … --hist …`; `python -m analysis.t2_gaps --log … --out …`; `python -m analysis.latency_causes --log data/runs/e2_rf.log --packets data/runs/2019-10-18_AAPL_slice0.pkts --out …` |
| Log | `data/runs/e2_rf.log` (local), SHA-256 prefix `b8b6a64c7f9d5c86` |

## Result

`compare`: 831,842 / 831,842 BBO records identical to the model except t0/t1/t2; 0
datagrams lost; `rx_fifo_overflow` 0, `mold_gaps` 0, `stale` 0, `t0_lost` 0.

| Metric | n | min | p50 | p99 | p99.9 | max |
|---|---|---|---|---|---|---|
| T2−T1 (pipeline), cycles | 831,842 | 14 | 17 | 24 | 29 | 29 |
| T2−T1, ns | | 112 | 136 | 192 | 232 | 232 |
| T2−T0 (line-rate bound), µs | 831,842 | 74.2 | 126.7 | 133.1 | 133.7 | 133.8 |

Before (`2026-11-11_first_latency_board.md`, same day and bitstream minus ADR 0012):
T2−T1 p50 37, p99 125, max 13,348 cycles.

## Interpretation

**No BBO waited.** Every one of the 831,842 took exactly the no-wait cycle count of its
cause (`_causes.csv`):

| Cause | T2−T1 | BBOs |
|---|---|---|
| Add (in window) / Delete / Reduce, best level stays | 17 | 717,549 |
| Delete / Reduce emptying the best level | 23 | 95,006 |
| Replace | 24 | 17,521 |
| Replace whose old order was alone at the best | 29 | 1,762 |
| Add / Delete out of window | 14 | 2 |
| Delete emptying the best and its side (both BBOs show a zero size) | 18 | 2 |

The 29 and 18 cases were missing from `docs/latency.md`'s table; `test_book`
`latency_matches_header` now reproduces both in simulation (27 and 16 inside the book,
+2 for the decoder). So the pipeline latency is fully determined by the message type and
the book state: 136 ns for the common case, 232 ns worst on this day.

The record FIFO removed both effects found in the first run: the 65-cycle writer pacing
and the TX-burst outliers (no BBO waited, so the 545-record FIFO never filled; its peak
occupancy was not measured).
T2−T0 also dropped at the tail (max 274 → 134 µs): it no longer includes output waits and
is now serialization plus store-and-forward of the cause frame.

Limits: one day, one symbol, 500 pps replay (bursts inside each packet are at line rate;
packet spacing is not). A sustained output rate above ~185 k BBO/s (100 Mb/s TX) would
still fill the FIFO and stall the book. This day's bursts reach ~540 k/s for ~160 µs
(86 BBOs in 20k cycles, first run); the FIFO absorbed them.
