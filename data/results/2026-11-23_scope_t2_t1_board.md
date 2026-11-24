# Board, 2026-11-23: T2−T1 on an oscilloscope = the board's own timestamps (Phase E)

CSV: `2026-11-23_scope_t2_t1_board.csv` (the scope reading and the board's p50),
`2026-11-23_scope_t2_t1_board_hist.csv` (the board's T2−T1 histogram for the same feed).
Photos: `docs/media/2026-11-23_phase-e_scope-setup.webp` (the bench),
`docs/media/2026-11-23_phase-e_scope-t2-t1-cursors.webp` (the reading).

| | |
|---|---|
| Commit | Bitstream: release build of the RTL at `8455bcf`, `build/arty_top/arty_top.bit` (DATA_W 8, store-and-forward, 125 MHz), SHA-256 prefix `dc1168385748c72f`; programmed with openFPGALoader. Analysis at this file's commit |
| Scope | Rigol DS1102Z-E (100 MHz, 2 channels), ×10 probes, DC coupling, 1 V/div, 50 ns/div, 500 MSa/s (2 ns per sample) with both channels on; trigger edge, CH2 rising, 1.54–1.58 V, Normal sweep |
| Probing | Pmod JA pin 1 = T1 pulse (`ja[0]`) → CH1, pin 2 = T2 pulse (`ja[1]`) → CH2, both grounds on pin 5. Each pin rises one cycle after its event and is held 4 cycles (32 ns); both leave through IOB flip-flops (`constr/arty_pmod.xdc`) |
| Host | WSL2, ASIX AX88179 via usbipd, `scripts/host_setup.sh`; load average 1.4–3.0 |
| Input | `data/runs/2019-10-18_AAPL.itch` (MANIFEST) → `data/runs/2019-10-18_AAPL_slice0.pkts` (SHA-256 prefix `20d845e5287fd5b3`), 35,313 packets per pass |
| Command | `ITCH=data/runs/2019-10-18_AAPL.itch scripts/board_run.sh e3_scope bbo 500 0`, repeated (`e3_scope_2..10`, `e3_scope_b1..b8`) while the scope was set up and read; `python -m analysis.latency --log data/runs/e3_scope.log --out … --hist …` for the histogram |
| Logs | `data/runs/e3_scope.log` SHA-256 prefix `9c87caaaaa11b109`; the other passes' logs and the raw scope photos (`data/runs/scope_2026-11-23/`) are local |

## Result

- **Scope:** on an isolated pair (one T1 pulse, then one T2 pulse, nothing between), the
  cursors on the two rising edges read A = −139.0 ns, B = −2.0 ns: **T2 − T1 = 137.0 ns**.
- **Board timestamps, same feed:** T2 − T1 is 17 cycles = **136 ns** for 717,549 of the
  831,842 BBOs (86.3 %, the p50); the rest are 14, 18, 23, 24 or 29 cycles (112–232 ns,
  the `_hist.csv`). The histogram is identical in passes `e3_scope`, `e3_scope_b2` and
  `e3_scope_b8`: T2 − T1 depends only on the message, not on the pass.
- **The board was right throughout:** 17 of the 18 completed passes matched the golden
  model exactly (831,842 / 831,842 BBOs, 0 lost datagrams). In `e3_scope_b1` the host lost
  229 output datagrams (`received_wrong=0`, `unflagged_wrong=0`: every record that arrived
  was right; the known host-side loss). `rx_fifo_overflow`, `mold_gaps` 0, never stale.
  The first loop's passes 11–30 failed: the USB adapter dropped out of WSL during pass 11.

## Interpretation

This is the external cross-check of buildplan §6.5: an instrument outside the FPGA agrees
with the pipeline-latency timestamps the FPGA writes into every BBO. 137.0 ns against
136 ns is 1 ns apart, inside this measurement's resolution: 2 ns per sample, the cursor
placement on each edge, and the pin-to-pin skew of the two outputs (IOB and board traces).
Since 136 ns is the board's value for 86 % of BBOs, the most likely pair to catch is a
17-cycle one; the reading is one pair, not a distribution.

A distribution on the scope did not work: each packet carries about 47 messages back to
back, so T1 pulses come tens of nanoseconds apart and, with infinite persistence, fill
every position before the T2 trigger (`data/runs/scope_2026-11-23/4.png`, local). Which
T1 caused a T2 can only be read on an isolated pair; the distribution comes from the
board's timestamps, which this reading confirms.
