# Portfolio captures outstanding

Physical captures only (photos, phone video, scope, ILA). Claude generates plots itself.

## Phase A

None. The only media is a generated plot: `docs/media/2026-11-02_phase-a_aapl-bbo-day.png`.

## Phase B (when reached)

- **Dropped by the user 2026-11-22** (the README's board GIFs cover the live view; a photo of the board adds little): board photo cabled to the host.
- **Done 2026-11-21 (Claude):** ILA capture of one frame MII → FIFO → udp_rx → decoder,
  checked by `analysis/ila_frame.py`; plot via `make plots`, table in the README
  (`data/results/2026-11-21_ila_frame_board.md`). Optional, user: a GUI screenshot of the
  same capture (`read_hw_ila_data data/results/2026-11-21_ila_frame_board_core.ila`,
  then `display_hw_ila_data`).

## Phase C

- **Done 2026-11-22 (Claude):** the full day decoded on the board in trace mode
  (`data/results/2026-11-22_trace_full_day_board.md`) and the waveform of one packet across
  64-bit words (`make wave-feed`, `make plots`;
  `docs/media/2026-11-22_phase-c_packet-to-messages-w64.png`), in the README.

## Phase D: first correct BBO on hardware (2026-11-09) — deferred by the user to a later, more visual checkpoint

Result: `data/results/2026-11-09_first_board_run.md` (91,021/91,021 BBO updates match).
- **Dropped by the user 2026-11-22** (the README's board GIFs cover the live view; a photo of the board adds little): photo: the Arty cabled to the USB Ethernet adapter, during a replay
  (`scripts/board_run.sh demo bbo 500 200000`): LD4 heartbeat, LD5 "running" (config acked)
  and LD7 activity lit, PHY link/activity LEDs on the RJ45 visible. LD6 (stale/error) off in a clean 500 pps run.
- **Dropped by the user 2026-11-22** (the README's board GIFs cover the live view; a photo of the board adds little): phone video, 10–15 s, same shot during a replay (GIF ≤ 8 MB for the README; the video
  itself is not committed).
- Screenshot of the terminal showing `match=1` with `expected_records=fpga_records=91021`.

## Phase D: zero-mismatch full AAPL day on the board (2026-11-09)

Result: `data/results/2026-11-09_full_day_board.md` (831,842/831,842 BBOs). Better shot
than the 200k-message one above, which this replaces when taken.
- **Dropped by the user 2026-11-22** (the README's board GIFs cover the live view; a photo of the board adds little): photo of the board during a full-day replay (LD4, LD5, LD7 lit, LD6 off, RJ45 link LEDs),
  with the USB Ethernet adapter in frame.
- **Dropped by the user 2026-11-22** (the README's board GIFs cover the live view; a photo of the board adds little): phone video 10-15 s of the same, or a screen + board shot with `capture`'s per-second
  progress line scrolling (GIF ≤ 8 MB).
- Terminal screenshot of `compare`: `expected_records=831842 fpga_records=831842 match=1`.
Before the shoot: pause other sessions' heavy jobs; `scripts/board_run.sh` with
`ITCH=data/runs/2019-10-18_AAPL.itch ... bbo 500 0 --idle 30` (about 70 s).

## Phase D: busier day (2025-12-12) exact on the board with the 16-way table (2026-11-10)

Result: `data/results/2026-11-10_full_day_board_16way.md` (916,429/916,429 BBOs).
Done by Claude from run `d2_shoot` (`make demo-gif-day2`, `make plots`): the day-2 GIF, the
`compare` image (saved output, rendered) and the board-vs-model plot, all in the README.
- **Dropped by the user 2026-11-22** (the README's board GIFs cover the live view; a photo of the board adds little): the board photo/video from the section above, during any
  full-day run.

## Phase E: first latency histogram, and every BBO on its budget (2026-11-11)

Result: `data/results/2026-11-11_record_fifo_board.md` (T2−T1 p50 136 ns, max 232 ns).
Done by Claude: `docs/media/2026-11-11_phase-e_board-latency-t2-t1.png` (`make plots`):
before/after tail and the per-cause histogram.
- **Done 2026-11-23:** scope cross-check, T2−T1 = 137.0 ns on an isolated pair vs 136 ns from
  the timestamps (`data/results/2026-11-23_scope_t2_t1_board.md`); bench photo at the top of
  the README, cursor photo in its board section. The plan below is kept for reference.
- **Physical, user: scope cross-check (buildplan §6.5).** Pmod JA, top row: pin 1 = T1
  (`ja[0]`), pin 2 = T2 (`ja[1]`), pin 5 = GND (3.3 V LVCMOS, 32 ns pulses). Each pin rises
  1 cycle after its event, so rising edge to rising edge = T2 − T1.
  - Setup: CH1 on JA1, CH2 on JA2, both grounded to JA5; ≥ 100 MHz bandwidth, ≥ 500 MS/s;
    trigger on CH2 rising, 1 V; timebase 50 ns/div with the trigger at the right third.
  - During any full-day replay (`scripts/board_run.sh ... bbo 500 0`), the T1 edge just
    before the trigger is the cause: expect 112, 136, 184, 192 or 232 ns (14/17/23/24/29
    cycles). Most captures: 136 ns.
  - Captures: (1) one screenshot with cursors on a 136 ns pair; (2) if the scope has
    edge-to-edge delay statistics, its min/mean/max over ≥ 1,000 triggers (screenshot);
    (3) a photo of the probes on the Pmod header.
  - Expected disagreement: a few ns of pin-to-pin skew (IOB + traces), and T1 pulses from
    messages that changed no BBO (they have no T2 after them).

## Phase E: cut-through vs store-and-forward on the board (2026-11-13)

Result: `data/results/2026-11-13_ct_vs_sf_board.md` (T2−T0 p50 126.7 → 64.2 µs, every
BBO identical to the model in both builds). Done by Claude:
`docs/media/2026-11-13_phase-e_board-t2-t0-ct-vs-sf.png` (`make plots`).
- No physical capture needed. Optional: the scope setup above also works on the
  cut-through bitstream (`build/arty_top_ct/arty_top.bit`); T2 − T1 is the same there.

## Phase E: pipeline capacity, DATA_W 8 vs 64 on the board (2026-11-15)

Result: `data/results/2026-11-15_injector_board_w64.md` (14.4 M msg/s at 64, 3.50x at 8)
and `data/results/2026-11-15_w64_full_day_board.md` (full day = model at 64). Done by
Claude: `docs/media/2026-11-15_phase-e_board-capacity-w8-vs-w64.png` (`make plots`); the
100 Mb/s line on it is computed from the same packets, not measured.
- No physical capture needed. Still open for the §9 list: "messages/s at line rate"
  measured on the board (README `[TBD]`).

## Phase F: whole-file replay and the README's "In Vivado" grid (2026-11-20)

Result: `data/results/2026-11-19_whole_file_board.md` (302,347,067 messages, every symbol,
831,842 / 831,842 BBOs and the filter counters = model). README updated to §11.4 except
the media below.
- **Done 2026-11-21:** Vivado screenshots (user, via `make gui-views`) in the README's
  "In Vivado" grid, `docs/media/2026-11-21_phase-f_vivado-*.png`; power from
  `docs/reports/arty_top/power.rpt`.
- **Dropped by the user 2026-11-22** (the README's board GIFs cover the live view; a photo of the board adds little): demo video (not committed; link it from the README): board + live `analysis/bbo_view.py`
  during a full-day replay, with the final `compare` output.
