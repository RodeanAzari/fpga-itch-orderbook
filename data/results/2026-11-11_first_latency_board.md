# First timestamped board run (2026-11-11): T2−T1 and T2−T0 over the whole 2019-10-18 AAPL day

CSVs: `2026-11-11_first_latency_board.csv` (percentiles), `_hist.csv` (count per cycle value).

| | |
|---|---|
| Commit | RTL `c12b7e0` (ADR 0011 timestamps; one RAM per table bank); bitstream `build/arty_top/arty_top.bit` SHA-256 prefix `2fe07dba58c484a6`, reports `docs/reports/arty_top/` (WNS +0.038 ns); `analysis/latency.py` at `d7982a1` |
| Host | WSL2, ASIX AX88179 via usbipd, `pfifo` qdisc; load average 9.7 at the start (other sessions) |
| Input | `~/itch-cache/2019-10-18_AAPL.itch` (MANIFEST; SHA-256 in `2019-10-18_AAPL.md`), 1,669,592 messages → 35,313 packets at 500 pps (replay max lag 50 ms) |
| Commands | `ITCH=~/itch-cache/2019-10-18_AAPL.itch LO_PRICE=2140700 SESSION=20191018AA scripts/board_run.sh e1_ts bbo 500 0`, then `python -m analysis.latency --log data/runs/e1_ts.log --out … --hist …` |
| Log | `data/runs/e1_ts.log` (local), SHA-256 prefix `54663b00725362ab` |

## Result

`compare`: 831,842 / 831,842 BBO records identical to the model in every byte except
t0/t1/t2; 0 datagrams lost; `t0_lost` 0; 0 MII FIFO overflows, 0 feed gaps, never stale.

| Metric | n | min | p50 | p99 | p99.9 | max |
|---|---|---|---|---|---|---|
| T2−T1 (pipeline), cycles | 831,842 | 14 | 37 | 125 | 125 | 13,348 |
| T2−T1, ns | | 112 | 296 | 1,000 | 1,000 | 106,784 |
| T2−T0 (line-rate bound), µs | 831,842 | 74.2 | 128.4 | 139.2 | 142.4 | 274.3 |

## Interpretation (partly open)

- **The no-wait cases equal the cycle budget** (`docs/latency.md`): 17 cycles is the most
  common value (298,973 BBOs, 35.9 %), 23 (Delete emptying the best) 4.6 %, 14 the minimum.
- **The rest is waiting inside a packet.** Grouped by the cause message's index in its
  MoldUDP64 packet: indices 0–4 have p50 17; from index 5 on, p50 is about 40, with about
  24 % at 100 cycles or more, flat to the end of the packet. A frame leaves eth_rx's
  store-and-forward buffer at one byte per cycle, so a packet's messages reach the book
  in a burst faster than one message per 17 cycles.
- **Open:** sharp peaks at 102 (83,814), 119 (48,914) and 125 (15,917) cycles, and 19
  values from 1,457 to 13,348 cycles. Fixed values point to a fixed-length stall,
  probably the output side (`out_builder` holding the book while it starts or ends a
  datagram), not yet verified. **This distribution is not yet a headline number.** It will be
  explained, and re-measured if the design changes, before the README reports it.
- T2−T0 includes the frame's 100 Mb/s serialization (up to 1,472 payload bytes ≈ 121 µs)
  and the store-and-forward wait for its FCS, as expected. T0 is uncorrected (the 18-cycle
  constant in `docs/latency.md` is negligible here).
