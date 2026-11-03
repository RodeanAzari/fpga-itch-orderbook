# 0002: Price-window base selection

**Status: Proposed.** The base rule matches CLAUDE.md §6.4. The open question is how to
treat the two measured out-of-window BBOs.

## Context

The price-level arrays cover 4,096 ticks (±$20.48 at 1¢) around a base fixed for the day
(no recentering, §10). §6.4 rules out "first trade" because pre-market orders arrive hours
earlier, and requires `bbo_oow == 0` on real data.

## Options considered

| Option | Notes |
|---|---|
| First trade | Rejected by §6.4: pre-market orders land out of window. |
| Prior day's close, host-configured | Needs an outside data source; can't be checked from the file alone. |
| **Golden model's opening mid** (first two-sided, uncrossed BBO at or after 09:30), host-configured | Computed from the same file; reproducible. |
| Widen to 16,384 ticks | Contains both outliers below, but costs 32 BRAM36 for levels, which no longer fits with ADR 0001's 96. |

## Decision (proposed)

The host configures the base as the golden model's opening mid: for 2019-10-18 AAPL,
23,455 ticks ($234.55), giving a window of $214.07 to $255.03. Keep 4,096 ticks.

## Consequences

The window contained 831,837 of 831,839 two-sided BBOs (99.9998 %). The two exceptions:

| Time | Best bid | Best ask | Why |
|---|---|---|---|
| 04:00:11 | $175.12 × 29 | $236.90 × 100 | First seconds of pre-market, nearly empty book |
| 20:00:00 | $234.00 × 50 | $260.00 × 6,000 | Last instant of after-hours |

So `bbo_oow == 0` over the whole file is unreachable with a 4,096-tick window.

**Proposal:** each such update carries an out-of-window flag (counted in `bbo_oow`) rather
than being hidden. The Phase D invariant becomes "`bbo_oow` equals the golden model's count
(2 on this day), each one flagged". Mismatches are still scored against the bounded model,
so "zero mismatches" stays achievable.

`price_oow` will be about 16,099 adds (1.85 %), including 9 sub-penny stub quotes.

## Measured evidence

`data/results/2019-10-18_AAPL_summary.csv` (`window_base_ticks`, `bbo_two_sided_in_window`,
`adds_out_of_window`), and the out-of-window events printed by `analysis/sizing.py`.
