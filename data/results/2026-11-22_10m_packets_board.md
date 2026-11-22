# Board, 2026-11-22: 11,250,632 packets from `replay`, every one counted by the board (Phase B)

CSV: `2026-11-22_10m_packets_board.csv`, one row (run `b1_10m`).

| | |
|---|---|
| Commit | Bitstream: release build of the RTL at `8455bcf`, `build/arty_top/arty_top.bit` (DATA_W 8, store-and-forward, 125 MHz), SHA-256 prefix `dc1168385748c72f`; Vivado 2025.2. `scripts/board_run.sh` `MAX_PAYLOAD` committed with this file |
| Host | WSL2, ASIX AX88179 via usbipd, `scripts/host_setup.sh`; load average 15-19 at start (other projects' jobs) |
| Input | `~/itch-cache/S101819-v50.txt.gz` (MANIFEST), the first 40,000,000 messages, all symbols, in packets of at most 150 payload bytes → `data/runs/S101819-v50.txt.gz_slice40000000_mp150.pkts` (SHA-256 prefix `7dd949239b90b157`): 11,250,631 packets + end of session |
| Command | `ITCH=~/itch-cache/S101819-v50.txt.gz MAX_PAYLOAD=150 scripts/board_run.sh b1_10m bbo 0 40000000 --idle 30 --duration 0` (pps 0 = unpaced; then `python -m analysis.compare --log data/runs/b1_10m.log --packets <that pkts file>`) |
| Logs | `data/runs/b1_10m.log` SHA-256 prefix `150f4751791e769d`; `b1_10m.{replay,capture,compare}.txt` (local) |

## Result

- `replay` sent 11,250,632 packets (`packets_sent`, `dropped_packets=0`).
- The board's last stats snapshot: `rx_frames_ok=11250632`, `feed_datagrams=11250632`,
  `mold_packets=11250632`; `rx_crc_err`, `rx_err`, `rx_runt`, `rx_oversize`,
  `rx_fifo_overflow`, `ip_csum_err`, `udp_malformed`, `mold_gaps` all 0; never stale.
- compare: `match=1`, the 99,018 AAPL BBO updates of these 40 M messages = model,
  `feed_counters_match=1` (38,294,904 other-symbol messages filtered, as the model).

## Interpretation

This is the Phase B hardware item of buildplan §9: 10 million packets from `replay` with
the frame counters matching the host. Every packet the host sent was received with a good
FCS, passed the IPv4/UDP filter and reached the MoldUDP64 deframer in sequence.

The host sent unpaced but reached only 13,431 packets/s (21.2 Mb/s on the wire): with
small packets the limit is the host's per-packet cost (WSL2, usbip, USB adapter), not the
100 Mb/s link. This run is therefore evidence of frame-count integrity over 11 M packets,
not of line rate; line rate with full-size packets is in `2026-11-15_line_rate_board.md`
and `2026-11-19_whole_file_board.md` (6,456,828 frames at 94 Mb/s, all received).
