# 2026-11-10: book_engine stress and both full days at the 16-way table (`_sim_full_book.csv`)

| | |
|---|---|
| Commit | RTL and model at `15b3824` (ADR 0010: 2 × 2,048 × 16); `tb/cocotb/test_book_full.py` as committed in `8318c33` (the run used that exact file, uncommitted at the time) |
| Input | Generator (`model/generator.py`) and `test_book.wide`, `SEED=20261106`; AAPL extracts of `S101819-v50.txt.gz` (SHA-256 `dcca1ce4…40ca8a76`) and `S121225-v50.txt.gz` (SHA-256 `98ff58f9…ff289b66`), see `data/MANIFEST.md` and `2025-12-12_AAPL.md` |
| Tools | Verilator 5.050, cocotb 2.1.0, Python 3.14.4 |
| Run time | 5 h 54 min wall in total, host load 10 to 40 from other sessions: random 1,726 s, wide 579 s, day 1 2,007 s, day 2 5,209 s |

## Commands

```
make sim-full SIM_FULL_CSV=build/sim_full_16way.csv ITCH_EXTRACT=$HOME/itch-cache/2019-10-18_AAPL.itch
COCOTB_TEST_FILTER=real_full_day make sim-full SIM_FULL_CSV=build/sim_full_16way.csv \
    ITCH_EXTRACT=$HOME/itch-cache/2025-12-12_AAPL.itch
```

## Interpretation

Every BBO update `book_engine` published was compared, in order and field by field, with
`BoundedBook` at the new geometry (2 × 2,048 × 16, ±2,048 ticks). Any mismatch, missing or
extra update fails the test before its row is written. All four rows have 0 mismatches.

- **Day 2 (2025-12-12) through the RTL:** 3,511,772 book messages, 916,429 BBO updates
  identical to the model (= `itch_stats`' `bbo_events`). Table overflow 0, unknown refs 0,
  never stale; `price_oow` 69,799 and `bbo_oow` 3, as `2025-12-12_AAPL_bounded_2x2048x16.csv`
  predicts. On the old 4-way table the same day was stale from pre-market.
- **Day 1 (2019-10-18) on the new table:** 831,842 BBO updates, every counter as on
  2026-11-06 (the old geometry), so the reshape changed no day-1 output.
- **`random_1m`:** 1,009,900 generated messages, 0 mismatches. `table_overflow` is now 0
  (290,065 at 8,192 × 4): the generator aims its collisions at 512 sets per bank, so at 2,048
  sets they don't collide. Overflow handling is still covered every `make sim` at 512 sets
  (`test_order_table` and `test_book` with `SET_BITS=9`, which assert it is reached).
- **`wide_200k`:** same stream and counts as on 2026-11-06, unaffected by the table.
- Day 2 alone takes 87 min of simulation, so `sim-full` is now about 6 h when both days
  run.
