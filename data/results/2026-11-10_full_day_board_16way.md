# Both AAPL days on the board with the 16-way table (2026-11-10): zero mismatches

CSV: `2026-11-10_full_day_board_16way.csv`. Every attempt is listed, failures included.

| | |
|---|---|
| Commit | bitstream from `15b3824` (ADR 0010: 2 × 2,048 × 16), `build/arty_top/arty_top.bit` SHA-256 prefix `e94d7179d6f1f235`, reports in `docs/reports/arty_top/` (`8b663e6`); `scripts/board_run.sh` at `8318c33` |
| Host | WSL2, ASIX AX88179 via usbipd (`cdc_ncm`, `tx_timer_usecs` 0), qdisc `pfifo limit 10000`; load average 15 to 22 on 16 CPUs (other sessions, not paused) |
| Input | Day 1: `2019-10-18_AAPL.itch`, 1,669,592 messages → 35,313 packets. Day 2: `2025-12-12_AAPL.itch`, 3,542,449 messages → 82,066 packets. Both from `~/itch-cache/` (SHA-256 in `2025-12-12_AAPL.md`) |
| Commands | `ITCH=~/itch-cache/2019-10-18_AAPL.itch LO_PRICE=2140700 SESSION=20191018AA scripts/board_run.sh <run> bbo 500 0` and `ITCH=~/itch-cache/2025-12-12_AAPL.itch LO_PRICE=2575300 SESSION=20251212AA scripts/board_run.sh d2_16way bbo 500 0` |
| Logs | `data/runs/` (local): `d1c_16way.log` SHA-256 prefix `6b1441d6f72f47d1`, `d2_16way.log` `3bbc0d2ff710ef89`, `d2_shoot.log` `30563e986610e2a5` (its `compare` output saved as `d2_shoot.compare.txt`) |

## Interpretation

**`d2_16way`: the whole 2025-12-12 AAPL day, 916,429 BBO updates, matches the golden model
byte for byte.** This is the day the 2 × 8,192 × 4 table could not hold (stale from
pre-market in the model). On the board: 82,066 frames received, 0 MII FIFO overflows, 0
feed gaps, 3,511,772 book records, `table_overflow` and `unknown_ref` 0, never stale,
`price_oow` 69,799 and `bbo_oow` 3 as the model predicts. All 442,417 output datagrams were
received.

**`d1c_16way`: the whole 2019-10-18 day, 831,842 BBO updates, byte for byte** on the new
table, as on the 4-way table on 2026-11-09.

Fixed 500 pps (70.6 s for day 1, 164 s for day 2), not real-time pacing.

The two failed day-1 attempts were caused by host load, and both were detected:
- `d1_16way`: the board's book was exact (all counters equal the model; 831,842
  `out_records`), but the host lost 138 of 224,603 output datagrams on receive. The
  interface and socket counters were not snapshotted before this run, so the split
  between the USB/NCM path and the socket is unknown. `compare` aligns by position, so
  everything after the first lost datagram counts as mismatched; `unflagged_wrong` 0.
- `d1b_16way`: replay bursts (max lag 44 ms, load 21.7) overflowed the 2 KB MII FIFO 3
  times. The feed gap made the book stale, and every BBO after the gap is flagged stale.
  `unflagged_wrong` 0. Host receive lost nothing in this run (counters snapshotted).

**`d2_shoot` (23:01, a fifth run for the media, board reprogrammed with the same bitstream):**
day 2 exact again, 916,429/916,429, load 5.1 at the start. 0 MII FIFO overflows, 0 feed
gaps, never stale. capture's `seq_lost` 3 and `seq_backwards` 3: three output datagrams
arrived out of order on the host and were not lost (`compare`: `lost_datagrams=0`).
`board_rx_frames_ok` 82,069 = 82,066 feed + 3 non-feed frames (`feed_filtered` 3). Media
rendered from this run's log by `make demo-gif-day2`:
`docs/media/2026-11-10_phase-d_board-full-day2-bbo.gif` (the day replayed through
`analysis/bbo_view.py`) and `docs/media/2026-11-10_phase-d_board-compare-day2.png` (the saved
`compare` output drawn by `analysis/term_shot.py`, a rendering, not a screen grab).

So at load 15 to 22 the clean rate was 2 of 4 runs. The clean runs had the lowest replay lag
(24 ms). The deeper MII FIFO (open question in `progress.md`) would absorb these bursts.
