# Board, 2026-11-15: full AAPL day at and below line rate, BBO mode

CSV: `2026-11-15_line_rate_board.csv`, one row per run.

| | |
|---|---|
| Commit | RTL as `a854f41` (`build/arty_top_w64/arty_top.bit`, DATA_W 64, store-and-forward, SHA-256 prefix `adc3853abeacffd2`, WNS +0.004 ns), programmed with openFPGALoader before every run; analysis at `fbcb484` |
| Host | WSL2, ASIX AX88179 via usbipd (`enx8cae4caa0ec7`), `pfifo` limit 10,000, NCM TX timer 0; load average 5.7-7.1 |
| Input | `~/itch-cache/2019-10-18_AAPL.itch` (MANIFEST) → `data/runs/2019-10-18_AAPL_slice0.pkts`, 1,669,592 messages, 35,313 packets |
| Commands | `ITCH=~/itch-cache/2019-10-18_AAPL.itch LO_PRICE=2140700 SESSION=20191018AA scripts/board_run.sh <run> bbo <pps> 0` (pps 0 = unpaced); `python -m analysis.compare --log data/runs/<run>.log --packets data/runs/2019-10-18_AAPL_slice0.pkts` |
| Logs | `data/runs/l1_flat.log`, `l2_p{6000,5000,4000,3000}.log` (local) |

## Result

| Run | Offered | MII FIFO overflows | Book stale | Output datagrams lost by the host | Unflagged wrong BBOs |
|---|---|---|---|---|---|
| unpaced | 7,919 pps, 374 k msg/s (wire-bound) | 3 | yes, from seq ~81 k | 32,091 | 0 |
| 6,000 pps | 284 k msg/s | 2 | yes | 2,437 | 0 |
| 5,000 pps | 236 k msg/s | 1 | yes | 1,306 | 0 |
| 4,000 pps | 189 k msg/s | **0** | **never** | 2 of 209,963 | 0 |
| 3,000 pps | 142 k msg/s | **0** | **never** | 29 of 214,530 | 0 |

## Interpretation

- **Input at wire speed is not the limit.** Unpaced, the host put the whole day on the wire
  in 4.46 s. The board received 35,310 of 35,313 frames with no CRC or framing error. The 3
  missing frames are counted MII FIFO overflows, and the book was flagged stale from the
  first one. Every BBO after it carries the flag (CLAUDE.md: never a silently wrong book).
- **With every BBO sent, the 100 Mb/s TX link is the limit.** Each BBO is a 64-byte
  record. Even at the best packing (22 records per datagram), this day needs at least 1.05×
  the input wire bytes on the output: 1.19× around the first overflow and up to 1.37× in its
  densest 200-frame windows (computed from the clean run `e5_w64`'s BBO counts). When the
  output falls behind, the record FIFO fills and the book stalls, and the MII input cannot
  be backpressured, so its 2 KB FIFO overflows. This is the arithmetic, not a stall
  counter; no counter names the stalled stage yet.
- **The replayer adds bursts.** At 5,000 and 6,000 pps it fell 15-21 ms behind and caught
  up back-to-back (`max_lag_ms`). At 4,000 pps the lag was 2 ms and the board was clean.
- **The host loses a few output datagrams before Linux sees them.** Linux counted no drop:
  UDP RcvbufErrors 0, adapter rx dropped/missed 0, softnet dropped 0. The loss sits in the
  AX88179/usbip path or the FPGA's TX, and these runs cannot tell which. So no run here is
  `match=1`. At 4,000 pps every received BBO equals the model (unflagged_wrong 0, stale 0).
  A full-day exact match at this rate is not claimed.
