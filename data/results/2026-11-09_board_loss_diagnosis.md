# Board runs: where frames are lost (2026-11-09)

CSV: `2026-11-09_board_loss_diagnosis.csv`, one row per run (the first four are also in
`2026-11-09_first_board_run.csv`).

| | |
|---|---|
| Commit | bitstream `66abceb`; host/analysis as committed with this file (`replay` reports `max_lag_ms` in `--pps` mode; `compare` reports `unflagged_wrong`) |
| Host | WSL2, ASIX AX88179 via usbipd (`cdc_ncm`), qdisc `fq_codel` (default). **Load average 33–44 on 16 CPUs** during these runs (other sessions' Java/vvp jobs). Rows with empty `replay_max_lag_ms` predate that report. |
| Input | `2019-10-18_AAPL.itch` (MANIFEST): first 200,000 messages, or the whole extract (`day500`, 1,669,592 messages, 35,313 packets); `itch_source` = where replay read it from (`/mnt/d` 9P mount or a copy on the Linux disk, identical SHA-256) |
| Command | `scripts/board_run.sh NAME MODE PPS COUNT` (`ITCH=` for the source); `ncm_tx_timer_us` set with `echo N > /sys/class/net/enx8cae4caa0ec7/cdc_ncm/tx_timer_usecs`; the `min-spacing` rows used a pacing rule since reverted |

## Interpretation

- **No silently wrong book on any run.** `unflagged_wrong` (BBOs that differ from the
  model's BBO for the same cause message and lack the stale flag) is 0 on all six BBO runs:
  436,778 BBOs, four of the runs lossy.
- **Every loss was counted somewhere.** At the board, `rx_frames_ok + rx_fifo_overflow`
  equals the frames that reached it; frames missing from that sum (and `feed_gaps` with
  `rx_fifo_overflow` = 0, as in `day500`) were lost on the host before the wire.
- **Board-side loss (`rx_fifo_overflow`) follows host bursts.** `replay` sends late
  packets at once; its lag was 18 ms reading from `/mnt/d` (23 frames lost) and 5 ms from
  the Linux disk (4 lost) at 2,000 pps. In a back-to-back burst the board's output needs
  more wire time than its input (model, this slice: BBO-mode output/input wire time per
  packet p50 1.07, max 2.21; trace 1.32 / 1.90), so backpressure fills the 2 KB MII FIFO.
  Losses repeat at the same packets run to run (fixed file-read points).
- **A no-catch-up pacing rule made it worse** (`min-spacing` rows): under this load the
  sender loses ~35 % of its time, so the achieved rate fell to ~1,290 pps and losses rose.
  Reverted.
- **Host-side loss on the USB path:** `fq_codel` dropped 21 packets during `day500` (its
  5 ms target is exceeded when the usbipd path stalls); a stall of more than 3 s ended
  that run early (capture's `--idle 3`); and up to 2,217 of the board's output datagrams
  per run never reached capture (`host_seq_lost`).
- Conclusion: on this host, under this load, the measurement path loses data in three
  places outside the board. The design behaved as specified in every run.
