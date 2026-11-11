# 0012: Record FIFO between the book and the output byte writer

**Status: Accepted 2026-11-11.** Partly supersedes ADR 0008 (its rejection of a wide
record FIFO). Changes `rtl/out/out_builder.sv`.

## Context

The first timestamped board day (`data/results/2026-11-11_first_latency_board.md`) gave
T2−T1 p50 37 / p99 125 cycles against a no-wait budget of 14–24 (`docs/latency.md`), with
sharp peaks at 102, 119 and 125 cycles and 19 values from 1,457 to 13,348.

Analysis of that log (`analysis/t2_gaps.py`, `analysis/tx_queue_model.py`; CSVs next to the
result):

- **Peaks: out_builder's writer paces the book.** ADR 0008's writer copies a record into
  the byte FIFO at one byte per cycle and holds `s_ready_o` low meanwhile, so the book can
  hand over one BBO per 65 cycles. Inside a MoldUDP64 packet, messages reach the book
  every 22–82 cycles (eth_rx releases a frame at one byte per cycle), so the book
  backs up to its input and T2−T1 settles at fixed values. Behind every peak the previous
  BBO's T2 is 65 cycles earlier (102: 65,323 of 83,814; 119: 45,357 of 48,914), or 71
  (125: 15,363 of 15,917). ADR 0008 assumed ≥ 160 cycles per message (it reasoned about
  wire rate, not eth_rx's burst out of its store-and-forward buffer).
- **Outliers: the TX path fills.** Each comes after 57–120 BBOs in the 40k cycles before. The
  output path holds about 85 BBOs (byte FIFO 2 KB, udp_tx one payload, eth_tx 2 KB) and the
  100 Mb/s wire drains about one per 675 cycles (22 records per 1,486-byte frame). A
  datagram-level model of that path driven by the board's T2s puts its 12 predicted stalls
  in the same 5 bursts as the 19 observed (not on the exact records), plus 2 bursts that
  did not stall.

So most of the measured tail is output backpressure, not book latency.

## Options considered

| Option | Cost | Effect |
|---|---|---|
| **Record FIFO, 512 bits × 512, in front of the writer** | ~7.5 BRAM36 (the width sets the count: 512 deep costs what 64 deep does) | The book hands over a BBO in 1 cycle until 512 are queued: removes the pacing, absorbs about 7× the output burst before the book waits |
| Byte FIFO 64 bits wide (8 cycles per record) | ~same BRAM | Removes most of the pacing (8 < 22 cycles per message); bursts still back up at ~85 BBOs |
| Keep; report the no-wait subset as book latency | 0 | Headline would be a filtered subset; the measured end-to-end tail stays |
| Record FIFO in ob_core between out_mux and out_builder | same | The ack flushes out_builder one cycle before `flush_o`; a stale record could slip into the writer in between. Inside out_builder one `flush` empties both |

## Decision

A FWFT `sync_fifo` of 512-bit records (`REC_ADDR_W` = 9) inside out_builder, in front of
the byte writer. `s_ready_o` = record FIFO not full. The ack's `flush` empties it in the
same cycle as the byte FIFO and the writer. Wire format, batching and ack semantics are
unchanged (ADR 0007, 0008).

## Consequences

- The book waits on the output only when 512 + 32 BBOs are queued (545; 564 traces).
- Record → first header byte at light load: 64 + 4 cycles (was + 2): the FWFT FIFO's
  2-cycle visibility. Not in T2−T1 (T2 is at the book's output).
- Output bandwidth is unchanged: a sustained BBO rate above ~185 k/s still backs up the
  book. 100 Mb/s TX is the limit (README line-rate wording).
- BRAM: 111.5 → 119 of 135 BRAM36, which leaves 16 for Phase E (histogram, deeper MII FIFO).

## Measured evidence

- `test_out` (2026-11-11): `accepts_every_cycle_until_full` (one accept per cycle for 512
  records with the output stalled, exactly 545 / 564 before `s_ready_o` drops, nothing lost
  after), plus the existing stream, saturation and ack tests (all now with a sim-time
  timeout, so a stalled handshake fails instead of hanging). Injected bugs: record FIFO
  not flushed by the ack (caught, ack_mid_traffic); writes ignore full (caught, 4 tests);
  `s_ready_o` gated by the writer again (caught, 7 tests); pop not blocked during flush
  (not caught: equivalent, since the FIFO's flush and the writer's reset take priority).
- `test_core` (one seeded run, 100 BBOs at line rate): every T2−T1 is exactly its no-wait
  budget (14: 3, 17: 63, 23: 21, 24: 13); before this change the same test logged 18–125.
- Board, day 1 (`data/results/2026-11-11_record_fifo_board.md`, run `e2_rf`): 831,842 /
  831,842 exact; T2−T1 min 14, p50 17, p99 24, p99.9 29, max 29 cycles (was p50 37, p99
  125, max 13,348). Every BBO equals the no-wait budget of its cause; no BBO waited.
- Full chip (`docs/reports/arty_top/`, 2026-11-11): WNS +0.015 ns (was +0.038; the worst
  path is still the order table's 16-way match), 119 BRAM36, 12,354 LUT, 19,091 FF, 10 DSP.
