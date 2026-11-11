# 0011: Timestamps T0/T1/T2: one core-clock counter, T0 matched to frames by queue

**Status: Accepted 2026-11-10** (design; measured evidence is added as Phase E lands).

## Context

Phase E reports latency per BBO update (buildplan §6.5). `docs/protocol.md` already has
three 64-bit fields per update, `t0`, `t1`, `t2`, sent as 0 so far:

- **T0:** the cause frame's start-of-frame delimiter (SFD), in the MII receive clock domain
  (25 MHz). `eth_rx` exports it as `sfd_o`, a one-cycle pulse; the consumer synchronizes it.
- **T1:** the last byte of the ITCH message that caused the update (core domain).
- **T2:** the update valid at the book's output (core domain).

Two questions: which clock counts, and how a T0 captured at the SFD finds the messages of
that frame. Frames are buffered whole in `eth_rx` (store-and-forward), so by the time a
frame's bytes reach the feed parser, later SFDs have already happened.

## Options considered

**Counter clock**
- **One free-running 64-bit counter in the core clock domain (125 MHz, 8 ns).** T1 and T2
  are core events, so T2 − T1 is an exact cycle count. T0 is quantized by the 25 MHz MII
  clock (40 ns) whatever the counter's rate.
- A separate fast timestamp clock (e.g. 400 MHz, 2.5 ns). Rejected: it adds nothing to
  T2 − T1 (both ends are core-clock events), claims precision at T0 that the 40 ns nibble
  clock does not have, and a wide counter plus Gray-code crossing at 400 MHz risks timing on
  the -1 part, whose core already closes with 4 ps margin (ADR 0010).

**Matching T0 to a frame's messages**
- **Queues in `ob_core`, using `eth_rx`'s per-frame status (chosen).** `eth_rx` gives
  exactly one status per frame that had an SFD, in wire order (`frame_ok`, `crc_err`,
  `rx_err`, `runt`, `oversize`, or `ovf` covering `ovf_frames`). The synchronized SFD
  pushes the counter into a queue. Each status pops the frames it covers. A good frame's T0
  moves to a second queue, which pops when that frame's first byte leaves `eth_rx`.
  `udp_rx` streams with one cycle of latency and its input can be at most one beat ahead
  of its output. So when the feed's first payload byte is accepted, the frame inside
  `udp_rx` is the one that most recently left `eth_rx`, and its T0 is latched for the
  datagram. The decoder copies it to each message at the message's first byte. No
  `fpga-eth-core` change.
- Add a frame-metadata sideband to `eth_rx`/`udp_rx` in fpga-eth-core (a v0.3 interface
  change). Cleaner inside the core, but it changes a shared block's interface for one
  consumer, and needs a release. Rejected for now. If P3/P4 need frame timestamps it can
  move there, with the same queue logic and tests.
- Timestamp at the first payload byte instead of the SFD. Rejected: that is not T0 as
  §6.5 defines it, and it hides the store-and-forward wait, which the cut-through
  comparison exists to show.

**Width carried through the book**
- **Full 64-bit T0 and T1 in the book's per-record sideband (`USER_W` 1 + 64 + 64).**
  No arithmetic, no wrap rules. It costs about 128 flip-flops per book stage that holds a
  record.
- Low 32 bits, rebuilt from T2 at the output. Saves flip-flops and adds a 64-bit
  subtract to the output path and a 34 s wrap limit. Rejected for simplicity; revisit only
  if utilization or timing measures a problem.

## Decision

- `rtl/meas/ts_counter`: a 64-bit free-running counter on the core clock, reset at the core's reset.
- T0: `sfd_o` through a toggle synchronizer (`rtl/lib/cdc_*`), the counter captured on
  the synchronized edge, then the two queues above. The synchronizer's constant delay is
  calibrated and documented in `docs/latency.md`. Precision ±40 ns (one nibble clock) plus
  ±1 core cycle.
- Queue overflow, underflow (a status with no queued T0) and a first payload byte with no
  T0 are counted in new stats counters. When that happens, T0 is reported as 0 and the
  update carries no valid T0. It is never silently wrong.
- T1: the counter on the cycle `itch_decoder`'s message stage registers the last byte
  (its existing `t1_o`), stored with the record.
- T2: the counter on the first cycle the book's BBO record is valid (output mode 0), or
  the decoder's record is valid (mode 1).
- The test bench checks every T0/T1/T2 against its own cycle count of the same events.

## Consequences

- `docs/latency.md` (new) holds the cycle budget from T1 to T2 and the T0 calibration.
- Stats grow by the T0 counters (`docs/protocol.md` updated with the RTL).
- The book's per-record sideband grows from 1 to 129 bits. Utilization and timing are
  measured after the change; the 4 ps margin is the risk.

## Measured evidence

[TBD] Phase E: the simulation checks, utilization and timing, then the board's
distributions (`data/results/`).
