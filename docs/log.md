# Lab notebook

## 2026-11-02: Phase A, golden model and first real-data numbers

**Done**
- Repo, Makefile (WSL), ITCH 5.0 parser, unbounded book, capacity-limited book, seeded
  adversarial generator. 19 tests, ruff and `mypy --strict` clean.
- Downloaded `S101819-v50.txt.gz` (2019-10-18, 3.95 GB). The first choice (`12302019`) was
  unreachable and its listed `.md5sum` returns 404.
- Full-day replay: 302,347,067 messages in 4 min 08 s. AAPL (locate 14): 1,669,591
  messages, peak 27,814 live orders, zero self-consistency errors.

**Finding: the specified order table cannot reach zero overflow**
- Symptom: replaying real refs against a 4-way table, the worst bucket held 18 live orders
  at 16,384 sets.
- Root cause:
  - Low ref bits are not evenly distributed for one symbol.
  - Even with a good multiplicative hash, the worst bucket reaches 8. At the peak, load is
    Poisson with λ = 1.7, and that is simply the maximum over 16,384 buckets.
- Fix (proposed, ADR 0001): 2-bank d-left hashing, 65,536 entries. Zero overflow all day,
  versus 36,545 overflow inserts for a single table of the same size.

**Finding: `bbo_oow` is 2, not 0**
- The ±2,048-tick window around the $234.55 opening mid missed two BBOs. Both were in an
  almost empty book: 04:00:11 (bid $175.12) and 20:00:00 (ask $260.00).
- Proposed treatment in ADR 0002.

**Decisions accepted (ADR 0001, 0002) and checked on the real day**
- `BoundedBook` now uses d-left placement, counts `ref_overflow` / `shares_overflow`, and
  flags out-of-window BBOs.
- Full-day result: zero table, ref, shares and unknown-ref errors; `bbo_oow` = 4 flagged
  updates; 0 flag-contract violations over 1,669,591 messages.
- CLAUDE.md §6.3, §6.4 and Phase D updated to match.

**Bug: generator's far prices never triggered the out-of-window flag**
- Symptom: `bbo_oow > 0` failed in the generator coverage test.
- Root cause: far prices were always placed past the side's worse edge (bids far below,
  asks far above), and both sides always had in-window levels.
- Fix: far prices fall past either edge, so a hidden better price occurs.

**Bug: generator never produced a locked book**
- Symptom: the coverage test `test_generator_actually_reaches_the_adversarial_cases` failed
  on `locked`.
- Root cause:
  - Crossing orders were never removed, so the book stayed permanently crossed.
  - `U` replacements also ignored side, priced bids above the base, and deepened the cross.
- Fix: crossed and locked states became brief explicit episodes (add both sides, then delete
  both), and replacements keep their side.

**Numbers:** `data/results/2019-10-18_AAPL.md`.

## 2026-11-03: Phase B start, toolchain pinned, async FIFO proven

**Toolchain**
- Versions pinned in `docs/toolchain.md`. cocotb 2.1.0 and cocotb-coverage 2.0 were added to
  `requirements.txt`.
- The local Verilator build reports two versions (5.053 from the wrapper, 5.050 in the run
  banner). Both are recorded.

**`fpga-eth-core` repo created locally (`~/fpga-eth-core`), first block `async_fifo`**
- Cummings-style Gray-pointer FIFO. AXI-Stream on both sides, FWFT read through a
  BRAM-friendly synchronous read, write-side and read-side occupancy outputs (for the
  high-water mark the `eth-core-v0.1` gate requires).
- Formal: k-induction PASS (depth 24, depth-4 instance, two free-running clocks). Proven:
  - no overflow or underflow
  - Gray encoding
  - synchronizer-chain monotonicity (needed as induction invariants)
  - data integrity of an arbitrary tagged word
- Three planted bugs each fail the proof: write allowed when full, binary read pointer,
  read from the next address.
- cocotb + Verilator: 25→125 MHz bursty, fast writer that fills it, and slow writer that
  drains it, at depth 16 and 2048, 3,000 words each. All pass; full is reached at exactly
  DEPTH.

**Bug (testbench): random clock phase rejected by cocotb**
- Symptom: every test died at t=0 with `Unable to accurately represent 4.84…ns`.
- Root cause: a float phase offset finer than the 1 ps simulator precision.
- Fix: draw the phase in whole picoseconds.

## 2026-11-03: Phase A signed off; repos; tb-common-v0.1 released

- Phase A signed off by the user and tagged `v0.1-model`.
- GitHub repo `OrderBook` renamed to `fpga-itch-orderbook` (private). The local folder
  stays `~/OrderBook`, because fpga-coord references it.
