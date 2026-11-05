# 0003: MoldUDP64 gap, duplicate and stale semantics

**Status: Accepted 2026-11-05.** Encoded in `model/moldudp64.py` (`SeqTracker`), and the RTL
must match it.

## Context

Retransmission and snapshot recovery are out of scope (§10). So a missing message can never
be recovered, and a book that missed one can never be proven correct again. §5.2 fixes the
basic rules: `expected = seq + count`, higher is a gap, lower is a duplicate, and a session
change resets tracking. It leaves four questions open:
- When does the stale flag clear?
- What happens to a packet that overlaps old and new messages?
- How are malformed packets handled?
- Does the very first packet count as a session change?

## Options considered

| Question | Options | Notes |
|---|---|---|
| Stale clears | (a) never, until reset; (b) after N clean packets; (c) at a session change | (b) and (c) would publish a book that is missing orders as if it were good. |
| Partial overlap (`seq < expected < seq + count`) | (a) drop the whole packet; (b) skip the seen prefix, apply the new tail | (b) is what a full MoldUDP64 receiver does, but it needs a per-message skip counter in the RTL. Our replayer only produces overlaps when told to inject them. |
| Malformed packet (message overruns the packet, trailing bytes, short header) | (a) drop the whole packet; (b) deliver the messages before the break, flag the packet | The RTL decodes messages as the frame streams through, so (a) would need a second packet buffer. |
| First packet | Session change or not | Counting it would make every run start stale. |

## Decision

- **Stale is sticky until reset.** Any gap, session change after the first, malformed
  packet or malformed ITCH message sets it. Only a reset clears it.
- **Partial overlap: drop the whole packet and count a duplicate.** `expected` does not
  move, so the next packet arrives as a gap, is counted, and marks the book stale. Its
  new messages are lost, but never silently.
- **Malformed packet: deliver the messages before the break,** count it, mark stale, and
  advance `expected` by the header's count. The messages it claimed but didn't carry are
  then accounted for as lost, rather than reported again as a gap.
- **The first packet sets the session** without counting as a change. Tracking always
  starts at sequence 1, so a run that misses the start of day is a gap and is stale. That
  is correct, because the `R` messages that teach the locate are lost with it.
- **Heartbeats and end-of-session** go through the same sequence check, so a heartbeat
  ahead of `expected` is a gap. A heartbeat or end-of-session packet with extra bytes is
  malformed.

## Consequences

- One lost packet makes the rest of the run stale. That is the honest result without
  retransmission. The BBO keeps flowing with the stale flag set, so the demo still
  shows a live book.
- The counters tell apart how the book went stale: `gaps`/`gap_messages`, `duplicates`,
  `session_changes`, `malformed_packets`, `malformed_messages`.
- The RTL's sequence logic needs one comparator against `expected`, and no buffer or skip
  counter.

## Measured evidence

`model/tests/test_moldudp64.py`, 14 tests:
- one for each rule above, including a partial overlap that surfaces as a 10-message gap
- a missed start of day counted as a 49-message gap
- a truncated packet that still delivers its first 11 messages

Planted bugs each fail a test: duplicates applied, a gap not marking stale, `expected`
advanced by delivered instead of claimed messages.

The full day replayed through `FeedDecoder` (302,347,067 messages in 6,456,827 packets)
gave 0 gaps, 0 duplicates and 0 malformed packets, and the book was never stale
([`_feed.csv`](../../data/results/2019-10-18_AAPL_feed.csv),
[sidecar](../../data/results/2019-10-18_AAPL_feed.md)).

In RTL (`tb/cocotb/test_feed.py`), directed single-cause tests pin the stale flag
exactly. The mixed adversarial stream could not: once one cause sets the sticky flag,
it hides whether another would have. Planted "gap not stale" and "session change not
stale" bugs passed the adversarial test and fail the directed ones.
