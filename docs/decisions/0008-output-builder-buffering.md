# 0008: Output builder buffering and datagram sequencing

**Status: Accepted 2026-11-08.** Implements ADR 0007's batching in `rtl/out/out_builder.sv`.

## Context

ADR 0007 fixes the wire format: a datagram's `count` is the number of records waiting when
it starts (flush on idle), records are never dropped after they are produced, and a config
ack (pkt_seq 0) starts each run. `udp_tx` (fpga-eth-core) takes one byte per beat and
buffers a whole payload. The record buffer must absorb TX backpressure, and BRAM is short:
the book takes 104 of 135 BRAM36.

## Options considered

| Option | Cost | Notes |
|---|---|---|
| **Byte FIFO + counter of complete records** | 1 BRAM18 (2,048 bytes = 32 BBO / 51 trace records), one 512-bit shift register | Record copied in at 1 byte/cycle (64 cycles per BBO, far below the feed's ≥ 160 cycles per message at line rate). `count` = complete records, so a datagram never waits on a half-written record. |
| Wide FIFO, one record per entry (512 bits) | ~8 BRAM36 at depth 32, or ~350 LUTs of distributed RAM per 32 entries | Serializer on the read side instead. No throughput advantage: TX drains 1 byte per 8 cycles. |
| Formatted header + records written together, length known up front | Same FIFO | Needs `count` before the records exist: contradicts flush on idle. |

Ack timing:

| Option | Notes |
|---|---|
| **Ack waits for the datagram in flight, then flushes the FIFO (and the writer's half record) and sets pkt_seq to 0** | Never cuts a datagram; no truncated frame on the wire. |
| Reset the builder and udp_tx with the pipeline | Cuts the datagram in flight: udp_tx would append the next payload to it. |

## Decision

Byte FIFO (`rtl/lib/sync_fifo.sv`, FWFT on `sdp_ram`) with a counter of complete records.
Priority when idle: ack, then stats, then records. The ack flushes at a datagram boundary,
latches the new mode (records queued from the old run leave in the old mode, because the
control block sets the mode thousands of cycles before the book's RAMs are clear), and arms
the builder; nothing is sent before it. Stats requests arriving while one is pending merge
(they come 1 s apart). The stats vector is copied to a 2,560-bit shift register on the
datagram's start cycle, so all counters share one snapshot cycle.

## Consequences

- The record source must stay in reset until `flush_o` (the control block's sequencing).
- Record lengths 40 and 64 are hard-wired into a shift-add for the body length (an
  elaboration check fails if they change).
- ~2,600 of the module's 3,513 FFs are the stats snapshot; cheap on this device.

## Measured evidence

- `make sim` (test_out, test_sync_fifo): exact record streams in both modes under random
  backpressure, `count` = 1 at light load and = MAX_COUNT (22 / 36) under saturation,
  atomic stats snapshots, ack in mid-traffic and mid-record. 11 injected RTL bugs: the 9
  that change behaviour are all caught; the 2 missed are equivalent (redundant guards).
- OOC, xc7a100t at 8 ns (`docs/reports/ooc_out_builder/`): WNS +0.998 ns, 3,245 LUT,
  3,513 FF, 0.5 BRAM36, 0 DSP. A `count × length` multiply had mapped to a DSP48 and was
  the critical path (WNS +0.080 ns); shifts fixed it.
