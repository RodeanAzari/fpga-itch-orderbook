# Board, 2026-11-13: cut-through vs store-and-forward eth_rx, same day, same feed

CSVs: `2026-11-13_ct_vs_sf_board.csv` (percentiles per build, plus the per-record pairing
`T2-T0 base-this` = store-and-forward minus cut-through), `_hist.csv` (count per cycle
value, per build). Plot: `docs/media/2026-11-13_phase-e_board-t2-t0-ct-vs-sf.png`
(`make plots`).

| | |
|---|---|
| Commit | RTL `2325efd` (eth-core-v0.3 submodule; ADR 0013 cut-through option); analysis at `22aed41` plus `--base` in `analysis/latency.py` (this commit) |
| Bitstreams | `build/arty_top/arty_top.bit` (store-and-forward) SHA-256 prefix `a87b6a3a9a418792`, WNS +0.048 ns; `build/arty_top_ct/arty_top.bit` (`CUT_THROUGH=1`) `5219ac2a57acc5f5`, WNS +0.172 ns (`docs/reports/arty_top_ct/`). Both built from `2325efd`; programmed with openFPGALoader v1.1.1 (SRAM) |
| Host | WSL2, ASIX AX88179 via usbipd (`enx8cae4caa0ec7`, now on its own laptop port), `pfifo` limit 10,000, TX coalescing 0; load average 13.8 (sf run) and 11.7 (ct run) at the start, other sessions |
| Input | `~/itch-cache/2019-10-18_AAPL.itch` (MANIFEST), 1,669,592 messages → 35,313 packets at 500 pps, 6.09 Mb/s on the wire (replay max lag 16.0 ms sf, 33.8 ms ct) |
| Commands | `openFPGALoader -b arty_a7_100t <bit>`; `ITCH=~/itch-cache/2019-10-18_AAPL.itch LO_PRICE=2140700 SESSION=20191018AA [CUT_THROUGH=1] scripts/board_run.sh e3_{sf,ct} bbo 500 0`; `python -m analysis.latency --log data/runs/e3_sf.log --out … --hist …`; `python -m analysis.latency --log data/runs/e3_ct.log --base data/runs/e3_sf.log --out … --hist …`; the two outputs joined with a `build` column |
| Logs | `data/runs/e3_sf.log` SHA-256 prefix `51fc3b0cf49cdfb8`, `data/runs/e3_ct.log` `75d8206903cc80e0` (local) |

## Result

Both builds: `compare` 831,842 / 831,842 BBO records identical to the model except
t0/t1/t2 (cut-through compared with `--cut-through`: every record carries the
`cut_through` flag, and fails without it); 0 datagrams lost; `rx_frames_ok` 35,313,
`rx_crc_err`/`rx_err`/`rx_fifo_overflow` 0, `mold_gaps` 0, `stale` 0, `t0_lost` 0,
`cut_through_bad` 0. The two runs' records are identical to each other but for the times
and the flag.

| Build | Metric | n | min | p50 | p99 | p99.9 | max |
|---|---|---|---|---|---|---|---|
| both | T2−T1 (pipeline), cycles | 831,842 | 14 | 17 | 24 | 29 | 29 |
| store-and-forward | T2−T0 (line-rate bound), µs | 831,842 | 74.2 | 126.7 | 133.1 | 133.7 | 133.8 |
| cut-through | T2−T0 (line-rate bound), µs | 831,842 | 7.2 | 64.2 | 120.5 | 121.6 | 121.7 |
| paired, per record | T2−T0 sf minus ct, µs | 831,842 | 7.2 | 62.6 | 113.2 | 114.8 | 115.1 |

T2−T1 histograms are identical bin for bin between the builds (`_hist.csv`), as they
should be: cut-through changes only what happens before T1. Store-and-forward repeats the
2026-11-11 record-FIFO run exactly (same percentiles).

## Interpretation

- **Cut-through takes the frame's tail off T2−T0.** No BBO was slower on cut-through
  (paired minimum +7.2 µs). In store-and-forward a message reaches the decoder only after
  its frame's FCS is checked and the buffer is read out, so a frame's first message waits
  for the whole frame. In cut-through it is decoded as its bytes arrive, so the saving is
  largest for messages early in a long frame (up to 115 µs, about 1,440 bytes at 80 ns).
  That reading fits the numbers; the paired saving has not been broken down by position in
  the frame.
- **The cut-through T2−T0 is still serialization-bound.** Its minimum, 7.2 µs, is a
  message near the start of a frame: header and first message bytes at 80 ns each. Its
  maximum, 121.7 µs, is the last message of a full frame. T2−T1 (136 ns p50) is the
  pipeline headline; T2−T0 is not.
- **Output packing changed.** Store-and-forward sent 219,431 datagrams (3.8 BBOs each),
  cut-through 767,408 (1.08 each). out_builder flushes on idle with no timer
  (`rtl/out/out_builder.sv`): a frame read out of the store-and-forward buffer at a byte per
  cycle yields a burst of BBOs that share datagrams; at wire speed the BBOs arrive
  microseconds apart and mostly go one per datagram. About 10.4 k datagrams/s, within the
  100 Mb/s TX path here; a heavier feed would push it closer to its limit.
- **Host reordering, store-and-forward run:** capture counted `seq_gaps` 1, `seq_lost` 3,
  `seq_backwards` 3: one datagram arrived three places early. Nothing was lost or
  duplicated (compare reorders by `pkt_seq`). This is the first reordering seen on the
  ASIX adapter; it is a host receive effect, not a board one.
- T0 is uncorrected (the synchronizer constant in `docs/latency.md` is negligible at this
  scale).