- `fpga-eth-core` and `tb-common` created as private GitHub repos.
- clang-tidy and clang-format 21.1.6 installed as pip wheels in `.venv` (no sudo).
  tcpreplay still needs `sudo apt install`.

**tb-common-v0.1** (the gate for P3, P4 and P5 testbenches; all soft gates)
- Contents:
  - `AxisSource` / `AxisSink` / `AxisMonitor` with random tvalid gaps and tready stalls
  - tkeep-aware byte packing
  - `InOrderScoreboard`, which dumps the last N matched items, expected vs. observed and
    the upcoming expectations on the first mismatch
  - `get_rng`, which logs its seed
- Self-tests:
  - 25 pytest cases covering every scoreboard failure path
  - cocotb against a skid buffer at DATA_W 8 and 64
  - a build with one bit flipped at beat 37, which the scoreboard catches ("mismatch
    after 13 matched item(s)")
- The `fpga-eth-core` async FIFO tests now use it and still pass at depths 16 and 2048,
  installed from the tag rather than the working tree.

**Bug (tb-common): seeds were invisible under cocotb**
- Symptom: no `SEED=` lines in cocotb output, although pytest's caplog saw them.
- Root cause: cocotb only shows INFO from loggers under `cocotb.*`. The logger was named
  `tb_common.rng`.
- Fix: logger names `cocotb.tb_common.*`. This is a name only; the module still doesn't
  import cocotb.

## 2026-11-04: fpga-eth-core RX path, simulation to bitstream

All work in `fpga-eth-core` (commits `0afa76b` → `f51a3bd`, pushed).

**RTL**
- `mii_rx` (nibble→byte, low nibble first, SFD hunt, `sfd_o` T0 pulse), `crc32_d8`,
  `eth_fcs_check` (residue 0xDEBB20E3, FCS stripped).
- `rx_ovf_guard`: when the async FIFO is full, the damaged frame is dropped and a 1-byte
  abort beat carries the count of lost frames into the core domain, so overflow is
  counted per frame, never silent.
- `frame_buffer`: store-and-forward; bad frames rewound, oversize frames dropped without
  deadlock.
- `eth_rx` top: exactly one status per frame with an SFD (ok / crc / rx_err / runt /
  oversize / ovf).
- `udp_rx`: Eth/IPv4/UDP filter with runtime `cfg_mac`/`cfg_ip`/`cfg_port`, IHL options,
  header checksum, fragments rejected, padding stripped by UDP length, sender
  MAC/IP/port latched for TX learning.
- `examples/rx_stats`: Arty top (MMCM 125 MHz + ODDR 25 MHz PHY ref, reset sequencing,
  payload sequence check, 15 counters over UART at 1 Hz).

**Verification**
- 20 cocotb tests, `make ci` green (lint, formal, sim).
- Every new test was shown to fail on a planted bug: reversed nibble order, wrong CRC
  residue, buffer releasing bad frames, guard undercounting lost frames (60 sent,
  accounting summed to 33), checksum ignored, padding leak, 24-bit multicast mapping,
  swapped counter wiring.
- The multicast-mapping bug is only visible with a group whose address bit 23 is set
  (224.255.255.255); 239.1.1.1 would never expose it.

**Vivado 2025.2** (`make example`, reports in `fpga-eth-core/docs/reports/rx_stats/`)
- Timing met at 125 MHz: WNS +0.663 ns, WHS +0.035 ns. Worst path is `udp_rx`'s verdict
  logic.
- MII input setup +6.3 ns against DP83848 T2.5.2 (RX_CLK→data 10–30 ns, read from TI
  SNLS250E, not from memory).
- 892 LUT, 1,710 FF, 2 BRAM36 for the whole example.

**Bug (async_fifo): Vivado-only warnings in a formally proven block**
- Symptom: Synth 8-6901 "used before declaration" and Constraints 18-402 "not a valid
  startpoint" on the FIFO pointer max-delay.
- Root cause: `rd_gray_q` declared in the read-domain section after the write domain
  used it (Verilator accepts this); the XDC name filter also matched LUT cells.
- Fix: declaration moved up; filter limited to `IS_SEQUENTIAL`. Formal re-run: PASS.

Not yet done: hardware link-up and the eth-core-v0.1 sustained-stream test.

## 2026-11-04 (later): first light on the board; host sender

**Board bring-up** (board claimed via fpga-coord, released afterwards)
- `rx_stats_top.bit` programmed over JTAG (usbipd-win → WSL, Vivado hw_server), DONE=1.
- UART (`/dev/ttyUSB1`, 115200) prints one `RX ...` line per second, uptime incrementing,
  all Ethernet counters 0. The MMCM, resets, stats formatter and UART work on silicon.
- No Ethernet link: Windows shows the I225-LM as Disconnected. Root cause: no cable plugged
  in yet. The user is ordering a Cat5e/Cat6 patch cable (both PHYs do Auto-MDIX, so no
  crossover needed).

**Host sender** (fpga-eth-core `23e8d21`)
- `host/udp_seq_send`, C++20: big-endian 64-bit sequence per datagram, absolute-schedule
  busy-wait pacing, queue-full retried with the same sequence, key=value summary.
- `make host-test`: 20,000 datagrams over loopback, each checked (size, filler, contiguous
  sequence across the 2^64 wrap). Planted little-endian encoding and a 32-bit counter
  each fail it. `make ci` green with host-test included.

Open: WSL2 NAT can't reach the wired NIC. Mirrored networking vs. a Windows-side sender
is the user's call (mirrored needs `wsl --shutdown`).

## 2026-11-05: fpga-eth-core TX path (toward eth-core-v0.2); cable still pending

**Host:** WSL2 mirrored networking works. WSL sees the I225-LM as `eth0`, the Arty
reprograms over usbipd (DONE=1), and the UART ticks. There is no link, because the patch
cable hasn't arrived.

**RTL** (fpga-eth-core)
- `mii_tx`: preamble/SFD, low nibble first, zero padding to 60 bytes, FCS, and exactly 24
  idle nibbles between frames. The MII cannot stall, so an underrun ends the frame with a
  deliberately wrong FCS and is counted.
- `eth_tx`: core → async FIFO → `mii_tx`. The underrun pulse returns through a toggle
  synchronizer.
- `udp_tx`: store-and-forward payload buffer, length counted from tlast (user decision:
  no length input), destination sampled on the first beat, RFC 791 checksum.
- `examples/udp_echo`: echoes every datagram to the sender MAC/IP/port that `udp_rx`
  learned. It's the second non-ITCH example and the v0.2 hardware harness.
- `host/udp_echo_check`: paced random-length (1–1,472 B) sender plus byte-exact echo
  checker.

**Verification**
- cocotb, all with `make ci` green:
  - `mii_tx`: 300 back-to-back frames byte-exact with IFG exactly 24; starved frames all
    spoiled and counted.
  - `udp_tx`: 539 frames checked by an independent RFC parser.
  - `udp_echo` end to end, MII pins to MII pins: 120 echoes of 1–1,472 B to 4 rotating
    senders, counters exact.
- 8 planted bugs each fail a test: high nibble first, IFG short, FCS not spoiled,
  padding to 59, destination sampled late, single-fold checksum, UDP length off by one,
  oversize not counted.
- A single-fold checksum bug is invisible to random headers (about 1 in 10⁴ need the
  second carry), so the test forces that case directly.

**Vivado 2025.2** (`make example-echo`, `docs/reports/udp_echo/`)
- Timing met at 125 MHz: WNS +0.312 ns, WHS +0.060 ns.
- MII TX output setup +20.645 ns against DP83848 T2.4.2 (10 ns setup; read from
  SNLS250E, which the subagent fetched and pypdf extracted).
- 1,119 LUT, 2,367 FF, 3 BRAM36; 0 critical warnings.

**Bug (async_fifo): "unsafe" clock crossings in a formally proven FIFO**
- Symptom: the first `udp_echo` build met timing, but `report_clock_interaction` marked
  core ↔ tx_clk as "Timed (unsafe)" / "Partial False Path (unsafe)".
- Root cause, two parts:
  1. The 64-deep TX FIFO mapped to distributed RAM, whose asynchronous read makes the
     memory itself a crossing (write clock → `m_tdata_o`). Only the Gray pointers were
     constrained.
  2. Synthesis merged `wr_gray_q[MSB]` into the identical `wr_bin_q[MSB]`, so the
     pointer max-delay filter missed that bit. The RX FIFO escaped only because the
     merge kept the Gray name.
- A first fix (a `-quiet` LUTRAM max-delay) still raised a critical warning on BRAM
  instances and didn't clear the flag, so I reverted it.
- Fix: `async_fifo` forces block RAM and marks the Gray registers `DONT_TOUCH`. The
  `eth_tx` FIFO went from 64 to 2,048 deep (one BRAM18). All crossings now report "Max
  Delay Datapath Only". Formal re-run: PASS.

**Bug (host test): loopback reorders**
- Symptom: `test_udp_seq_send` failed 4 of 5 runs.
- Root cause: WSL loopback reorders a few datagrams per 20,000 (per-CPU backlog when
  the thread migrates). Every datagram was present and contiguous; the sender was fine.
- Fix: the test checks each sequence number arrives once, not arrival order. The planted
  bugs still fail it.
- Hardware consequence: the gate senders are pinned with `taskset`, since a multi-queue
  NIC could reorder in the same way and show up as false `seq_err`.

Not yet done: the eth-core-v0.1 and v0.2 hardware gates (cable), and the consumer guide.
