# Board, 2026-11-22: the full AAPL day decoded on the board, record for record (Phase C)

CSV: `2026-11-22_trace_full_day_board.csv`, one row per run (`c1_trace_day`, `c2_trace_day`,
`c3_trace_day`). The result is `c3`; `c1` and `c2` are the two runs before it, kept because
they failed for reasons the board counted.

| | |
|---|---|
| Commit | Bitstream: release build of the RTL at `8455bcf` (unchanged since), `build/arty_top/arty_top.bit` (DATA_W 8, store-and-forward, 125 MHz), SHA-256 prefix `dc1168385748c72f`; Vivado 2025.2; programmed with openFPGALoader. Analysis at `fb4e7a9` (compare's `received_wrong` / `missing_records`) |
| Host | WSL2, ASIX AX88179 via usbipd, `scripts/host_setup.sh` (`pfifo` 10,000, NCM TX timer 0); load average 17-22 (other projects' jobs) |
| Input | `data/runs/2019-10-18_AAPL.itch` (AAPL extract of `S101819-v50.txt.gz`, MANIFEST) → `data/runs/2019-10-18_AAPL_slice0.pkts` (SHA-256 prefix `20d845e5287fd5b3`): 1,669,592 messages in 35,312 packets + end of session, session `20191018AA` |
| Commands | `ITCH=data/runs/2019-10-18_AAPL.itch scripts/board_run.sh <run> trace <pps> 0 --idle 30` with pps 500 (`c1`, `c2`) and 200 (`c3`); it runs `python -m analysis.compare --log data/runs/<run>.log --packets data/runs/2019-10-18_AAPL_slice0.pkts`. CSV columns from the runs' `replay.txt`, `capture.txt` (last stats snapshot) and compare |
| Logs | `data/runs/c{1,2,3}_trace_day.log`, SHA-256 prefixes `2744a4afd0397b48`, `1aa65c70cddcd87d`, `48cc5a5b8faddab0` (local) |

## Result

`c3_trace_day` (200 packets/s): `match=1`, `expected_records=fpga_records=1658958`,
`lost_datagrams=0`, `feed_counters_match=1`.

- In trace mode the board sends one record per decoded ITCH message of the §5.3 subset
  (type, flags, ITCH timestamp, MoldUDP64 sequence, refs, shares, price; `docs/protocol.md`).
  All 1,658,958 equal the golden parser's (`model.protocol.expected`) in order, byte for
  byte. The other 10,633 messages are types outside the subset, skipped by length and
  counted (`itch_unknown_types`), as in the model.
- Every frame arrived: 35,313 `rx_frames_ok`, no MII FIFO overflow, no MoldUDP64 gap; the
  board's feed counters equal the model's.

The two runs at 500 packets/s, same bitstream and input:

- `c1`: the replayer fell 607 ms behind at load 17 and caught up with a back-to-back burst;
  the MII FIFO overflowed on 24 frames (`rx_fifo_overflow=24`), 11 MoldUDP64 gaps of 1,656
  messages, all counted. The host also lost 1,658 output datagrams. compare:
  `received_wrong=11`, the 11 input gaps (skips with no output loss to explain them).
- `c2`: no input loss (`feed_counters_match=1`), the board produced every record
  (`out_records=1658958`), but the host lost 1,086 of its 221,448 output datagrams
  (no Linux UDP or interface counter shows them). compare: `received_wrong=0`,
  `missing_records=22550`: every one of the 1,636,408 records received equals the model's,
  and every missing one lies in a lost datagram.

## Interpretation

This is the Phase C hardware item of buildplan §9: the decoded message stream matches the
golden parser on the full day, on the board. Simulation and a 200,000-message slice on the
board (`2026-11-09_first_board_run.md`) showed it before; this is the whole day, with every
message the RTL decodes checked, not only those that move the BBO.

Trace mode is the hardest mode for the link: each record is 40 bytes, more than the
30.8 feed payload bytes per message of this day (51,427,539 / 1,669,592), so during a line-rate
burst the output cannot drain faster than the input fills, and a burst longer than the
2 KB MII FIFO overflows it. That is what `c1` shows, and the board counted it. At 200 packets/s the replayer's
worst lag was 22 ms, so no such burst occurred. The output loss in `c1`/`c2` is the open
host-side loss (README limitations): it is outside the board (its own datagram counter
is complete) and not in the Linux counters. It went away at the lower rate in this one run;
one run does not show that it depends on the rate.
