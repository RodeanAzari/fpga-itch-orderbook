# Board, 2026-11-19: the whole 2019-10-18 file, every symbol, zero mismatches (buildplan §8.4)

CSV: `2026-11-19_whole_file_board.csv`, one row (run `f1_whole`).

| | |
|---|---|
| Commit | Bitstream `f81a9f6`: `build/arty_top/arty_top.bit` (DATA_W 8, store-and-forward), SHA-256 prefix `000189e08975e722`; Vivado 2025.2, 125 MHz; programmed with openFPGALoader. Analysis at `80d5a53` (compare checks the feed counters) |
| Host | WSL2, ASIX AX88179 via usbipd, `pfifo` 10,000, NCM TX timer 0; load average 1.0 at start |
| Input | `~/itch-cache/S101819-v50.txt.gz` (MANIFEST, SHA-256 `390acb4d…`, checked this session), the whole file, all symbols → `data/runs/S101819-v50.txt.gz_slice0.pkts` (SHA-256 prefix `9166e8c7d11eaf48`), 302,347,067 messages in 6,456,827 packets + end of session |
| Commands | `ITCH=~/itch-cache/S101819-v50.txt.gz LO_PRICE=2140700 SESSION=20191018AA scripts/board_run.sh f1_whole bbo 0 0 --idle 120 --duration 0` (pps 0 = unpaced; it runs `python -m analysis.compare --log data/runs/f1_whole.log --packets data/runs/S101819-v50.txt.gz_slice0.pkts`) |
| Logs | `data/runs/f1_whole.log` SHA-256 prefix `43cd4a50cc904506`; `f1_whole.{replay,capture,compare}.txt` (local) |

## Result

- **831,842 / 831,842 BBOs equal the golden model's, byte for byte** (times excepted, ADR
  0011): `match=1`, `mismatched_positions=0`, `lost_datagrams=0`, `unflagged_wrong=0`.
- **The locate filter and R learning, counted on the board, equal the model:**
  `feed_counters_match=1`. The board skipped 295,290,395 book messages for other symbols
  and 5,388,821 messages outside the ITCH subset, learned AAPL's locate (14) from the
  one matching `R` message, and saw no locate change. Every one of the 6,456,828 frames
  arrived (rx_frames_ok, no CRC error, no MII FIFO overflow, no MoldUDP64 gap).
- The feed went out unpaced in 835.5 s: 7,728 packets/s, 361,876 messages/s, 94.1 Mb/s on
  the wire (line rate for this framing). Nothing stalled in the FPGA (rx/out stall cycles 0)
  and nothing was marked stale.

## Interpretation

This is the hardware scoreboard of buildplan §8.4: the whole trading day, not the AAPL
extract the Phase D/E runs used. 98 % of the messages are for other symbols and make no
output, so the BBO comparison alone could not show they were filtered right; the feed
counters close that gap, and both agree with the model exactly. The BBO stream equals
the AAPL-extract runs' count (831,842), as it must if filtering is correct.

The host reordered 3 output datagrams (capture's `seq_backwards=3`; `seq_gaps=1`,
`seq_lost=3` are the same events seen before the late datagrams arrived). compare puts
them back by pkt_seq; none was lost. Earlier line-rate runs on the AAPL extract lost output
datagrams (`2026-11-16_stall_counters_board.md`); here the output rate is ~1,000
BBOs/s instead of ~20,000, and the host was quiet. That loss remains unexplained, and this
run says nothing new about it.

The book counters equal the AAPL-extract run `m2_p4000` (2026-11-16) of the same day:
book_records 1,658,958, `price_oow` 16,099, `bbo_oow` 4 (the window limits of ADR 0002;
the out-of-window BBOs carry their flag, which compare checks). compare does not check
book counters directly.

Limits: one run, one day, store-and-forward DATA_W 8 only.
