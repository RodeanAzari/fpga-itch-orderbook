# Full AAPL day on the board, BBO mode, 500 pps (2026-11-09)

CSV: `2026-11-09_full_day_board.csv`.

| | |
|---|---|
| Commit | bitstream `66abceb`; host/analysis `63d1d40` |
| Host | WSL2, ASIX AX88179 via usbipd (`cdc_ncm`, `tx_timer_usecs` 0), qdisc **`pfifo limit 10000`** (replacing `fq_codel`); load average 28–33 on 16 CPUs (other sessions) |
| Input | `2019-10-18_AAPL.itch` (MANIFEST, copy on the Linux disk), all 1,669,592 messages → 35,313 packets + end of session |
| Command | `ITCH=data/runs/2019-10-18_AAPL.itch scripts/board_run.sh day500b bbo 500 0 --idle 30` |

## Interpretation

- The whole day reached the board: `rx_frames_ok` 35,314 + `rx_fifo_overflow` 1 = 35,313
  feed packets + end of session + the config. The host dropped nothing (`pfifo` 0 drops)
  and lost no output datagram (`host_seq_lost` 0).
- **The first 408,158 BBO updates match the golden model byte for byte** (49 % of the
  day's 831,842). Then the board's MII FIFO dropped one frame: `replay` lagged up to 139 ms
  under the host load and sent the late packets back to back (mechanism:
  `2026-11-09_board_loss_diagnosis.md`).
- After the loss the book is stale and every BBO says so: `unflagged_wrong` 0 of 619,150.
- Not yet a zero-mismatch day: that needs a host without 100 ms stalls (other sessions
  idle), or a board that absorbs such bursts. Not a real-time replay: fixed 500 pps.
