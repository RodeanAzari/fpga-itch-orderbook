# Board, 2026-11-13: on-chip injector run (ADR 0014), both eth_rx builds

CSV: `2026-11-13_injector_board.csv` (one row per run: the board's stats 42–44 and the
model's expected count and digest).

| | |
|---|---|
| Commit | RTL `2325efd`; bitstreams `build/arty_top/arty_top.bit` (store-and-forward, SHA-256 prefix `a87b6a3a9a418792`) and `build/arty_top_ct/arty_top.bit` (cut-through, `5219ac2a57acc5f5`), programmed with openFPGALoader v1.1.1 |
| Input | The injector ROM `data/samples/injector_aapl.mem` (in the bitstream): 1,043 messages of the AAPL fixture `data/samples/2019-10-18_AAPL_head50k.itch`, 22 MoldUDP64 payloads, 31,679 bytes. `python -m analysis.injector_image` rebuilds it byte-identical and prints the expected result per build |
| Host | WSL2, ASIX AX88179 via usbipd; the host only configures and reads stats (no feed sent) |
| Commands | `openFPGALoader -b arty_a7_100t <bit>`; `build/host-Release/capture --log data/runs/e4_inj_<build>[_rN].log --lo-price 2140700 --mode inject --bind-ip 192.168.10.1 --duration 5` (runs 2 and 3: `--duration 4`); each capture sends a fresh config, which resets the book and replays the image |
| Logs | `data/runs/e4_inj_{ct,sf,sf_r2,sf_r3}.log` (local) |

## Result

| Build | Runs | `inj_cycles` | `inj_bbos` | `inj_digest` | model |
|---|---|---|---|---|---|
| cut-through | 1 | 31,703 | 248 | `0x437daad2b849c4b8` | 248, `0x437daad2b849c4b8` |
| store-and-forward | 3 | 31,703 (each) | 248 | `0x437daadd4849c4b8` | 248, `0x437daadd4849c4b8` |

Every run: `book_records` 987 (the image's 518 Add, 458 Delete and 11 Executed messages),
`stale` 0, `feed_datagrams` 0, no BBO datagrams sent (mode 2 meters them on chip).

## Interpretation

- **The board equals simulation to the cycle.** test_core `inject_capacity` measured
  31,703 cycles in both builds (`docs/log.md` 2026-11-12), and the board gives the same
  count on every run. The two digests differ only in the `cut_through` flag each BBO
  carries; each equals the model's value for its build, so the 248 BBOs are the model's
  records in the model's order.
- **Capacity at `DATA_W` 8:** 1,043 messages in 31,703 cycles (253.6 µs at 8 ns) is
  4.11 M messages/s, 0.999 payload bytes per cycle. The injector feeds one byte per cycle,
  so this run is input-bound: the book kept up with this mix (mostly Adds and Deletes,
  pre-market). It is not a bound on the book itself; a 64-bit input path (ADR 0005) is
  needed to find that.
- The cut-through build was run once; the store-and-forward build three times, identical,
  showing the run is repeatable on hardware as in simulation.
