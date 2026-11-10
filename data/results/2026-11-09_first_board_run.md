# First full-pipeline runs on the board (2026-11-09)

CSV: `2026-11-09_first_board_run.csv` (one row per run).

| | |
|---|---|
| Commit | RTL/bitstream `66abceb` (`build/arty_top/arty_top.bit`, ADR 0009, 125 MHz core); host + analysis `9938fca` |
| Board | Arty A7-100T, programmed over JTAG (`make prog`), DONE=1 |
| Host | WSL2 kernel 6.18.33.2; ASIX AX88179 USB 3 adapter (0b95:1790) attached with usbipd-win, Linux `cdc_ncm` driver, `enx8cae4caa0ec7`, 192.168.10.1/24, 100 Mb/s full duplex; static neighbour 192.168.10.2 → 02:00:0a:0b:0c:0d. The Windows I225-LM path is not involved. |
| Input | `2019-10-18_AAPL.itch` (MANIFEST), first 200,000 messages → 4,391 MoldUDP64 packets (`replay --count 200000 --session 20191018AA`, max payload 1,472) |
| Config | `lo_price` 2,140,700 (base 23,455 ticks, ADR 0002); locate learned from `R` |

## Commands

The four CSV rows were run by hand with the same commands that `scripts/board_run.sh`
now wraps (it reproduced `bbo500` as `check500`: 91,021/91,021, 0 mismatches):

```
build/host-Release/capture --log data/runs/<run>.log --lo-price 2140700 --mode <trace|bbo> \
  --bind-ip 192.168.10.1 --ready-file data/runs/ready --idle 3 --duration <120|300>
build/host-Release/replay --in /mnt/d/itch/derived/2019-10-18_AAPL.itch --dst 239.1.1.1 \
  --port 26400 --if-ip 192.168.10.1 --count 200000 --session 20191018AA --pps <500|2000>
python -m analysis.compare --log data/runs/<run>.log --packets data/runs/slice200k.pkts
```

## Interpretation

- **At 500 packets/s both modes match the golden model exactly**: 198,084 trace records
  (every decoded message) and 91,021 BBO updates, byte for byte, with no input loss and the
  book never stale. This is the first end-to-end hardware result: MII → MAC → IPv4/UDP →
  MoldUDP64 → ITCH → order table → price levels → BBO → UDP out.
- **At 2,000 packets/s the board lost input frames at the MII FIFO** (54 in trace mode,
  2 in BBO mode). Every one was counted: `rx_frames_ok + rx_fifo_overflow` = 4,391 =
  packets sent, in every run. The cause is not established. BBO output is small, so
  "trace output exceeds the TX link" does not explain the BBO-mode loss; host-side bursts
  (the USB NCM path batching frames back to back) are a candidate, not a finding.
- **No silently wrong book.** In `bbo2000` the first BBO that differs from the model
  (record 33,380, MoldUDP64 seq 97,815) is exactly the first one with the `stale` flag;
  all 29,188 later records carry it and none before it does.
- The capture side saw 3 FPGA → host datagrams out of order in each trace run (none in BBO
  runs); none lost. `compare` restores order by `pkt_seq`.
- These are 200,000-message slices at fixed rates, not a full day and not real-time pacing.
