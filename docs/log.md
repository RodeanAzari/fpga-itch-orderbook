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

## 2026-11-05 (later): READMEs; Phase C model and RTL in simulation

**Repos.** The fpga-eth-core README is rewritten as a full write-up, including the
consumer guide v0.2 needs. tb-common gets a full README. This README now links both.

**Phase C golden model** (`model/moldudp64.py`)
- `SeqTracker` implements the MoldUDP64 sequence rules (ADR 0003): stale is sticky
  until reset, a partial overlap is dropped whole and resurfaces as a gap, a malformed
  packet delivers what it carries.
- `FeedDecoder` checks type, then length, then locate, then fields (ADR 0004). That is the
  order the RTL uses, which also made the full-day run fast enough.
- 14 tests; 4 planted model bugs fail them.
- Full day: 302,347,067 messages in 6,456,827 packets → 1,658,958 AAPL records. That is
  exactly Phase A's independently counted A+F+E+C+X+D+U. 5,388,821 unknown types equal
  the day's non-subset messages. 0 gaps, never stale (14 min 25 s).

**Phase C RTL** (`rtl/feed/`, `DATA_W` = 8 per ADR 0005)
- `mold_deframer` → `itch_decoder`. The decoder reads fields at fixed distances from the
  message's last byte, held in a right-aligned 40-byte window.
- cocotb feeds every packet to the model as well, and requires records field for field,
  every counter and the stale flag to match. 9/9 pass. Six planted bugs are each caught.

**Bug (deframer): `m_tuser` on every byte.** Symptom: an assertion fired at cycle 208.
Root cause: tuser was set on every mid-message beat, not only on a truncated message's
last beat. Fix: `s_tlast_i && remain != 1`.

**Bug (test): planted bugs that seemed caught were not.** Symptom: P2 (gap not stale) and
P4 (locate never relearned) passed once the seed was fixed. Root causes:
- The test sometimes chose a 60-byte packet size that cannot hold a 39-byte message, so
  `packetize` raised and the adversarial test failed for that reason alone. Every
  plant's "FAIL" in that test was ambiguous.
- Stale is sticky, so in a stream with many causes, one missing cause is invisible.
- The single locate-change packet could be randomly dropped.

Fix: a minimum packet size of 84 bytes, fixed seeds when judging plants, and six
directed single-cause tests with an exact expected stale value. P2, P4 and P6 (session
change not stale) are now caught by those tests.

**Bug (test): a missing record hung the run.** Symptom: the P4 run never finished.
Root cause: an unbounded `while sb.pending` wait. Fix: a bounded wait, then
`check_done()`, which reports "580 expected item(s) never observed".

**Bug (my analysis): "dead" logic.** I removed `bad_q` from the deframer as redundant. It
is: a packet ending in `S_TRAIL` already fails `end_ok`. The clean failure seen right
afterwards was the 60-byte stimulus bug above, confirmed on two fixed seeds.

## 2026-11-05 (evening): link up; eth-core-v0.1 RX gate passed on hardware

**Link.** The cable is in: DP83848J ↔ I225-LM at 100 Mbit/s full duplex. `rx_stats`
reprogrammed over JTAG (DONE=1).

**Gate (fpga-eth-core `dc3e0b9`, `data/results/2026-11-05_v0.1_rx_stats_sustained.md`).**
610 s at 8,000 datagrams/s × 1,472 B (98.4 Mbit/s on the wire): 4,880,000 sent, 4,880,000
delivered, payload bytes exact, CRC/RX/runt/oversize/overflow/IP-checksum/malformed all 0,
RX FIFO high-water mark 1 byte of 2,048. PASS on the registered criterion (zero drops).

**Bug (host): datagrams arrive out of order.** Symptom: `seq_err` ≈ `seq_lost` ≈ 0.08% of
datagrams although `udp_ok` = sent. Root cause, narrowed by elimination: WSL egress is in
order (tcpdump, 80,000/80,000); Windows pktmon, following IP IDs, shows order intact through
vmswitch and first broken at `netadaptercx.sys` (I225-LM miniport). WSL `ethtool -L eth0
combined 1` did not help. Not fixed yet: MoldUDP64 would turn it into gaps, so Phase D needs
an in-order host path (Intel driver queue setting, a usbipd USB NIC, or a pinned Windows sender).

**Mistakes of mine worth remembering.**
- Called the 6/6 `seq_err`/`seq_lost` pattern "reordering" before measuring, then called the
  next run's pattern "a skipped check in the FPGA". Neither follows from the counters alone; a
  datagram arriving k late adds about k+1 to `seq_lost` for 3 errors. Only the captures settled it.
- Claimed opening `/dev/ttyUSB1` resets the design. It is closing it with HUPCL set (DTR drop);
  with `-hupcl` the counters persist.

**Environment.** Vivado aborts on a locale error outside `vivado-env`. fpga-eth-core `make ci`
needs `PY=~/OrderBook/.venv/bin/python` (it has no venv of its own). tcpdump installed in WSL.

**Docs.** CLAUDE.md split: §5–§11 to `docs/buildplan.md` (749 → 248 lines), phase table to
`progress.md`, lean-docs rule added.

## 2026-11-05 (night): eth-core-v0.1 tagged; v0.2 TX gate passed

