# 0015: 64-bit feed path for the capacity measurement

**Status: Accepted 2026-11-14** (user approved the plan as proposed). Buildplan §6.7 and the Phase E DoD ("injector: pipeline
capacity in messages/s at `DATA_W` = 8 and 64"); carries out the plan in ADR 0005.

## Context

At `DATA_W` 8 the on-chip injector measured 4.11 M messages/s (1,043 messages in 31,703
cycles, `data/results/2026-11-13_injector_board.md`). That is the byte-wide input's
limit: ~30 cycles per message, while the book is idle most of the time. The book takes one
message at a time: `order_table` presents its first delta 7 cycles after the handshake
(a Replace takes two), and `book_levels` takes 4 to 14 cycles per delta (headers of both).
So at 8 bytes per cycle (~4 cycles per average message) the book, not the input, should
set the capacity. The expected gain is roughly 2× to 3×; only the measurement decides it.

The MAC delivers one byte per core cycle and the link carries a tenth of that, so the
64-bit path matters only for the injector. It must still be correct on real traffic.

## Options considered

| Question | Options | Recommendation |
|---|---|---|
| Where the width is 64 | (a) injector → `mold_deframer` → aligner → `itch_decoder` all at 64, book unchanged; (b) injector emits messages already split (no deframer at 64); (c) 64 only in the decoder, behind an 8→64 packer | **(a)**: measures the whole feed path, as the DoD asks. (b) skips MoldUDP64 framing, so the number would not be the pipeline's; (c) leaves the input at 1 byte/cycle, which measures nothing new. |
| Network feed at `DATA_W` 64 | (a) an 8→64 packer between `udp_rx` and the deframer, so a 64-bit build still runs the full day from the network; (b) the 64-bit build is injector-only | **(a)**: a 64-bit build can then be checked on the board with the same full-day comparison as today (831,842 BBOs, byte for byte), which proves the wide path on real traffic, not only in simulation. |
| Message alignment | (a) a separate aligner after the deframer puts each message's first byte in lane 0 (ADR 0005's plan); (b) the decoder takes any start lane | **(a)**: the decoder's capture registers are already indexed by byte offset in the message, so with lane 0 = byte 0 a beat writes bytes 8k..8k+7 by index alone. (b) spreads alignment muxes into the decoder. |
| Width selection | build parameter `DATA_W` (8 default, 64), elaboration error otherwise (as today) | One parameter; `make sim` runs both widths (CLAUDE.md §2), `syn/build_top.tcl` gains a width argument. |
| Verification | golden model unchanged (the records are the same at any width); cocotb at both widths with random `tkeep`/backpressure; coverage bins for every message start lane and every header/length-prefix straddle (§8.1); bug injection; board: full day at 64 matches the model, then the injector run | as listed |

## Decision

As recommended above, in this order, each step verified before the next:
1. `mold_deframer` at `DATA_W` 64: header and length-prefix parsing across lanes; output
   one message per tlast, any start lane, `tkeep`.
2. Message aligner: each message's first byte to lane 0.
3. `itch_decoder` at 64: up to 8 bytes per beat into the existing capture registers.
4. 8→64 packer for the network feed; injector and meter at 64 bits per beat.
5. Full chip at `DATA_W` 64: timing at 125 MHz, the full-day board comparison, then the
   injector run (capacity at 64).

## Consequences

- More logic in the deframer and decoder (8 lanes of compares); timing at 125 MHz is the
  main risk, mitigated as in ADR 0009 by registering compares a beat ahead.
- `make sim` time roughly doubles (two widths).
- No change to the book, the output, the protocol or the host tools.

## Measured evidence

**Step 1, `mold_deframer` at 64 (2026-11-14).** Implemented as `mold_deframer_w64` behind a
width-selecting `mold_deframer`; the 8-bit implementation is unchanged (`mold_deframer_w8`).
- `tb/cocotb/test_deframer.py` at `DATA_W` 8 and 64, against `SeqTracker`: 4 tests per
  width (clean, adversarial with backpressure, every lane at full rate and with
  backpressure). Coverage asserted at 64: message start and end at all 8 lanes, packet
  end at all 8 lanes, length prefix straddling two beats, every header truncation point.
- Throughput at 64, clean 6,000-message stream: 31,874 cycles for 26,429 beats
  (bound asserted: beats + messages + packets + 4).
