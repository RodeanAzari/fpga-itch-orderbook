# 0014: On-chip injector for the pipeline-capacity measurement

**Status: Accepted 2026-11-12** (user approved the design as proposed). Buildplan §6.7, Phase E DoD ("injector: pipeline capacity
in messages/s at `DATA_W` = 8 and 64").

## Context

The 100 Mb/s link caps the feed far below what the decoder and book can take: at line
rate a byte arrives every 10 core cycles, and the book is idle most of the time. To
measure the pipeline's own capacity, stored MoldUDP64 payloads must enter the feed
decoder at core-clock rate, bypassing the MAC. The book's BBOs then come out faster than
the 100 Mb/s TX can carry them, so the measurement cannot go through the normal output
either. And a capacity number is worthless unless the pipeline was still right at that
rate.

Budget: 16 BRAM36 free on the full chip (119 / 135 used).

## Options considered

| Question | Options | Choice |
|---|---|---|
| What is stored | (a) MoldUDP64 payloads from the committed AAPL fixture (`data/samples/2019-10-18_AAPL_head50k.itch`), packetized by `model.moldudp64.packetize`; (b) the seeded adversarial generator | **(a)**: real messages, real mix. The generator is a verification tool; its mix (noise, unknown refs, far prices) is not a market's. |
| How it gets on chip | (a) BRAM initialized at synthesis (`$readmemh`, file built from the model); (b) loaded over the control port | **(a)**: no new protocol for loading, and the image is fixed per bitstream (reproducible). Changing it needs a rebuild, acceptable for one measurement. |
| Size | 32 K × 9 bits (byte + payload-last), 8 BRAM36 | Two header words hold the image length, so ~32 KB of packets: ~900 messages. Enough for a steady rate (tens of thousands of cycles). |
| Start | (a) a new config `mode` 2 = inject; (b) a separate control packet type | **(a)**: the config sequence already resets the book and the feed. In mode 2 the injector replays its image once, from the first cycle of RUN. The network feed is blocked (`pkt_gate`) meanwhile. |
| Where BBOs go | (a) through `out_builder` to TX; (b) into an on-chip meter that never stalls | **(b)**: (a) would measure the TX. The meter takes every BBO record (times zeroed) on the cycle it is valid and folds it into a running digest. |
| Proving correctness at speed | (a) BBO count only; (b) count + 64-bit digest of the record stream, computed identically by the model | **(b)**: `digest = rotl(digest, 1) ^ (XOR of the record's eight 64-bit words)`, per record in order. Order-sensitive; not cryptographic, a cross-check that every record, its fields and order match the model's. |
| What "cycles" spans | (a) the injector's first to last byte; (b) first byte to the last BBO into the meter | **(b)**, and the image ends on a message that changes the BBO (the builder trims it so), so the span covers every message's full processing. |

Reported through three new stats counters (end of the stats table, after
`cut_through_bad`): `inj_cycles`, `inj_bbos`, `inj_digest`. The host computes
messages/s = messages in the image × 125 M / `inj_cycles` and checks `inj_bbos` and
`inj_digest` against the model.

## Decision

As chosen above: `rtl/meas/injector.sv` (ROM source, AXI-Stream, `DATA_W` 8 first) muxed in
front of `feed_frontend`, `rtl/meas/inj_meter.sv` (sink, digest, cycle span), config mode 2.
Golden model: `model/injector.py` (image builder, digest, expected counters). The 64-bit
path (ADR 0005 aligner) reuses the same image and meter.

## Consequences

- Mode 2 is a measurement mode: its BBOs are not sent. Modes 0 and 1 are unchanged.
- 8 more BRAM36 (119 → 127 of 135).
- The number is for this image's message mix (the first ~900 AAPL messages of 2019-10-18,
  pre-market: mostly Adds and Deletes); the result sidecar states the mix.

## Measured evidence

- Simulation (test_core `inject_capacity`, both eth_rx builds): 1,043 messages / 31,679
  bytes → 248 BBOs, digest = model, 31,703 cycles (`docs/log.md` 2026-11-12).
- Board, 2026-11-13 (`data/results/2026-11-13_injector_board.md`): the same 31,703 cycles,
  248 BBOs and the model's digest for each build (cut-through once, store-and-forward three
  times, identical). 4.11 M messages/s at `DATA_W` 8, input-bound.
