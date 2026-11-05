# 0004: Stock-locate filter population

**Status: Accepted 2026-11-05.** Encoded in `model/moldudp64.py` (`FeedDecoder`), and the RTL
must match it.

## Context

Every ITCH message carries a 2-byte stock locate. Locate codes are assigned per day
(§5.3), so the symbol → locate mapping must come from the day's own `R` (Stock
Directory) messages. These appear only at the start of the day. The filter should drop
other stocks' messages as early as possible, because only one symbol's book is built
(ADR 0001).

## Options considered

| Option | Notes |
|---|---|
| **Learn from `R`**: compare the 8-byte stock field with a configured symbol, latch its locate | Self-contained and correct for any day file, as long as replay starts at the beginning. A missed start of day is already a gap (ADR 0003). |
| Host-configured locate only | Works without `R`, but the host must parse the file first, and a stale locate from another day builds the wrong book silently (§12). |
| Both: learn from `R`, with an optional host override | Covers mid-day starts for debugging, at the cost of one extra register. |

## Decision

Learn from `R`, with an optional host-configured locate as a fallback:
- The symbol is configured (8 ASCII bytes, space padded, as in the `R` message).
- Each `R` whose stock matches latches its locate. A later matching `R` with a different
  locate replaces it and is counted (`locate_changes`). That never happens in a clean
  day, so a non-zero count is worth investigating.
- Until a locate is known, book messages are dropped and counted (`other_locate`), so an
  unknown locate cannot accept anything.
- Only `A F E C X D U` for the matched locate go on to the book. `S` is log-only, and `R`
  is consumed here. Other types are skipped by their length field and counted
  (`unknown_types`).

## Consequences

- The filter costs a 64-bit compare on `R` messages and a 16-bit compare on every message.
- Replay must start at sequence 1. That is already required (§5.3) and is now enforced by
  the gap rule.
- When a locate is configured up front, learning still runs and `locate_changes` reports
  any disagreement.

## Measured evidence

`model/tests/test_moldudp64.py`:
- On a 20,000-message adversarial stream, the decoder's output equals an independent
  decode-then-filter path.
- Messages before the `R` and other stocks' messages are filtered, and a second `R` with
  a new locate is counted.
- The configured fallback accepts without any `R`.

A planted "first `R` wins" bug fails. On the real day, AAPL is locate 14
([summary](../../data/results/2019-10-18_AAPL_summary.csv)).
