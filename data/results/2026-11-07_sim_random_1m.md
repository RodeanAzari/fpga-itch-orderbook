# 2026-11-07: book_engine on 1M+ random book records below table capacity (`_sim_random_1m.csv`)

| | |
|---|---|
| Commit | `4913d82` (RTL unchanged since `1118078`) |
| Input | `model/generator.py` with `Config(max_live=30_000)`, `SEED=20261106`, test `random_1m` in `tb/cocotb/test_book_full.py` (1,040,000 generated messages + directed limit blocks) |
| Tools | Verilator 5.050, cocotb 2.1.0, Python 3.14.4 |
| Run time | 21 min 42 s wall; 390 MB peak RSS |

## Command

```
COCOTB_TEST_FILTER=random_1m make sim-full SIM_FULL_CSV=data/results/2026-11-07_sim_random_1m.csv
```

## Interpretation

**1,009,900 book records through `book_engine` at the real geometry (2 × 8,192 × 4
table, ±2,048-tick window), 0 mismatches.** Every BBO update (42,823) was compared field
by field with `BoundedBook`, and every counter equals the model's. This closes the Phase D
item "1M+ random events in sim with zero scoreboard mismatches". The 2026-11-06 run
reached only 970,835 records and saturated the table.

- Live orders are capped at 30,000 (the real day peaks at 27,814), so the table runs at
  ≤ 46 % load. `table_overflow` = 59 comes from the generator's deliberately colliding
  refs plus rare bucket-pair saturation under ~1M inserts of churn. A 200k-message model
  run without colliding refs still overflows twice at this load. This is a sizing data
  point for the "busier second day" check: the real day (27,814 peak) has 0.
- `unknown_ref` = 7,976: the generator's deliberate 1 % plus cancels of overflowed orders.
  The book is therefore stale from early in the run, as designed.
- **Caveat:** 42,791 of the 42,823 BBO updates are flagged out of window. Orders priced
  past the window's near edge stay live, so most BBOs differ from the unbounded book. The
  run is heavy on flagged BBOs. The real-day run (`2026-11-06_sim_full_book`, 831,842
  updates, 4 flagged) covers the typical book.
