# 2026-11-07: host datagram reordering vs. send rate and I225-LM driver settings (`_host_reorder_sweep.csv`)

| | |
|---|---|
| Commit | OrderBook `6ea3838` + this commit's `scripts/reorder_sweep.sh`, `scripts/reorder_table.py` |
| Bitstream | fpga-eth-core `build/rx_stats/rx_stats_top.bit`, SHA-256 prefix `2aba4741cc5ac665` (the eth-core-v0.1 gate bitstream) |
| Sender | fpga-eth-core `3909b55` (`eth-core-v0.2`) `build/host/udp_seq_send`, g++ 15.2.0, pinned with `taskset -c 2` |
| Host | WSL2 6.18.33.2 mirrored networking → Intel I225-LM, Windows driver `e2fn.sys` 2.1.4.3; direct cable, 100 Mb/s full duplex |
| Board | Arty A7-100T programmed over JTAG (usbipd), Vivado 2025.2; counters read over UART, one reader for the whole session (`-hupcl`) |
| Input | `udp_seq_send` sequence numbers, continued across runs (`--start-seq`), 1,472-byte payloads, 60 s per run; no file input |
| Concurrent load | A cocotb simulation (one core) ran during the baseline runs; the sender was on its own CPU |

## Command

```
OUT=build/sweep/run1 RATES="8000 6800 5600 4000" scripts/reorder_sweep.sh   # baseline
OUT=build/sweep/run1 RATES="2000" START_SEQ=1464000 scripts/reorder_sweep.sh
OUT=build/sweep/run1 RATES="4000" START_SEQ=1584000 scripts/reorder_sweep.sh
# user: Packet Priority & VLAN -> Disabled
OUT=build/sweep/run2 RATES="4000 4000 8000" START_SEQ=1824000 scripts/reorder_sweep.sh
# user: Interrupt Moderation -> Disabled
OUT=build/sweep/run3 RATES="4000 4000 8000" START_SEQ=2784000 scripts/reorder_sweep.sh
# board reprogrammed (same bitstream); other sessions loading 2-3 cores
OUT=build/sweep/run4 RATES="4000 4000" scripts/reorder_sweep.sh
python3 scripts/reorder_table.py build/sweep/runN/runs.txt <label>   # one block per run dir
```

`_uart.log` holds every board counter line of the session (cumulative; `rx_stats` README
for the field list). Each CSV row is the difference of the lines before and after a run.

## Interpretation

- **No loss anywhere:** in all 12 runs `udp_ok` equals the datagrams sent, and
  `ovf_lost` and `crc_err` are 0. Every `seq_err` is a reordering, not a drop.
- **Reordering does not grow with load.** Baseline: 0.024 % at 98 % of line rate, 0.065–
  0.078 % at 69–84 %, 0.115–0.145 % at 49 %, 0.068 % at 25 %. Two runs at the same rate
  differ by about ±25 %, so single-run differences smaller than that mean nothing.
- **Packet Priority & VLAN disabled:** 4,000 pps falls to 0.073–0.075 % (about half,
  outside the run-to-run spread); 8,000 pps is unchanged (0.021 %). It helps, but it is
  not the cause.
- **Interrupt Moderation also disabled:** 8,000 pps improves to 0.0044 %, but 4,000 pps
  gets worse (0.156 %, and 0.384 % in a run that started while the link was still
  re-acquiring its address).
- **Conclusion:** the reordering tracks the Windows driver's interrupt and completion
  timing, and no adapter setting brings it to zero. That fits the explanation "sends
  reach the I225-LM miniport from different CPUs and are spread across its TX queues"
  (still not proven directly). The Phase D hardware full day needs a host path without
  this driver: next is a USB 3 Ethernet adapter attached to WSL with usbipd.
- **CPU load from other sessions (rows `heavy_cpu_load`, later the same day):** with other
  sessions' jobs using 2–3 cores (`_load_run4.txt`, 10 s samples of the top processes),
  4,000 pps reordered 1.20 % and 1.40 %, about 10× the lightly loaded runs. Still 0 loss.
  The board was reprogrammed with the same bitstream first (another session had used the
  JTAG). Load is a strong driver of the rate, but no run so far has reached zero, and a
  fully idle machine was not tested. Whether Interrupt Moderation was back to Enabled for
  these runs is unknown.
- **Host state left behind:** Packet Priority & VLAN stays Disabled (it helps a little);
  the user was asked to restore Interrupt Moderation to Enabled.
