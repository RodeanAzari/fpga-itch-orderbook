# 0016: The feed stale flag rides in-band with each message

**Status: Accepted 2026-11-14** (user chose this option over relaxing the test).
Amends ADR 0003 (when a record is stale) and ADR 0013 (when a bad cut-through frame's
stale takes effect). docs/protocol.md `stale` flag.

## Context

docs/protocol.md promises that the feed's stale bit "travels with the record", so a gap
found while an earlier message is still being processed does not flag that earlier
update. The RTL did not keep that promise everywhere. `ob_core` passed the *live* feed
stale (`feed_frontend.stale_o || ct_stale_q`) into the book's per-record sideband, so it
was sampled when the record **entered the book**, not when it was decoded.

The failure, found in `test_core ct_bad_frames` at DATA_W 8 in cut-through
(SEED=1623422250, on main): the FCS verdict for a bad frame comes 1 cycle after its last
byte leaves `eth_rx`. The bad packet's last message (seq 309) was still on its way to
the book, so its BBO came out stale, while ADR 0013 says a bad frame's own BBOs go out
unflagged. The same live sampling can flag the last record before a gap packet whenever
that record waits at the book input while the deframer reads the next header. That is
more likely at DATA_W 64 (a header is 3 beats). No test had shown it.

The model had a second, opposite ordering problem. `SeqTracker.feed` reads the whole
packet before its messages are decoded, so a packet with a malformed tail (truncated, or
bytes after `count` messages) marked its own earlier, intact messages stale. Streaming
RTL only learns of the tail at tlast, after those messages are out.

## Options considered

| Option | For | Against |
|---|---|---|
| **A. In-band stale.** Each message carries the stale state as of its delivery, in stream order: the deframer stamps each output beat; the aligner and decoder carry it; the record is stamped at the decoder | Exact by construction (one stream, one order); deterministic, so the model predicts every flag; the promise in docs/protocol.md becomes true | Touches the verified deframer (both widths), aligner and decoder, and their tests |
| B. Sample the live stale at the decoder output instead of the book input | Small change in `ob_core` | Still racy: at 64 the deframer can finish the next header while a message sits in the aligner or decoder; and the cut-through verdict still beats the bad packet's last record |
| C. Relax the test: accept either flag on the bad packet's own BBOs | No RTL change | Leaves the gap race in place and unchecked; the board-vs-model comparison could then mismatch on a stale bit |

## Decision

Option A.
- **Deframer** (both widths): new input `s_stale_i` (a packet-level external stale,
  sampled at the packet's first beat, as T0 is), new per-beat output `m_stale_o` =
  `stale_o` (sequence/header verdicts so far) OR an empty message seen so far OR the
  packet's `s_stale_i`. A packet's malformed tail is found after its messages, so it
  flags only later messages.
- **Aligner**: carries `m_stale_o` like `m_t0_o`.
- **Decoder**: takes the bit at the message's last beat; the record's `m_stale_o` = that
  bit OR a malformed message decoded earlier (sticky).
- **ob_core**: the book's per-record stale is the record's `m_stale_o`. The cut-through
  stale (`ct_stale_q`) enters at `s_stale_i`, so it flags every packet that *starts*
  after the verdict. The bad frame's own messages stay unflagged (ADR 0013), whatever
  the timing.
- **Model**: `SeqTracker.msg_stale`, the stale state when the packet's messages are
  delivered (after the header, before any tail malformation); `Record.stale` uses it.
  The tracker's end state (`stale`) is unchanged.

## Consequences

- Records differ from before only (a) after a malformed packet tail, where the earlier
  messages of that packet are no longer stale, and (b) in the timing races above.
  Neither occurs in the day files (clean feed), so the board results stand.
- The `stale` stats counter (global, sticky) is unchanged.
- One more bit through the feed pipeline; no timing concern expected (registers only).

## Measured evidence

2026-11-14, simulation (no board run yet; the day files have no stale events, so the
board comparison is unaffected either way).
- Model: `test_stale_is_in_stream_order_around_a_malformed_tail` (trailing byte and
  overrunning prefix: own records unflagged, next packet's stale; a gap flags its own
  packet). It fails on the previous model rule.
- `test_deframer` at 8 and 64: every message's `m_stale_o` vs the model's
  `msg_stale`, OR an earlier empty message, OR the packet's `s_stale_i` (driven with a
  new random value every cycle, so only first-beat sampling passes). Two directed
  malformed-tail tests with the external bit held at 0. Bins: clean_stream covers model-0
  with ext 0 and 1, adversarial covers model-1 with ext 0 and 1.
- `test_aligner`: stale carried per message, including the flush beat.
- `test_feed` at 8 and 64: each record's stale vs `Record.stale` OR its packet's
  `s_stale_i`; new `stale_per_packet` (1-3 messages per packet, external bit 1 half the
  time, output ready 20%).
- `test_core`: the failing seed (SEED=1623422250, `ct_bad_frames`) passes at DATA_W 8 and
  64; all 4 sets (8/64 x store-and-forward/cut-through) pass.
- Bug injection: deframer (external bit dropped or sampled live, at 8 and 64; stale_o
  dropped; tail flagging its own message), aligner (flush beat's bit, bit dropped), decoder
  (sticky malformed dropped): every one fails a test. Two do not, by design: sampling the
  decoder input's bit at stage 2 instead of at the last beat is equivalent (stage 2 always
  runs the cycle after a multi-beat message's last beat, while the next packet's header
  still occupies the deframer), and the `malformed_o` term covers a record on the cycle
  after a malformed message, unreachable with ITCH's >= 19-byte book messages.
- Out-of-context `feed_frontend` at 125 MHz: DATA_W 64 WNS +0.556 ns, 1,315 LUT, 2,527 FF
  (was +0.460 ns, 1,249, 2,518); DATA_W 8 WNS +1.175 ns, 639 LUT, 1,863 FF (was
  +1.577 ns, 633, 1,857). `docs/reports/ooc_feed*/`.