- Bug injection: 16 distinct bugs (lane, off-by-one, straddle, T0, duplicate on a new session,
  precomputed-flag errors); every one fails the test. It found a stimulus hole (a new
  session's first packet as a duplicate, now covered) and a dead sticky flag (removed).
- Out-of-context at 125 MHz: WNS +0.664 ns, 669 LUT, 1,161 FF, 0 BRAM
  (`docs/reports/ooc_mold_deframer_w64/`). The first version failed (-1.493 ns:
  length-prefix lane mux -> 16-bit compare -> tready); fixed by computing each lane's
  prefix verdicts as the beat is accepted.

**Step 2, message aligner `msg_aligner` (2026-11-14).** Residual buffer (up to 7 bytes);
each input beat is shifted to lane 0 and appended; a message's last beat that overflows
one output beat leaves in two (one extra "flush" cycle).
- `tb/cocotb/test_aligner.py` (DATA_W 64): expected beats built independently
  (`bytes_to_beats` of the message), compared beat by beat (tkeep and kept bytes) with
  seq, T0 and tuser; garbage in unkept input lanes. Coverage asserted: every
  (start lane, length mod 8) pair, single-beat messages, the flush. At full rate,
  cycles <= input beats + flushes + 2 (asserted; measured 12,239 for 11,476 beats and 762
  flushes).
- Bug injection: 10 bugs (keep mask, shift lane, full/flush thresholds, residual clear,
  flush tkeep/tuser/seq, tready during flush, tail tkeep); every one fails the test.
- Out-of-context at 125 MHz: WNS +2.403 ns, 528 LUT, 400 FF (`docs/reports/ooc_msg_aligner/`).

**Step 3, `itch_decoder` at 64 and `feed_frontend` at 64 (2026-11-14).** Each capture
byte at message offset o is written from beat o / 8, lane o % 8 (one fixed source lane,
no lane muxes); the byte count advances by the beat's tkeep. At `DATA_W` 8 this is the
previous logic. `feed_frontend` inserts `msg_aligner` at 64 only.
- `tb/cocotb/test_feed.py` now runs at 8 and 64 (9 tests each, records and counters vs
  `FeedDecoder`). Coverage asserted at 64: each book type (A F E C X D U) entered the
  aligner at all 8 lanes (buildplan §8.1 "every alignment").
- Bug injection at 64: capture lane, capture beat, byte count, beat index and a bypassed
  aligner; every one fails the test.
- Out-of-context `feed_frontend` at 125 MHz: DATA_W 64 WNS +0.460 ns, 1,249 LUT,
  2,518 FF, 0 BRAM (`docs/reports/ooc_feed_w64/`); DATA_W 8 WNS +1.577 ns, 633 LUT,
  1,857 FF (`docs/reports/ooc_feed/`, refreshed; the previous report predated ADR 0011).

**Step 4, packer, injector and `ob_core` at 64 (2026-11-14).** `rtl/lib/byte_packer`
(bytes to 8-byte beats, the packet's T0 carried with its beats; one byte per cycle);
`injector` with `DATA_W` (8 lanes of {last, byte} per ROM word, payloads start at lane 0;
the 64-bit image holds the same 22 packets as the 8-bit one, 3,971 of 4,096 words, so both
capacity runs replay the same 1,043 messages); `ob_core` with `DATA_W`.
- `test_packer`: exact beats vs `bytes_to_beats` for lengths covering every final lane,
  random gaps on both sides, T0 changed every cycle, tuser, reset mid-packet; one byte per
  cycle at full rate. 9 injected bugs (lane, keep, meta sampling, valid, refill, tuser,
  reset) each fail it.
- `test_injector` at 8 and 64: the payloads exactly, tkeep shape, one ROM word per cycle,
  replay after reset; first beat valid at the 5th rising edge after reset at 8, the 4th
  at 64 (the header's previous "4 cycles" was off by one).
- `test_core` at DATA_W 64, both eth_rx modes: frames to datagrams vs the model, the
  inject run (248 BBOs, model digest). Simulated inject span 9,059 cycles at 64 vs 31,703
  at 8: a simulation figure; the capacity result is step 5's board run.
- Found on the way, fixed on main: `test_core`'s stop rule could read stats from before
  the last frames (a test bug) and parallel sim sets shared one results.xml. Fixed on this
  branch: the every-lane coverage now holds by construction (`test_feed every_alignment`).
  A real ordering bug in the stale flag led to ADR 0016.

**Step 5, full chip and board at 64 (2026-11-15).** `arty_top` and `syn/build_top.tcl`
take `DATA_W` (`-tclargs 8 sf 64`, reports in `docs/reports/arty_top_w64/`).
- Full chip, store-and-forward, 125 MHz: WNS +0.004 ns, WHS +0.051 ns, 127 BRAM tiles,
  13,568 LUTs, 21,232 FFs.
- Injector on the board, 3 runs: 9,059 cycles each (= simulation), 248 BBOs, the model's
  digest. **14.4 M messages/s, 3.50× DATA_W 8** on the same 1,043 messages
  (`data/results/2026-11-15_injector_board_w64.md`). At 3.50 of 8 bytes per cycle the
  input no longer sets the rate; which stage does is not measured.
- Full AAPL day on the board: 831,842/831,842 BBOs match the model. T2−T1 unchanged
  (p50 17 / p99 24 / max 29 cycles); T2−T0 2–12 cycles longer (packer and aligner sit
  before T1) (`data/results/2026-11-15_w64_full_day_board.md`).
- Not done: a cut-through build at 64.
