# Portfolio captures outstanding

Physical captures only (photos, phone video, scope, ILA). Claude generates plots itself.

## Phase A

None. The only media is a generated plot: `docs/media/2026-11-02_phase-a_aapl-bbo-day.png`.

## Phase B (when reached)

- Board photo cabled to the host, link LEDs visible.
- ILA screenshot: one frame through MII → FIFO → parser.

## Phase D: first correct BBO on hardware (2026-11-09) — deferred by the user to a later, more visual checkpoint

Result: `data/results/2026-11-09_first_board_run.md` (91,021/91,021 BBO updates match).
- Photo: the Arty cabled to the USB Ethernet adapter, during a replay
  (`scripts/board_run.sh demo bbo 500 200000`): LD4 heartbeat, LD5 "running" (config acked)
  and LD7 activity lit, PHY link/activity LEDs on the RJ45 visible. LD6 (stale/error) off in a clean 500 pps run.
- Phone video, 10–15 s, same shot during a replay (GIF ≤ 8 MB for the README; the video
  itself is not committed).
- Screenshot of the terminal showing `match=1` with `expected_records=fpga_records=91021`.

## Phase D: zero-mismatch full AAPL day on the board (2026-11-09)

Result: `data/results/2026-11-09_full_day_board.md` (831,842/831,842 BBOs). Better shot
than the 200k-message one above, which this replaces when taken.
- Photo of the board during a full-day replay (LD4, LD5, LD7 lit, LD6 off, RJ45 link LEDs),
  with the USB Ethernet adapter in frame.
- Phone video 10-15 s of the same, or a screen + board shot with `capture`'s per-second
  progress line scrolling (GIF ≤ 8 MB).
- Terminal screenshot of `compare`: `expected_records=831842 fpga_records=831842 match=1`.
Before the shoot: pause other sessions' heavy jobs; `scripts/board_run.sh` with
`ITCH=data/runs/2019-10-18_AAPL.itch ... bbo 500 0 --idle 30` (about 70 s).

## Phase D: busier day (2025-12-12) exact on the board with the 16-way table (2026-11-10)

Result: `data/results/2026-11-10_full_day_board_16way.md` (916,429/916,429 BBOs).
Done by Claude from run `d2_shoot` (`make demo-gif-day2`, `make plots`): the day-2 GIF, the
`compare` image (saved output, rendered) and the board-vs-model plot, all in the README.
- Still optional, physical only: the board photo/video from the section above, during any
  full-day run.

## Phase E: first latency histogram, and every BBO on its budget (2026-11-11)

Result: `data/results/2026-11-11_record_fifo_board.md` (T2−T1 p50 136 ns, max 232 ns).
Done by Claude: `docs/media/2026-11-11_phase-e_board-latency-t2-t1.png` (`make plots`):
before/after tail and the per-cause histogram.
- Physical (with the Pmod step, not yet built): scope capture of the T1 and T2 pulses on
  two channels during a replay, cursors on one T1→T2 pair, to cross-check the in-band
  timestamps (buildplan §6.5).
