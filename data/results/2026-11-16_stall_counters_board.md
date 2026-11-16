# Board, 2026-11-16: stall counters on a full day, unpaced and at 4,000 pps

CSV: `2026-11-16_stall_counters_board.csv`, one row per run. Follows
`2026-11-15_line_rate_board.md`, which attributed the overflows to the output by
arithmetic alone. Stats 45 and 46 (`docs/protocol.md`) now measure that cause directly.

| | |
|---|---|
| Commit | `f81a9f6`. `build/arty_top/arty_top.bit` (DATA_W 8, store-and-forward), SHA-256 prefix `000189e08975e722`; Vivado 2025.2, 125 MHz, WNS +0.012 ns (`docs/reports/arty_top/`); programmed with openFPGALoader before each run |
| Host | WSL2, ASIX AX88179 via usbipd, `pfifo` 10,000, NCM TX timer 0; **load average 12.3** (other sessions' jobs) |
| Input | `~/itch-cache/2019-10-18_AAPL.itch` (MANIFEST) → `data/runs/2019-10-18_AAPL_slice0.pkts`, 1,669,592 messages, 35,313 packets |
| Commands | `ITCH=~/itch-cache/2019-10-18_AAPL.itch LO_PRICE=2140700 SESSION=20191018AA scripts/board_run.sh <run> bbo <pps> 0` (`m1_flat` pps 0 = unpaced; `m2_p4000` 4000); `python -m analysis.compare --log data/runs/<run>.log --packets data/runs/2019-10-18_AAPL_slice0.pkts` |
| Logs | `data/runs/m1_flat.log` SHA-256 prefix `9e80078bc610bcb3`, `data/runs/m2_p4000.log` `2641d2d219961cbf` (local) |

## Result

| Run | Offered | `out_stall_cycles` | `rx_stall_cycles` | MII FIFO overflows | Stale | Output datagrams lost by the host |
|---|---|---|---|---|---|---|
| unpaced | 6,533 pps, 309 k msg/s | **248,625** (1.99 ms) | **239,413** (1.92 ms) | 2 | yes (flagged) | 44,020 |
| 4,000 pps | 189 k msg/s, replay max lag 18.3 ms | **0** | **0** | 0 | never | 1,089 of 210 k |

Both runs: `unflagged_wrong` 0. A 20,000-message sanity run at 500 pps before them gave
2,087 / 2,087 BBOs, `match=1`.

## Interpretation

- **On the board, the output stalls the feed.** Unpaced, the book waited 248,625 cycles on
  a full record FIFO, meaning the 100 Mb/s TX was behind. The feed waited almost as long at
  eth_rx's output (239,413). At 12.5 bytes/µs, the 2 KB MII FIFO fills in about 164 µs of
  feed stall, and it overflowed twice. Both overflows were counted, and the book was
  flagged stale. This confirms the 2026-11-15 arithmetic with a hardware count.
- **At 4,000 pps nothing stalls, even with bursts.** The replayer fell up to 18 ms behind
  and caught up back to back, and the 512-record FIFO absorbed it: zero stall cycles. Every
  BBO received equals the model.
- **The host on this run was loaded (load 12),** so the unpaced run reached 6,533 pps, not
  the 7,919 of the quiet 2026-11-15 run. Host-side output loss was larger too: 44,020 and
  1,089 datagrams. Linux counted none of them (as on 2026-11-15). Still no run is
  `match=1` at full length; the cause sits outside the board's counters.
