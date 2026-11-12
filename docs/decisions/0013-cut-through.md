# 0013: Cut-through receive as a build option

**Status: Accepted 2026-11-11** (user chose option A as proposed). Buildplan §6.2, Phase E DoD
("cut-through mode added; latency compared with store-and-forward").

## Context

eth_rx (fpga-eth-core) is store-and-forward: `frame_buffer` holds a frame until its FCS
is checked, then releases it at one byte per cycle. The feed therefore starts decoding a
frame only after its last byte is on the wire: T2−T0 on the board is p50 127 µs, almost all
of it serialization of the whole frame (80 ns/byte) plus the wait for the FCS
(`data/results/2026-11-11_record_fifo_board.md`). Cut-through would let the decoder see each
message as its bytes arrive (one byte per 10 core cycles), so a message near the start of a
1,400-byte frame is acted on ~110 µs earlier.

The cost: a frame's verdict (FCS, PHY error, overflow) is known only at its end, after its
messages have already moved the book. The spec (§6.2) accepts this: no rollback; a later
bad frame is counted and marks the book stale.

## Options considered

| Option | Where | Notes |
|---|---|---|
| **A. `CUT_THROUGH` parameter in eth_rx: bypass `frame_buffer`; every frame is output as it arrives (FCS stripped by eth_fcs_check's 4-byte window); the per-frame status pulses keep their meaning and order** | fpga-eth-core (new tag) | Payload-agnostic, a build option; default 0 keeps today's behaviour for P3/P4. Consumers in cut-through must act on the status pulses themselves. |
| B. Speculative path inside OrderBook, tapping eth_rx's internal stream | OrderBook | Breaks the reuse contract (reaches below eth_rx's interface). Rejected. |
| C. Carry the verdict on `tuser` through udp_rx to the feed | fpga-eth-core udp_rx | The payload's `tlast` comes before the frame's end when the frame is padded, so the verdict arrives after the payload has ended. Needs udp_rx to hold its `tlast`: delays every payload's end by the padding. More change than A for the same information. |

Book-side handling of a bad frame (with A):

| Option | Notes |
|---|---|
| **Stale when a frame udp_rx delivered to the feed later gets a bad status** (pair udp_rx's per-frame status with eth_rx's, both one per frame, in order) | Only frames that touched the book make it stale. New counter `cut_through_bad`. |
| Stale on any bad frame | Simpler, but a bad frame addressed elsewhere would mark the book stale for nothing. |

T0 (ADR 0011) in cut-through: frames leave eth_rx before their status, so `t0_track` takes
T0 from PEND at frame start (not from DONE after status). Frames lost whole to the async
FIFO overflow never start; their PEND entries must be retired by the ovf status without
misaligning a started frame. To be designed against `model/timestamps.py` first (golden
model before RTL).

## Decision

Option A in fpga-eth-core (`eth-core-v0.4`, interface addition only: default unchanged),
stale-on-delivered-bad-frame in OrderBook, `t0_track` cut-through mode, and a
`CUT_THROUGH` build flag for `arty_top` (`syn/build_top.tcl` argument). Measure both
builds on the same day file: T2−T1 (expected unchanged) and T2−T0 (expected to drop by
roughly the frame's serialization after each message).

## Consequences

- BBOs produced from a frame that later fails its FCS go out unflagged; the BBOs after it
  are stale (sticky, ADR 0003). The host can tell which by the `cut_through_bad` counter.
- On a clean link (no FCS errors in any run so far) the two builds produce the same records.
- Hardware FCS-error testing needs a NIC that really sends bad FCS (`SO_NOFCS`); otherwise
  the bad-frame path is covered in simulation only (CLAUDE.md measurement rules).

## Measured evidence

[TBD]
