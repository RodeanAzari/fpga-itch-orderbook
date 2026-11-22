# Board, 2026-11-21: one feed frame through MII → eth_rx FIFO → udp_rx → decoder (ILA)

Data: `2026-11-21_ila_frame_board_{mii,core}.csv` (`write_hw_ila_data -csv_file`, 4,096
samples each) and the same captures as `.ila` for the Vivado GUI
(`read_hw_ila_data` + `display_hw_ila_data`). Plot:
`docs/media/2026-11-21_phase-b_ila-frame-mii-to-decoder.png` (`make plots`).

| | |
|---|---|
| Commit | RTL at `921df33` (unchanged); ILA insertion `syn/ila_probes.tcl`, capture `syn/ila_capture.tcl`, check `analysis/ila_frame.py` committed with this file |
| Bitstream | `make impl ILA=1` → `build/arty_top_ila/arty_top.bit` (SHA-256 prefix `a5f5fdfc3edb6ff9`, `.ltx` `ca66f5811e2cef7f`): the release build (store-and-forward, DATA_W 8, 125 MHz) plus two ILA cores; WNS +0.032 ns, WHS +0.012 ns, 133 / 135 BRAM tiles. Vivado 2025.2 |
| Probes | `u_ila_mii` on rx_clk: mii_rx's IOB input register (`rxd_q`, `dv_q`, `er_q`; the pins one rx_clk later, as the pins themselves can't be probed). `u_ila_core` on the core clock: eth_rx output `f_*`, feed udp_rx payload `u_*`, decoder records `r_*`, `e_ok` (ob_core nets) |
| Host | WSL2, ASIX AX88179 via usbipd, `scripts/host_setup.sh`; load average about 14 (other projects' jobs running; irrelevant at 2 packets/s) |
| Input | `data/runs/2019-10-18_AAPL.itch` (AAPL extract of `S101819-v50.txt.gz`, MANIFEST) → `data/runs/2019-10-18_AAPL_slice2000.pkts` (SHA-256 prefix `71c4bfe95b47831551e9001a43f3517afa61b08bd4a679f6089dbe25b18c81e6`), 2,000 messages, session `20191018AA` |
| Commands | `ITCH=data/runs/2019-10-18_AAPL.itch scripts/ila_run.sh ila1 2 2000` (programs the ILA build, replays at 2 packets/s, arms both cores after the board's config ack); then `python -m analysis.ila_frame --mii data/results/2026-11-21_ila_frame_board_mii.csv --core data/results/2026-11-21_ila_frame_board_core.csv --packets data/runs/2019-10-18_AAPL_slice2000.pkts --table` |

## Result

`mii_fcs=1 fifo_eq_mii=1 payload_eq=1 sent=1 records=1`:

- The MII frame (low nibble first after preamble and SFD) is 1,512 bytes after the SFD
  with a good FCS.
- eth_rx's output is those 1,508 bytes without the FCS, byte for byte, one byte per
  core cycle (samples 213-1720), released after the frame's FCS was checked (store-and-forward).
- The feed udp_rx's payload (1,466 bytes, samples 256-1721) is exactly that frame's UDP
  payload, and equals packet 3 of the replayed slice: MoldUDP64 session `20191018AA`,
  sequence 143, 47 messages.
- The decoder produced 47 records (`AAAADADADADAADDAAADADADAADADADADADADAADDADAADAD`), the
  types the golden model's `FeedDecoder` gives for that packet after the three before it.
  The first record (a 36-byte Add Order) leaves the decoder 3 cycles after the message's
  last byte leaves udp_rx (samples 313 → 316); both streams move one byte every cycle.

## Interpretation

This is the Phase B evidence that a real frame from the host crosses the MII boundary,
the clock-domain crossing and the parsers on the board intact. Simulation has checked
the same path on every frame since Phase B; this capture shows it on silicon, and the
check is a script against the bytes the host sent, not a reading of a waveform. One frame
is one sample: the full-day and whole-file runs (`2026-11-09_full_day_board.md`,
`2026-11-19_whole_file_board.md`) are the volume evidence. The two ILAs have no common time
base (separate clocks, separately armed), so the capture says nothing about the MII-to-core
delay; T0 on hardware is in `2026-11-15_t0_wire_board.md`.

The replay was cut short by the run's 300 s timeout after the capture (Vivado's hardware
server took 4.5 minutes to start on the loaded host), so no `compare` result belongs to
this run.
