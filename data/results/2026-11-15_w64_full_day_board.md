# Board, 2026-11-15: full AAPL day at DATA_W 64 (ADR 0015 step 5)

CSV: `2026-11-15_w64_full_day_board.csv` (latency percentiles, DATA_W 64 run and the
DATA_W 8 store-and-forward baseline `e3_sf` from 2026-11-13);
`2026-11-15_w64_full_day_board_hist.csv` (cycles,count per metric for the 64 run).

| | |
|---|---|
| Commit | as `2026-11-15_injector_board_w64.md`; bitstream `build/arty_top_w64/arty_top.bit` (store-and-forward, DATA_W 64, SHA-256 prefix `adc3853abeacffd2`) |
| Input | `~/itch-cache/2019-10-18_AAPL.itch` (MANIFEST), 1,669,592 messages → 35,313 packets at 500 pps in 70.6 s, 6.09 Mb/s on the wire (replay max lag 69.9 ms) |
| Commands | `ITCH=~/itch-cache/2019-10-18_AAPL.itch LO_PRICE=2140700 SESSION=20191018AA scripts/board_run.sh e5_w64 bbo 500 0`; `python -m analysis.latency --log data/runs/e5_w64.log --base data/runs/e3_sf.log --out … --hist …`; `python -m analysis.latency --log data/runs/e3_sf.log --out …`; joined with a `build` column |
| Logs | `data/runs/e5_w64.log` SHA-256 prefix `21eb4979c1c793f8`; baseline `data/runs/e3_sf.log` `51fc3b0cf49cdfb8` (local) |

## Result

`compare`: 831,842 / 831,842 BBO records match the model, 219,806 datagrams, 0 lost,
0 duplicated, 0 mismatched positions, `unflagged_wrong` 0.

| Build | T2−T1 p50 / p99 / p99.9 / max | T2−T0 p50 / p99 / max |
|---|---|---|
| DATA_W 64 | 17 / 24 / 29 / 29 cycles (136 / 192 / 232 / 232 ns) | 126.80 / 133.20 / 133.84 µs |
| DATA_W 8 (e3_sf) | 17 / 24 / 29 / 29 cycles | 126.74 / 133.14 / 133.81 µs |

Paired per BBO (same feed, same mold_seq), T2−T0 at 64 minus at 8: min 2, p50 7,
max 12 cycles (16–96 ns).

## Interpretation

- **The 64-bit feed path gives a correct book on hardware:** the full day matches the
  model record for record, as at DATA_W 8.
- **T2−T1 is unchanged, as designed.** T1 is stamped when `itch_decoder` accepts the
  message's last byte (`docs/latency.md`). The book and output path after it is the same
  RTL at both widths, so the same no-wait budget (17/24/29) holds.
- **T2−T0 grows 2–12 cycles at 64.** The byte packer and message aligner sit between T0
  and T1. On a 100 Mb/s feed the bytes arrive at 1 per 10 core cycles, so the wider path
  cannot help this metric (it is serialization-bound, 80 ns/byte). It only adds its own
  pipeline stages. DATA_W 64 helps capacity (`2026-11-15_injector_board_w64.md`: 3.50×),
  not latency at line rate.
- One run only; store-and-forward only. The host was quiet (load ~3–5) and no feed
  frame was dropped.
