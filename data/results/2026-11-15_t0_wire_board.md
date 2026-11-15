# Board, 2026-11-15 (analysis of 2026-11-13/14 runs): T0 on hardware vs simulation

CSVs: `2026-11-15_t0_wire_board_ct.csv` (run `e3_ct`), `2026-11-15_t0_wire_board_ct_v04.csv`
(run `e5_ct`). Rows: `last_in_frame, cycles, count`, where cycles = T1 − T0 − 10 × (bytes
after the SFD up to the cause message's last byte), per BBO.

| | |
|---|---|
| Commit | analysis `analysis/t0_wire.py` and the `test_core` log line (this commit). Runs: `e3_ct` RTL `2325efd` (eth-core-v0.3), `e5_ct` RTL `d920ac0` (eth-core-v0.4), both cut-through, DATA_W 8, 125 MHz |
| Runs | Full AAPL day 2019-10-18 at 500 pps, as in `2026-11-13_ct_vs_sf_board.md` and the 2026-11-14 `docs/log.md` entry; logs `data/runs/e3_ct.log` SHA-256 prefix `75d8206903cc80e0`, `data/runs/e5_ct.log` `c6a93bd9efb939ea` (local) |
| Input | `data/runs/2019-10-18_AAPL_slice0.pkts` (`20d845e5287fd5b3`; `host/replay --out` of `~/itch-cache/2019-10-18_AAPL.itch`, MANIFEST) |
| Command | `python -m analysis.t0_wire --log data/runs/e3_ct.log --packets data/runs/2019-10-18_AAPL_slice0.pkts --out data/results/2026-11-15_t0_wire_board_ct.csv` (and `e5_ct` → `_ct_v04.csv`) |
| Simulation | `python tb/run.py test_core`, cut-through DATA_W 8 set, seeded per run; the line `T1 - T0 - serialization` |
| Tools | Python 3.14.4 |

## Result

| Source | Other messages (cycles: count) | Frame's last message | Host clock |
|---|---|---|---|
| Simulation (`test_core`, 89 BBOs) | 53: 72 | 48: 17 | n/a (exact clocks) |
| Board `e3_ct` (831,842 BBOs, 0 without T0) | 51: 146, 52: 431,411, 53: 382,593 | 46: 328, 47: 17,352, 48: 12 | +68.2 ppm |
| Board `e5_ct` (831,842, 0 without T0) | 51: 1,907, 52: 476,771, 53: 335,472 | 46: 1,350, 47: 16,342 | +67.1 ppm |

Host clock: least-squares slope of the other messages' value against byte position,
read as the host's TX clock offset from the board's core clock (positive = host fast).

## Interpretation

- **On the board, T0 matches the simulated constant within one cycle of phase.** With the wire
  time removed, T1 − T0 in simulation is 53 cycles for every BBO. On the board it is 53 or
  52, and 51 for a few messages at the end of long frames. Two effects explain the spread:
  each frame's rx_clk phase against the core clock (0 or 1 cycle, shared by the T0
  synchronizer and the data FIFO), and a host clock about 68 ppm fast. At 68 ppm, the 80 ns
  per byte comes about 0.95 cycles short over a 1,400-byte frame. A T0 synchronizer that
  lost or added an rx_clk on hardware would move the value by 5 cycles. None did, in
  1.66 M BBOs over two runs on two eth-core tags.
- **A frame's last message is 5 cycles (one rx_clk) earlier, in simulation and on the board
  alike.** It is released by the frame's last FCS byte, and `mii_rx` emits a frame's last
  byte 2 rx_clk after `rx_dv` falls instead of waiting for a next byte (eth-core
  `rtl/mii_rx.sv` header). This is by design and not a T0 effect: the same frames carry
  both values with one T0.
- **What this does not show:** how the one cycle of phase splits between the T0
  synchronizer and the data FIFO. An ILA capture of `sfd_o` and the T0 capture would
  separate them. It would not change T2 − T0 by more than that one cycle. The constant
  18 cycles (SFD at the pins → T0) stays a simulation figure (`docs/latency.md`).
- The +68 ppm is the gap between two oscillators, the host NIC's and the Arty's 100 MHz.
  Both are within the ±100 ppm that 802.3 allows. It is not a board error.
