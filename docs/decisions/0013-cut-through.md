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

T0 (ADR 0011) in cut-through: a frame leaves eth_rx before its status, so `t0_track`
takes its T0 straight from PEND at frame start (DONE is unused). A status then discards
only the PEND entries of frames that never started. "Started" cannot be inferred from
timing at t0_track: eth_fcs_check emits nothing for a frame of 4 bytes or fewer
(including a lone overflow abort beat), and a 5-byte frame's first beat can leave after
its status. So eth_rx adds one output, `emitted_o`, valid with every status pulse: this
frame put at least one beat on `m_*` (in store-and-forward it equals `frame_ok_o`).

| T0 match in cut-through | Notes |
|---|---|
| **`emitted_o` qualifier on eth_rx's status; a frame start pops PEND; a status pops its frames minus `emitted`; an emitted status before its frame's start moves the head to a one-entry hold that the start takes** | Exact for every frame length. Order holds because a frame's first beat cannot leave fcs_check until the previous frame's last beat has (one-beat pipeline register), so every earlier status has been applied. "Before its start" = no start since the previous status (a start from hold does not count: that frame was already judged). On a cycle with both, they are the same frame: the start takes the head, the status the entries after it. |
| Hold the first 60 bytes so runts never leave | Adds ~5 µs to every frame and still leaves overflow-aborted frames of any length. Rejected. |
| Infer "started" from frame starts alone, without `emitted` | At a status with no start since the last one, the frame may be a 4-byte frame (pop its entry) or a 5-byte frame still to start (keep it): indistinguishable, and either guess shifts every later T0 to another frame. Rejected. |

Golden model: `model/timestamps.py` `T0Track(cut_through=True)`, tested in
`model/tests/test_timestamps.py` against a frame-level schedule (each start must get its
own SFD cycle; 40 random seeds plus the edge cases; 5 injected model bugs all caught).

An overflow abort that ends a started frame counts that frame in `ovf_frames_o`
(rx_ovf_guard counts a frame on its first lost beat, and the abort is the next beat
written), so its status pops `ovf_frames_o − 1`.

Book side in ob_core: eth_rx also marks a bad frame with `m_tuser_o` on its `tlast` beat
in cut-through (as `eth_fcs_check` already does internally; oversize is added in eth_rx).
When that beat is accepted, the feed `udp_rx`'s status for the same frame comes on the
same or the next cycle; if that frame put any payload beat into the feed, the feed is
marked stale (sticky until the next config) and `cut_through_bad` counts it. A payload's
last beat can leave udp_rx after its frame's status (output backpressure); a flag keeps
that beat from counting as payload of the next frame.

Control path: the control udp_rx also sees every frame, and a corrupted config must
never be applied. ob_core sets the control datagram's `tuser` (out_ctrl rejects it,
counted in `ctrl_bad`) unless its frame has already ended good when the datagram's last
beat leaves udp_rx. So in cut-through a control datagram must end its frame (no
Ethernet padding: payload ≥ 18 bytes; a config is 32). Rejected alternative: a
store-and-forward buffer on the control path only (BRAM and a second frame path, for
a packet sent once per run).

## Decision

Option A in fpga-eth-core (`eth-core-v0.4`, interface additions only: `CUT_THROUGH`,
`emitted_o`, `m_tuser_o`; default unchanged),
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

- Board, full AAPL day 2019-10-18 at 500 pps, both builds from `2325efd`
  (`data/results/2026-11-13_ct_vs_sf_board.md`): 831,842 / 831,842 BBOs identical to the
  model in each build and to each other but for the times and the `cut_through` flag;
  `cut_through_bad` 0, no FCS errors on the link. T2−T1 unchanged (p50 136 ns, max 232 ns).
  T2−T0 p50 126.7 → 64.2 µs, max 133.8 → 121.7 µs; paired per record, cut-through is
  7.2–115.1 µs earlier, never later.
- Output: cut-through sent 3.5x the datagrams (1.08 BBOs each vs 3.8), since out_builder
  flushes on idle and BBOs now arrive at wire speed.
- Timing (full chip with the injector, 125 MHz): WNS +0.172 ns cut-through, +0.048 ns
  store-and-forward (`docs/reports/arty_top_ct/`, `docs/reports/arty_top/`). Closing it
  took the registered deframer sum, a registered sync_fifo full and registered t0_track
  PEND occupancy (`docs/log.md` 2026-11-13).
- The bad-frame path (`cut_through_bad`) is covered in simulation only (`ct_bad_frames`).