- `eth-core-v0.1` tagged and pushed; P3's hard gate "Streaming full passes" is MET.
- **v0.2 gate** (fpga-eth-core `e148085`, `data/results/2026-11-05_v0.2_udp_echo_sustained.md`):
  `udp_echo`, 600 s at 15,000 datagrams/s (96.3 Mbit/s each way): 9,000,000 echoes of
  1–1,472 B intact byte for byte, 0 lost; every length ≥ 5,782 times; board `udp_ok` =
  `tx_sent`, 0 underruns.
- **Bug (host): no echoes at all.** Symptom: board `tx_sent` = sent, host echo_ok = 0, tcpdump
  on WSL `eth0` sees nothing. Root cause: the Hyper-V firewall blocks all inbound to WSL by
  default. Fix: a Hyper-V firewall rule for UDP from 192.168.10.2 (user ran it as admin).
- **Bug (host checker): false losses.** Symptom: `lost` = `mismatched` in erratic bursts (0 to
  16% per 10 s run, not rate-dependent) while the board echoed every datagram. Root cause:
  `udp_echo_check` wrote a datagram off after 256 later datagrams, and while catching up after
  a stall it sent without reading; intact late echoes then matched nothing. Fix (`79fba80`):
  loss judged 1 s after send, socket drained before every send; a loopback case with 600
  late echoes fails on the old checker (lost = 1,042) and passes now.
- Correction: I first read the board's `udp_ok` before its last UART line printed and thought
  it had missed 11,304 datagrams. The per-second log showed it had not.

## 2026-11-05 (late): host/replay

- `host/replay` (C++20): gzip or plain ITCH file → MoldUDP64 packets (≤ 1,472 B, greedy,
  end-of-session last) over UDP, or `--out` to a file. Pacing: flat out, `--pps`, or
  `--realtime --speed X` (each packet paced by its first message's ITCH timestamp; lag is logged).
  Deterministic `--drop-every/--dup-every/--swap-every` faults. `make host host-test host-lint`,
  all in `make ci`. GoogleTest 1.17.0 comes in via CMake FetchContent, SHA-256 pinned.
- Verified: 14 GoogleTest cases (ASan/UBSan), 25 pytest cases checking replay byte for byte
  against `model.moldudp64.packetize` + `SeqTracker`; 5 injected bugs (payload-limit
  off-by-one, fault on the wrong packet, swap not reordered, little-endian seq, missing count
  cap) each caught, then reverted. AAPL 2019-10-18 extract: 1,669,592 messages → 35,313
  packets, byte-identical to the model (smoke check to a file; not a reported result).
- Lint note: clang-tidy's `bugprone-unchecked-optional-access` does not treat gtest's
  `ASSERT_TRUE(opt)` as a guard, so it is disabled for `test_mold.cpp` only.

## 2026-11-06: ADR 0006 (price levels), model stale rule, order-table RTL

- **Host reordering, no board today.** The I225-LM driver (`e2fn.sys` 2.1.4.3, Lenovo OEM)
  exposes no RSS/VMQ/queue setting, so the "driver queue setting" idea was wrong. Agreed
  plan for the next board session is in `progress.md` (rate sweep, Packet Priority off).
- **ADR 0006** from `analysis/levels.py` over the AAPL day: max level 294,065 shares; up to
  922 live levels per side; ~97k next-best searches; max search distance 188 ticks. 36-bit
  level words make overflow impossible (65,536 orders × < 2^20 shares) at no BRAM cost.
  In-window sub-penny prices: 0 on the day (10 sub-penny prices, all out of window).
- **Model:** `BoundedBook` gains `price_subpenny` and a sticky `stale` (table/ref/shares
  overflow, sub-penny, unknown/duplicate ref, over-reduce). Real day: all 0, `book_stale` 0.
- **Order table RTL** (`rtl/book/order_table.sv`, `rtl/lib/sdp_ram.sv`): records → level
  deltas, one message at a time, first beat in cycle 5. cocotb at 512 and 8,192 sets per
  bank against `BoundedBook`, shadow book compared after every message; 49,271 real
  messages (first 50k of the day) with 0 overflow, never stale.
- **Bug (test gap): stale raised by `price_oow` went unnoticed.** Symptom: that injected
  RTL bug passed the adversarial tests. Root cause: every adversarial stream is already
  stale from unknown refs and overflows, so the flag could not be wrong there. Fix: a
  clean stream with far prices only (`out_of_window_alone_is_not_stale`); it now fails on
  that bug. The other 9 injected bugs were caught from the start.
- **My own miscount:** I hand-counted the order table's latency as 4 cycles and nearly
  "corrected" the header's 5; the new latency test measured 5. Latency numbers come from
  that test, not from reading the FSM.
- **Bug (synthesis): order table failed timing and BRAM budget.** First OOC run: WNS
  −8.449 ns at 125 MHz and 108 BRAM36. Root causes, one per run: (1) the hash's final add
  fed the BRAM read address directly, which fans out to 54 BRAMs across the die (8.5 ns of
  route), and the whole price → index divide sat in one cycle; (2) one 8K-deep RAM per bank
  mapped as 8K × 4 tiles (54 each); (3) the reduce compare/subtract/row-mux chain in
  S_ACT; (4) BRAM clock-to-out into the tag match. Fixes: registered set/tag (S_ADDR), a
  three-stage price class, two 4K-deep halves per bank, per-way reduce arithmetic in
  S_EVAL, BRAM output register. Result: WNS +0.294 ns, 96 BRAM36, 8 DSP, 1,973 LUTs,
  2,446 FFs, 0 critical warnings. Latency 5 → 7 cycles (measured); 4 more injected bugs
  in the new logic were each caught.
- **Model fixes before the level RTL.** A 0-share Add left a 0-size level in `OrderBook`
  (the RTL bitmap would not mark it); `BoundedBook` compared a 5-field state to a 4-field
  initial value, so its first message always published a BBO. Both fixed, each with a
  test that fails on the old model; real-day CSV unchanged.
- **Price levels + bitmap + BBO** (`rtl/book/book_levels.sv`, `book_engine.sv`): matches
  `BoundedBook`'s BBO stream update for update, 0 mismatches: adversarial/directed at 512
  and 8,192 sets, a whole-window stream (~4,200 bitmap searches, up to 2,485 ticks), and
  the first 50k real messages (10,582 BBO updates). Record → BBO: 15 cycles, 21 when the
  best empties (measured).
- **Bug (test gap): worse-edge flag.** Symptom: the injected bug "flag a worse-edge order
  even while the side has levels" passed at 512 sets. Root cause: the seeded stream never
  had a far-worse order live beside in-window levels on the same side. Fix: a directed
  case for both sides in `book_stim.directed`; caught at both geometries now.
- **Timing (book_engine OOC):** −0.240 → −0.335 → +0.006 → +0.065 → +0.089 → +0.069 ns
  at 125 MHz, each run's worst path fixed in turn (L2 read address register, two-stage
  reciprocal multiply, L2 word register in the search, registered hash key, registered
  level-non-zero). Stopped at +0.069 ns: the last runs differ by placement noise. 104
  BRAM36, 10 DSP, 3,172 LUTs, 3,528 FFs, 0 critical warnings. Margin < 1 %.
