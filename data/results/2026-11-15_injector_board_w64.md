# Board, 2026-11-15: on-chip injector run at DATA_W 64 (ADR 0015 step 5)

CSV: `2026-11-15_injector_board_w64.csv` (one row per run: the board's stats and the
model's expected count and digest).

| | |
|---|---|
| Commit | RTL `cb5254c` + `arty_top`/`build_top.tcl` DATA_W argument (committed with this file); bitstream `build/arty_top_w64/arty_top.bit` (store-and-forward, DATA_W 64, SHA-256 prefix `adc3853abeacffd2`), programmed with openFPGALoader v1.1.1 |
| Build | `vivado -mode batch -source ../syn/build_top.tcl -tclargs 8 sf 64` (from build/), Vivado 2025.2: core 8.000 ns, WNS +0.004 ns, WHS +0.051 ns, 127 BRAM tiles, 13,568 LUTs, 21,232 FFs (`docs/reports/arty_top_w64/`) |
| Input | The injector ROM `data/samples/injector_aapl_w64.mem` (in the bitstream): the same 1,043 messages, 22 MoldUDP64 payloads and 31,679 bytes as the DATA_W 8 image, 8 bytes per word. `python -m analysis.injector_image` rebuilds it byte-identical and prints the expected result |
| Host | WSL2, ASIX AX88179 via usbipd; the host only configures and reads stats (no feed sent) |
| Commands | `openFPGALoader -b arty_a7_100t <bit>`; `build/host-Release/capture --log data/runs/e5_inj_w64_r{1,2,3}.log --lo-price 2140700 --mode inject --bind-ip 192.168.10.1 --duration 4` |
| Logs | `data/runs/e5_inj_w64_r{1,2,3}.log` (local) |

## Result

| Build | Runs | `inj_cycles` | `inj_bbos` | `inj_digest` | model |
|---|---|---|---|---|---|
| store-and-forward, DATA_W 64 | 3 | 9,059 (each) | 248 | `0x437daadd4849c4b8` | 248, `0x437daadd4849c4b8` |
| store-and-forward, DATA_W 8 (2026-11-13) | 3 | 31,703 (each) | 248 | `0x437daadd4849c4b8` | same |

Every run: `book_records` 987, `stale` 0, `feed_datagrams` 0.

## Interpretation

- **The board equals simulation to the cycle at DATA_W 64 too.** test_core
  `inject_capacity` gives 9,059 cycles at 64 (ADR 0015); all three board runs give 9,059.
  The digest is the model's store-and-forward value, so the 248 BBOs are the model's
  records in the model's order: the 64-bit path changes timing, not the book.
- **Capacity at DATA_W 64:** 1,043 messages in 9,059 cycles (72.5 µs at 8 ns) is
  14.4 M messages/s, 3.50× the DATA_W 8 figure (4.11 M/s) on the same image, above the
  roughly 2×–3× ADR 0015 expected.
- **No longer input-bound:** 31,679 payload bytes in 9,059 cycles is 3.50 bytes per cycle
  against 8 the input could supply, so some later stage now sets the rate (8.7 cycles per
  message on average). This run does not say which stage; that would need per-stage
  stall counters. The figure holds for this mix only (pre-market, mostly Add/Delete).
- Only the store-and-forward build was built at 64; cut-through at 64 was not built.
