# 0007: Output framing, decode trace and control path

**Status: Accepted 2026-11-07** (choices confirmed by the user). Wire formats in
`docs/protocol.md`; golden encoder `model/protocol.py`.

## Context

The top level must send every BBO update with its timestamps to the host (§6.6), let the
host check the hardware decoder against the golden parser on a full day (Phase C DoD),
and take run-time settings: symbol, price-window base (ADR 0002), feed group/port and the
locate fallback (ADR 0004). The output shares a 100 Mb/s link with nothing else, but a
BBO record is ~64 bytes and a frame costs 66 bytes of overhead (preamble, headers, FCS,
gap), so one record per frame tops out near 96k updates/s, below what a line-rate feed
can cause.

## Options considered

**Framing**

| Option | Notes |
|---|---|
| **Batch, flush on idle**: a datagram carries every record waiting when it starts (≤ 22) | 1 record per datagram at light load (no added delay), batching under load. No timer. Needs a `count` known at the start: snapshot the FIFO level. |
| One record per datagram | Simplest; TX falls behind at high BBO rates and stalls the book. |
| Batch with a timeout | Better packing, but adds up to the timeout to every update's delivery. No benefit over flush-on-idle here. |

**Phase C full-day decode check**

| Option | Notes |
|---|---|
| **Trace mode**: decoded records instead of BBOs, same framing | Exact per-record diff on the host; a mismatch is located. A second record layout in the builder. |
| CRC-32 digest of decoded records in the stats packet | Tiny, but a mismatch only says "something differs". |
| Rely on the Phase D BBO check | Weaker evidence; would reword the DoD. |

**Control**

| Option | Notes |
|---|---|
| **UDP config packet** to the FPGA's unicast IP, acknowledged | One cable, scriptable; the output destination is learned from the config's source. Needs a static ARP entry on the host (the FPGA does not answer ARP). |
| UART | Second process during runs; the HUPCL reset trap (§12). |
| Build-time constants | Smallest; rebuild per day; contradicts ADR 0002's host-configured base. |

## Decision

Batch with flush-on-idle, a trace mode selected by the config, and a UDP config packet.
A second `udp_rx` instance on the same `eth_rx` stream filters the control port, so
`fpga-eth-core` is used unchanged. Every packet has a 12-byte header with a datagram
sequence number, so lost output datagrams are visible. A config resets the pipeline and
every counter (except the control counters) and is acknowledged once the book's RAMs are
clear, so each run starts from a known state without reprogramming.

The output path is backpressured end to end, so records are never dropped after they are
produced: when TX is the bottleneck, loss happens only at the MII input FIFO, where it is
counted.

## Consequences

- Host tools must add a static neighbour entry for the FPGA and wait for the ack before
  replaying.
- In trace mode the book is idle; a full-day run needs one pass per mode.
- T0/T1/T2 fields exist from version 1 and read 0 until Phase E, so the format does not
  change then.
- Stats are 40 u64 counters snapshotted on one cycle: ~2,560 FFs, cheap on this device.

## Measured evidence

[TBD]: the top-level simulation (frames in → datagrams out, byte-compared with
`model/protocol.py`) and the hardware runs.