- **Formal (§8.3):** the best-price search's encoders now live in `rtl/book/prio_enc.sv`;
  `tb/formal/bitmap_search.sby` proves, for any 4,096-level bitmap, that the L1 → L2
  search returns exactly the best set level (bids highest, asks lowest). 38 s; 3 injected
  encoder bugs each fail the proof. `make formal` is in `make ci`.
- **`make sim-full`** (`data/results/2026-11-06_sim_full_book.*`, 1 h 03 min): the whole
  2019-10-18 AAPL day through `book_engine` in simulation, 1,658,958 messages → 831,842
  BBO updates identical to the golden model, `bbo_oow` 4, never stale. Wide stream:
  204,162 messages, 69,739 searches. Random: 970,835 records, 0 mismatches, but below 1M
  on its own and the table saturates (290,065 overflows), so it mostly exercises overflow.

## 2026-11-07: random_1m closed, protocol v1 + golden encoder, host reordering sweep

- **`random_1m` (Phase D "1M+ random events"):** 1,009,900 book records through
  `book_engine`, 0 mismatches, 21.7 min (`data/results/2026-11-07_sim_random_1m.*`). The
  generator gained `Config.max_live` (default off; `generate(1, 50000)` SHA-256 identical
  before/after); capped at 30,000 live, the table stays ≤ 46 % full. Sizing note: even
  without colliding refs, ~1M inserts of churn at 30k live overflow a bucket pair twice
  (model, 200k messages). The real day (27,814 peak) has 0, but this is the margin to
  check on a busier day.
- **Bug (generator): table saturation.** Symptom: random_1m had 290,065 overflows.
  Root cause: adds outnumber removals (~41 % adds vs ~11 % deletes), so live orders grow
  without bound; `rng.choice(list(live))` also made generation O(live) per message.
  Fix: the `max_live` cap; a model test catches "cap ignored" and an off-by-one.
- **ADR 0007 + `docs/protocol.md` v1** (user chose all three): batched output with
  flush on idle, a decode-trace mode for the Phase C full-day check, and a UDP config
  packet with an ack that resets the pipeline. A 12-byte header with `pkt_seq` makes lost
  output datagrams visible.
- **Golden encoder `model/protocol.py`** (+13 tests, offsets taken from the doc's tables;
  6 injected encoder bugs each caught). `Record` now carries its ITCH type byte and the
  decoder's stale bit at decode time.
- **Bug (spec): stale flag timing.** Symptom (on paper): flagging a BBO stale from the
  feed's *current* state would flag updates whose cause preceded a gap, depending on
  pipeline timing. Fix: the feed's stale bit rides with each record (model done; the RTL
  needs a 1-bit sideband through `book_engine`).
- **Mistake of mine: test too weak twice.** My first stale test passed with the feed's bit
  dropped, because the gap also made the book stale. The second version failed on correct
  code, because the default generator makes the book stale early (unknown refs). The final
  version separates the sources: a malformed `D` makes only the feed stale, and the
  default stream makes only the book stale.
- **Host reordering sweep (hardware, `data/results/2026-11-07_host_reorder_sweep.*`).**
  12 × 60 s runs, 0 loss. Reordering does not grow with load (0.024 % at 98 % of line
  rate, up to 0.145 % at 49 %; ±25 % run to run). Packet Priority & VLAN off halves it at
  4,000 pps; Interrupt Moderation off helps at 8,000 but hurts at 4,000. No setting
  reaches zero, so the Windows I225 driver path is a dead end; next is a USB 3 NIC in WSL.
- **Mistake of mine:** a 3 s `cat /dev/ttyUSB1` peek before setting `-hupcl` reset the
  design (the §12 trap). It was harmless before the run, but configure the TTY first, always.
