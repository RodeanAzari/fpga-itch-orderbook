# Full AAPL day on the board, BBO mode, 500 pps (2026-11-09): zero mismatches

CSV: `2026-11-09_full_day_board.csv`.

| | |
|---|---|
| Commit | bitstream `66abceb`; host/analysis `63d1d40` |
| Host | WSL2, ASIX AX88179 via usbipd (`cdc_ncm`, `tx_timer_usecs` 0), qdisc **`pfifo limit 10000`** (replacing `fq_codel`); load average 28–33 on 16 CPUs (other sessions) |
| Input | `2019-10-18_AAPL.itch` (MANIFEST, copy on the Linux disk), all 1,669,592 messages → 35,313 packets + end of session |
| Command | `ITCH=data/runs/2019-10-18_AAPL.itch scripts/board_run.sh <run> bbo 500 0 --idle 30` |
| Runs | `day500b`: host load average ~30 (other sessions). `day500c`: other sessions' jobs paused, load average 2.6–3.2 (two single-threaded simulations still running); capture log SHA-256 prefix `b184e8fd0d7da6a8` |

## Interpretation

**`day500c`: the whole AAPL day, 831,842 BBO updates, matches the golden model byte for
byte.** 35,313 frames sent, 35,313 received by the board (`rx_frames_ok`; the config is
not counted, as a config clears the counters), 0 MII FIFO overflows, 0 feed gaps, book
never stale, 0 capacity counters (`table_overflow`, `unknown_ref` 0), 226,235 output
datagrams all received. `replay` lag max 1.4 ms. Fixed 500 pps (70.6 s), not real-time
pacing; the out-of-window BBOs are flagged as the model flags them (ADR 0002).

**Reproduced** in `shoot500` and `demo` (same command, quiet host, replay lag max 7.2 and
41.3 ms): 831,842/831,842 each, 0 frames lost. Three zero-mismatch days in a row.
`docs/media/2026-11-09_phase-d_board-full-day-bbo.gif` is rendered from the `demo` run's
capture log (`make demo-gif`: `analysis/bbo_view.py --gif`, 200 frames spaced by BBO count;
the log is local, `data/runs/demo.log`).

`day500b` (the earlier run, loaded host):

- The whole day reached the board: `rx_frames_ok` 35,314 + `rx_fifo_overflow` 1 = 35,313
  feed packets + end of session + the config. The host dropped nothing (`pfifo` 0 drops)
  and lost no output datagram (`host_seq_lost` 0).
- **The first 408,158 BBO updates match the golden model byte for byte** (49 % of the
  day's 831,842). Then the board's MII FIFO dropped one frame: `replay` lagged up to 139 ms
  under the host load and sent the late packets back to back (mechanism:
  `2026-11-09_board_loss_diagnosis.md`).
- After the loss the book is stale and every BBO says so: `unflagged_wrong` 0 of 619,150.
- The difference between the two runs is the host: a 139 ms replay stall under load vs.
  1.4 ms on a quiet host. `day500b`'s 2 extra frames are `feed_filtered` (host traffic).