- **Book sideband + per-update stale.** `book_engine` gained `USER_W` (`s_user_i` →
  `m_user_o`, the feed's stale bit now, T1 in Phase E) and `m_stale_o`.
- **Bug (design, caught before RTL existed for it): stale race.** Symptom (on paper): a
  BBO flagged stale by a *later* message's overflow. Root cause: `order_table` takes the
  next record while `book_levels` still searches (6–12 cycles), so the live `stale_o` can
  be ahead of the BBO being published. Fix: each delta beat carries `stale_o` OR that
  cycle's error pulses (the pulses lead `stale_o` by a cycle), which rides as a sideband
  bit. test_book checks it per BBO; the "live sample" and "pulses missing" injections both
  fail, but only at 512 sets: at 8,192 the stimulus rarely goes stale mid-run.
- **USB NIC choice:** this WSL kernel has `ax88179_178a` (+ `cdc_ncm`, `lan78xx`) but no
  `r8152`, so the adapter must be ASIX AX88179, not the more common Realtek RTL8153.

## 2026-11-08: Phase D, output builder (`rtl/out/out_builder`)

- Inbox: request 0008 done (tb-common "Used by" lists fpga-rs-core). 0005 (eth_rx link-up)
  still waits on P3's raw stats dump of a bad case.
- **`sync_fifo`** (FWFT, block RAM, `rtl/lib/`) and **`out_builder`**: records → byte FIFO;
  each datagram's `count` = complete records waiting (flush on idle); ack > stats > records;
  the ack flushes at a datagram boundary. ADR 0008.
- Tests: `test_out` (7) and `test_sync_fifo` (3), scored with `model.protocol.parse`.
  Latency record → first header byte = REC_LEN + 2 cycles, as predicted in the header.
- **Bug (design, before the test ran): mode race.** Symptom (on paper): old-run BBO
  records sent as trace datagrams. Root cause: the control block sets the new mode at
  config time, but the ack goes out only after the book's RAMs are clear, thousands of
  cycles later. Fix: the builder latches the mode when the ack starts.
- **Mistakes of mine: tests too weak, found by bug injection.** "Flush leaves the writer"
  and "mode from the live input" passed at first: the ack waited behind a datagram, so no
  record was half written at the flush, and the mode changed on the ack's own cycle. A
  FIFO visibility bug was invisible through the builder (it never reads near a write).
  Fixes: a directed half-record test, an early mode change, a sync_fifo unit test. Now
  all 9 behaviour-changing injections are caught; 2 more are equivalent mutants.
- Test bugs: records pushed on the flush cycle (breaks the documented contract); stats
  requests closer together than a datagram merge by design.
- **Timing: DSP multiply on the critical path.** Symptom: OOC WNS +0.080 ns at 8 ns.
  Root cause: `cnt × {40, 64}` mapped to a DSP48 after the `min`. Fix: shifts. WNS
  +0.998 ns, 3,245 LUT, 3,513 FF, 0.5 BRAM36, 0 DSP (`docs/reports/ooc_out_builder/`).
- Spec fix: `docs/protocol.md` said the host's `pkt_seq` is echoed in the ack and also
  that the ack's `pkt_seq` is 0. Kept 0 (stated twice; the echoed record identifies it).
- **`out_stats`** (the 40 protocol counters + 1 s tick): `test_stats` compares all 40
  with a reference every cycle under random events on every input, clears, and 64-bit gap
  amounts; 9/9 injected bugs caught (cleared ctrl counters, gap counted as 1, swapped
  slots or stale bits, tick off by one, ...). OOC 8 ns: WNS +4.068 ns, 116 LUT, 2,479 FF,
  615 CARRY4 (`docs/reports/ooc_out_stats/`). `itch_malformed_messages` takes a 2-bit
  count: the feed's malformed and empty-message pulses can coincide.
- Host: Interrupt Moderation confirmed back to Enabled; USB adapter not yet arrived.
- **`out_mux`** (mode routing + BBO/trace record packing, combinational): `test_mux`
  compares 3,000 BBO and 3,000 trace records byte for byte with `bbo_record` /
  `trace_record` (empty sides, locked, equal prices with an empty side, max sizes) and
  every handshake combination in both modes; 10/10 injected bugs caught. No OOC run: no
  registers, so its timing is judged in the full-chip build.
- **`out_ctrl`** (control-port parser + config sequence OFF → RST → CLEAR → ACK → RUN):
  `test_ctrl` judges every payload with `parse_config` (9 malformed kinds, boundary
  values: length 35, mode/locate_valid 2, count 2, truncated datagram) and checks the
  sequence every cycle against a reference: load exactly when idle with a config pending,
  the latest wins, ack only with the book idle, feed in reset until the flush, ack_rec
  stable. 12/12 injected bugs caught after two test fixes: a new config accepted in the
  very cycle a pending one loads (needs a directed, flush-aligned test), and mode = 2 /
  length 35 were too rare at random (now chosen on purpose). OOC 8 ns: WNS +2.659 ns,
  227 LUT, 785 FF, 0 BRAM/DSP (`docs/reports/ooc_out_ctrl/`).
- `book_engine` now exports `busy_o` (the RAM clears end when it falls); `test_book`
  checks it covers both clears (an injected table-only busy is caught at SET_BITS 9).
- Protocol doc: a config arriving while one is applied waits; only the latest is acked.
- **`ob_core`** (whole pipeline, MII to MII) and **`test_core`** (frames in → datagrams
  out): BBO mode at line rate, then trace mode with a configured locate after a reconfig
  from another host. Every record is compared with `model.protocol.expected()`, and 38 of
  the 40 stats counters are compared exactly (uptime only checked to be monotonic and ≥
  each tick; `rx_fifo_overflow` 0). Checks: ack first with `pkt_seq` 0, consecutive
  `pkt_seq`, every frame FCS/IPv4-checked and addressed to the config's sender. 12/12
  injected wiring bugs caught after three test fixes:
  - feed stale not carried to the BBO: undetectable while the book went stale at the
    same moment (losses → unknown refs; the generator's 1 % unknown refs). Phase 1 now
    has a malformed message mid-run, no unknown refs, losses only in the last quarter.
  - `pkt_gate` bypassed: no feed payload ever spanned the feed's release. A straddle
    frame, timed from a measurement (release 65.4 µs after the config's last wire byte;
    a 730-byte payload's burst covers it), now does in both phases, and the test asserts
    it; expected packets follow the spec rule (feed reset low over the whole payload).
  - broadcast not gating the control `udp_rx` by the feed's ready: now an SVA in
    `ob_core` (a beat either side takes is a beat taken from `eth_rx`).
- `pkt_gate` (new, `rtl/lib`): passes or drains whole packets while the decoder is in
  reset; 6/6 injected bugs caught by `test_pkt_gate`.
- Spec/RTL mismatch fixed: `rx_fifo_overflow` counts lost frames (`ovf_frames_o`), as
  `docs/protocol.md` says; `out_stats` took a 1-bit pulse. Now 8 bits; `test_stats`
  catches the old behaviour.
- **Mistake of mine: an injected bug survived on disk.** A background injection run was
  killed (system low on memory) mid-mutation; its `finally` restore never ran, and my
  integrity grep matched the out_builder's `.mode_i (cfg_mode)` instead of the mux's.
  Symptom: phase 2 (trace) produced 36 of 269 records with book counters non-zero.
  Root cause: `out_mux.mode_i` still tied to 0. Fix: restored; injection scripts now
  keep an on-disk backup that the next run restores first. The end-to-end test caught it.
- Board top `arty_top` (MMCM `clk_gen` in `rtl/lib/xilinx`, `cdc_reset_sync`, MAC start
  sequencing as eth-core's examples), `constr/arty_timing.xdc`, `syn/build_top.tcl`, and
  the CDC list in `docs/design.md`. Not yet built in Vivado.
- Sim note: a Python wake-up on every core cycle doubled `test_core`'s run time; the
  release monitor now runs only around each config. `test_core` takes ~100 s.
- **First full-chip build** (`arty_top`, Vivado 2025.2): core 8 ns, WNS +0.059 ns,
  WHS +0.010 ns; worst path `order_table` price-index multiply (7.806 ns, 7 CARRY4);
  8,445 LUT, 13,259 FF, 107.5 BRAM36, 10 DSP; no critical warnings, CDC report only the
  expected Gray-pointer warnings. Bitstream built, not run on the board. ADR 0009 keeps
  125 MHz (`docs/reports/arty_top/`).
- Open: `make sim` ~9 min (target 5); host/capture; first board run.

## 2026-11-09: host/capture and analysis/compare

- **`host/capture`** (C++): sends the config to the FPGA's control port, resends every
  `--retry-ms` until the ack (type 0x11, `pkt_seq` 0, our record echoed), creates
  `--ready-file`, then logs every well-formed FPGA datagram (u16 BE length + payload, the
  same framing as `replay --out`). Counted, never logged: pre-ack datagrams, other sources,
  malformed ones. Counts `pkt_seq` gaps / datagrams lost / backwards steps and other hosts'
  acks; stops on `--idle` (after the last record), `--duration` or Ctrl-C; prints the last
  stats snapshot by counter name. `host/test_capture.py` (22 tests) runs it against a fake
  FPGA on loopback, judging the config with `model.protocol.parse_config`. 12/12 injected
  bugs caught after two test fixes: no datagram had only a wrong `record_len` (my first
  case was also the wrong length, so the length check hid it), and none was one byte too
  long.
- **`analysis/compare.py`**: the capture log vs `model.protocol.expected()` over the
  `replay --out` packets; the config (mode, window, locate) comes from the ack at the
  head of the log. Undoes host reordering and byte-identical duplicates by `pkt_seq`; a
  lost datagram fails the run. `analysis/test_compare.py` (12 tests, now in pytest's
  `testpaths`); 7/7 injected bugs caught. Two test assumptions of mine were wrong: 2,000
  generated messages give only 120 BBO changes, and a window edge 100 ticks above the
  base moves no generator price out of it.

## 2026-11-09 (later): first full-pipeline runs on the board

- `make prog` added (`syn/prog.tcl`: JTAG program + DONE check); `scripts/board_run.sh`
  (capture → replay after the ack → compare).
- Host now on the **ASIX AX88179 USB adapter** in WSL (usbipd, `cdc_ncm`), 192.168.10.1/24,
  static neighbour for the FPGA. The I225-LM link had dropped mid-session: the Arty had
  fallen off usbipd (no `/dev/ttyUSB*`), so presumably it lost power and with it the
  bitstream and the PHY's reference clock.
- **First hardware result** (`data/results/2026-11-09_first_board_run.md`): 200,000 AAPL
  messages at 500 pps: trace mode 198,084/198,084 records and BBO mode 91,021/91,021
  updates match the golden model byte for byte (BBO reproduced in a fifth run).
- At 2,000 pps: frames lost at the MII FIFO (trace 54, BBO 2), all counted; after the
  loss every BBO is flagged stale, starting exactly at the first wrong one. Cause of the
  loss open (see progress.md).
- Bug (mine, procedural): `sudo` through the session's `!` prefix cannot prompt for a
  password; host network commands go in the user's own terminal.

## 2026-11-09 (later): where the 2,000 pps losses come from

Data: `data/results/2026-11-09_board_loss_diagnosis.md`.
- Symptom: MII FIFO overflow at 2,000 pps (not at 500), in clusters at the same packets
  every run, trace mode much worse than BBO. Root cause (measured): `replay` falls behind
  when the host stalls (18 ms reading `/mnt/d`, 5 ms from the Linux disk; load average
  33–44 on 16 CPUs from other sessions), then sends the late packets back to back; in a
  burst the board's output needs more wire time than its input, so backpressure fills the
  2 KB FIFO. Fix tried: a pacing rule that never catches up. It made things worse (the
  host loses ~35 % of the sender's time; 1,290 pps achieved). Reverted. Kept: `replay`
  reports `max_lag_ms` in `--pps` mode.
- NCM aggregation (`tx_timer_usecs` 400 → 0) cut trace-mode loss 54 → 4 but did not
  change the BBO-mode loss: the same 2 frames at the same place.
- Full AAPL day at 500 pps: ended after 8,477 packets. All losses were host-side:
  `fq_codel` drops (21), a > 3 s USB/IP stall, 91 output datagrams lost on receive.
- New: `compare` reports `unflagged_wrong` (BBO wrong without the stale flag, matched by
  mold_seq so it stays exact after a loss); 0 on all six BBO runs (436,778 BBOs); 3/3
  injected bugs caught. Test trap: the generator's default 1 % unknown refs leave almost
  every record stale, so a test of the flag needs `p_unknown_ref=0`.
- Full day again with `pfifo` instead of `fq_codel` (user ran `tc qdisc replace`): every
  frame reached the board, no output loss; first 408,158 BBOs exact, then one MII FIFO
  overflow from a 139 ms replay stall at load ~30
  (`data/results/2026-11-09_full_day_board.md`). Board had lost its bitstream again before
  the run (no carrier; reprogrammed).
- **Zero-mismatch full day on the board.** With the polytopia-engine jobs paused (load
  2.6), the whole AAPL day at 500 pps: 831,842/831,842 BBO updates byte-identical to the
  golden model; 0 frames lost anywhere; replay lag max 1.4 ms (vs 139 ms under load).
  Mistake of mine: a `pgrep -f` "is the run going" check matched my own watcher's command
  line, so I told the user the run had started when it had not.
- **Demo:** `analysis/bbo_view.py`, a live terminal view that follows capture's log
  (now flushed per datagram): ITCH clock, bid/ask, the whole day on a 04:00-20:00 axis,
  the last 4,000 updates, and every BBO checked against the model by cause message.
  `--gif` renders a recorded log; `make demo-gif` →
  `docs/media/2026-11-09_phase-d_board-full-day-bbo.gif` (2.7 MB, 20 s). The user found
  the first version (recent updates only) too fast to follow; the day chart was the fix.
  Two more zero-mismatch full days on the board (shoot and demo runs).
  Bugs in my GIF code: glyph ink width used as cell width (segments overlapped);
  chart width one column over the line limit (rightmost column and "20" label cut).
- Phase D checklist: on the board's full day `table_overflow`, `ref_overflow`,
  `shares_overflow`, `unknown_ref` 0; `bbo_oow` 4 = bounded model, 4 records flagged;
  `price_oow` 16,099 = model (ADR 0002 explains it). Media: `analysis/board_bbo.py` +
  `plots.py` board-vs-model day plot (650 minutes, 0 differ); demo GIF re-rendered from
  `day500c` after the `demo` log it came from was overwritten by a later run (the GIF was
  also rewritten mid-run from a half-written log; restored). README updated.
- Second day chosen by the user: 2025-12-12 (12.55 GB, the largest listed). Download
  started (3 MB/s); analysis left for the next session.

## 2026-11-10: second day overflows the table; 16-way table; both days exact on the board

- fpga-coord: request 0011 (P4, serdes-lab in fpga-eth-core "Used by") done (`06a9a6d`).
- Day file `S121225-v50.txt.gz` checked (`gzip -t`, SHA-256 into MANIFEST, size = server
  Content-Length). 923,744,247 messages; AAPL 3,542,448, base 27,801, max ref 1.02e9.
- **AAPL peaks at 54,981 live orders (day 1: 27,814).** The 2 × 8,192 × 4 table overflowed
  14,717 times in the model: 1.34 M unknown refs, stale from pre-market. 0 unflagged wrong
  BBOs (new `bounded_check` column). Same entries as 2 × 2,048 × 16: 0 overflows on both
  days; 2 × 4,096 × 8: 529. User chose 16 ways → ADR 0010. The RTL was generic, so only the
  defaults changed. OOC WNS +0.128 ns; table 96 → 100 BRAM36, 2,099 → 6,872 LUTs.
  Full chip WNS +0.004 ns (worst path: the 16-way match), 111.5 BRAM36.
- Bug injection (ties to bank 1, full off-by-one, overwrite a valid way): all caught.
- sim-full at the new shape: day 2 916,429 BBOs = model, day 1 unchanged, random 1M 0
  mismatches. Day 2 alone is 87 min of sim.
- **Board: both days byte for byte** (day 2 916,429/916,429; day 1 831,842/831,842) at
  load 15 to 22. Two failed day-1 attempts, both detected: host receive lost 138 output
  datagrams; replay bursts (44 ms lag) overflowed the MII FIFO 3 times.
- Bugs (symptom → root cause → fix):
  - Python reads of the 12.5 GB file and even `cp` failed with ENOMEM after 1 to 33 min →
    drvfs (`/mnt/d`) reads fail under memory pressure (other sessions filled swap) → copy
    in 256 MB chunks to `~/itch-cache/` (ext4) with retries, SHA-256 checked.
  - `make ci` failed in `test_stats.sparse` → p_clear 0.0005 over 6,000 cycles draws no
    clear ~5 % of seeds → one forced clear mid-run (`c17dec9`).
  - `test_core` straddle assertion failed after the reshape → the shorter clear moved the
    feed release from 69 to 33 us → payload retuned 730 → 325 B from the measured window.
  - `board_run.sh` would have replayed day 1's packets for day 2 → packet cache keyed by
    count only → keyed by day too (`8318c33`).
  - Mine: a backgrounded `&&` chain lost its variables, `>( )` ran under `sh`, and
    `pkill -f chunkcopy.sh` killed my own shell (the `pgrep -f` trap again).
- **Media run (23:01, load 5):** `d2_shoot`, day 2 exact again (916,429/916,429), 0 FIFO
  overflows, 3 output datagrams reordered on the host (none lost). The user asked me to make
  the captures: `make demo-gif-day2` renders the day-2 GIF (`bbo_view`) and the `compare`
  output (new `analysis/term_shot.py`, drawn from the saved text). Board photo and video stay
  with the user (optional). README: day-2 media section; the "sizing from one day"
  limitation was stale and now says two days. Tagged `v0.4-book` on the user's go-ahead.
- Bug (symptom → root cause → fix): `make ci` failed in `test_order_table.adversarial_full_rate`
  at `SET_BITS=9` ("stimulus never produced table_overflow", SEED=4188399474) → since ADR
  0010 an overflow needs 33 live refs in one bucket pair, and the generator's 64 aimed refs
  rarely have that many live at once (the reshape's ci passed by luck) → a directed
  `overflow_block` (2 × WAYS + 1 colliding Adds, then Deletes) in the adversarial streams of
  `test_order_table` and `test_book`. Overflow is now reached and checked at 2,048 sets too,
  the board's geometry, which the old skip left untested.

## 2026-11-11 — Phase E started: T0/T1/T2 end to end, first board distribution

- ADR 0011: one 64-bit core-clock counter (no fast timestamp clock); T0 matched to frames
  by two queues on eth_rx's one-status-per-frame contract (`rtl/meas/t0_track`, golden
  model `model/timestamps.py`), SFD through a new toggle synchronizer (`rtl/lib/cdc_pulse`).
  No fpga-eth-core change. T0/T1 ride the book's sideband (USER_W 129); T2 = first valid
  cycle at the book output. New stats counter `t0_lost` (41 counters).
- Simulation: T0 − SFD = 8 cycles on every BBO; T2 − T1 equals the budget in every
  no-wait case (14 out of window, 17 normal, 21/24 Replace, 23 best empties).
  `docs/latency.md` written. Injected bugs (8 across t0_track, feed and core) all caught
  except one that turned out unobservable by construction (T0 at record time).
- Timing: the sideband pushed the 16-way match path to −0.204 ns. Removed the half-depth
  table RAM split (ADR 0010's named fix): WNS +0.038 ns, fewer LUTs/FFs than v0.4.
- **Board, day 1, timestamped:** 831,842/831,842 still exact; T2−T1 min 14, p50 37, p99
  125, max 13,348 cycles; T2−T0 p50 128 µs. Mode 17 = budget. Open: peaks at 102/119/125
  cycles (fixed-length stall, suspected out_builder), not yet a headline.
- Bugs (symptom → root cause → fix):
  - `make ci`: test_out ValueError on stats_i width → out_builder's default N_STATS still
    40 → 41.
  - p99.9 reported the max in latency.py's test → float 99.9/100*1000 = 999.0000000000001
    rounds up a rank → exact Fraction arithmetic.
  - Full-chip build failed −0.204 ns → extra sideband FFs crowded the table's match path
    → one RAM per bank (no output mux).
  - Mine: syn/build_top.tcl lists RTL dirs by hand, rtl/meas was missing; a failed build
    overwrote the committed reports (restored with git checkout before committing).

## 2026-11-11 (later) — T2−T1 tail explained and removed (ADR 0012)

- The first board day's tail (peaks 102/119/125, 19 outliers to 13,348 cycles) was the
  output, not the book. `analysis/t2_gaps.py`: behind each peak the previous BBO's T2 is 65
  cycles earlier (71 for 125): out_builder's byte writer took 64 cycles per record and
  held `s_ready` low, while messages inside a packet reach the book every 22–82 cycles.
  `analysis/tx_queue_model.py` puts the outliers in TX-path bursts (approximate: same 5
  bursts, not the same records).
- ADR 0012 (user's choice of three options): 512 × 512-bit record FIFO inside out_builder,
  flushed by the ack in the same cycle as the byte FIFO. test_out: new
  `accepts_every_cycle_until_full`; 4 injected bugs, 3 caught, 1 equivalent. test_core: every
  T2−T1 on its budget (was 18–125). Full chip WNS +0.015 ns, 119 BRAM36.
- **Board, day 1 (`e2_rf`):** 831,842/831,842 exact; T2−T1 min 14, p50 17, p99 24, max 29
  cycles (232 ns). Every BBO equals its cause's no-wait budget
  (`analysis/latency_causes.py`). Two budget cases were missing from docs/latency.md and the
  book_levels header: 29 = Replace whose old order was alone at the best (the first delta's
  search delays the second by 5), 18 = Delete that empties its side (no search). Both now
  pinned in test_book `latency_matches_header`.
- Bugs (symptom → root cause → fix):
  - Bug-injection run hung 20 min instead of failing → test_out had no timeouts; a
    blocked `push()` waits forever → `timeout_time` on every test_out test.
  - `make ci` failed in model-test → ruff B905 (`zip` without `strict`) in the new test →
    `strict=True`.
  - Mine: `pkill -f "tb/run.py test_out"` killed my own shell again (the `pgrep -f` trap);
    the injected file had to be restored by hand. Don't pkill -f with a pattern that
    appears in the calling command.

## 2026-11-11 (evening) — latency plot; eth-core 0005 hardware runs; cut-through proposal

- `make plots`: `docs/media/2026-11-11_phase-e_board-latency-t2-t1.png` (before/after CCDF,
  per-cause histogram with nearest-rank percentile markers).
- fpga-eth-core request 0005 (link-up reset gating, `mdio_link_mon`, untagged since 11-07):
  350 reprogram-then-stream cycles on the board (`fpga-eth-core`
  `data/results/2026-11-11_0005_reprogram_cycles.md`). Gated 249 clean + 1 corrupted
  (likely a design that was not reprogrammed); ungated control 0/100 corrupted. The fault
  does not reproduce on demand here, so the fix is not demonstrated: no tag.
- ADR 0013 (cut-through) drafted as Proposed; needs the user's go-ahead (eth-core interface
  addition, t0_track change, stale on a delivered bad frame).
- Bugs (symptom → root cause → fix):
  - Harness exited silently with code 2 → `vivado-env`'s `ls` of an unmatched glob fails
    under `set -o pipefail` → relax `-euo pipefail` around it.
  - Harness exited after its first poll, leaving `cat /dev/ttyUSB1` orphaned and the
    caller's pipe open → `grep` with no match yet exits 1 under pipefail → `|| true` and an
    EXIT trap that kills the reader.
  - UART fields failed bash hex arithmetic → CRLF line endings → `tr -d '\r'`.
  - 100 cycles would have taken ~5 h → Vivado start-up ~2.5 min per program at load 13 →
    openFPGALoader (SRAM, ~5 s).
  - Run 1 kept no per-cycle logs, so its one corrupted cycle can't be diagnosed → the
    harness now keeps UART/programmer/sender logs and a "fresh design" check.
- Pmod scope pulses: JA1 one cycle after each T1 (feed_frontend's existing `t1_o`), JA2 one
  cycle after each T2, stretched to 4 cycles, IOB output registers (OLOGIC 6 → 8); pins from
  Digilent's master XDC at the same commit as eth-core's. test_core checks every BBO's T1
  and T2 have their pulse. Full chip WNS +0.028 ns (host load 38: ~1 h build).
- User decisions: ADR 0013 accepted as proposed; 0005 next = link flaps + ILA; a scope is
  available for the cross-check.

## 2026-11-12 — cut-through (ADR 0013): model, eth-core option, OrderBook side

- P5 request 0012 done (tb-common Used by: learned-resilience).
- User decision: the cut-through release is `eth-core-v0.3`; the 0005 fix moves to v0.4
  (request 0005 and `dependencies.toml` updated; v0.3 release test = sim both modes +
  `rx_stats` cut-through ≥ 10 min on the board, zero drops).
- Golden model first: `T0Track(cut_through=True)` (`model/timestamps.py`), tested against
  a frame-level schedule (`model/tests/test_timestamps.py`, 49 tests, 5 injected bugs
  caught). Needs a new eth_rx output, `emitted_o`, and a one-entry hold (ADR 0013).
- fpga-eth-core `71097d9`: `eth_rx` `CUT_THROUGH`, `emitted_o`, `m_tuser_o`; oversize
  checked in eth_rx in cut-through. Sim: first byte 520 ns after the SFD (store-and-forward
  121,536 ns, 1,514-byte frame). `rx_stats` cut-through build WNS +0.592 ns.
- OrderBook (uncommitted until the tag): t0_track cut-through mode (5/5 injected bugs
  caught); ob_core stale on a delivered bad frame + stats counter 41 `cut_through_bad`
  (N_STATS 42), BBO flag `cut_through`, control datagrams only from frames already ended
  good; `CUT_THROUGH` through arty_top and `syn/build_top.tcl ... ct`. test_core in both
  modes + `ct_bad_frames` (3/4 injected bugs caught; the 4th was an unreachable term,
  removed and replaced by an assertion).
- Bugs (symptom → root cause → fix):
  - Model: a frame start after a late frame's start mismatched its T0 → a start taken from
    `hold` set `started`, so the next frame's status thought its own start had happened →
    only a PEND start sets `started`.
  - eth_rx cut-through: a good 339-byte frame came out marked bad → the oversize hold was
    set on the same cycle its tlast beat left (set overrode clear) and leaked to the next
    frame → set only while the beat waits.
  - `ct_status_order` reported no overflow → the stall phase still drained 2.5 B/µs →
    full stall.
  - test_core cut-through: no straddle of the feed release → the 325-byte payload streams
    at wire speed and ends before the release → 400 bytes in cut-through. The "clean" run
    went stale → `feed_packets` injects damaged messages → plain `packetize`.
  - `pkill -f` on the sim name killed its own shell again (pattern in the command line).
