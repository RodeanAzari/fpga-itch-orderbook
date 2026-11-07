# 2026-11-06: book_engine stress and full-day simulation (`_sim_full_book.csv`)

| | |
|---|---|
| Commit | RTL at `6d58b1f` (`rtl/book/`, before the behaviour-neutral `prio_enc` refactor in `1118078`, which `make sim` re-checked); testbench `tb/cocotb/test_book_full.py` as committed with this file |
| Input | Generator (`model/generator.py`) and `test_book.wide`, `SEED=20261106`; AAPL extract of `S101819-v50.txt.gz` (`data/MANIFEST.md`), extract SHA-256 `dcca1ce4…40ca8a76` |
| Tools | Verilator 5.050 (local build, `docs/toolchain.md`), cocotb 2.1.0, Python 3.14.4 |
| Run time | 1 h 03 min wall in total (random 2,899 s, wide 218 s, full day 677 s); 638 MB peak RSS |

## Command

```
make sim-full SIM_FULL_CSV=data/results/2026-11-06_sim_full_book.csv
```

## Interpretation

Every BBO update `book_engine` published was compared, in order and field by field
(bid/ask price and size, out-of-window flag, cause seq and ts), with `BoundedBook` at the
real geometry (2 × 8,192 × 4 table, ±2,048-tick window). Any mismatch, missing or extra
update fails the test before its row is written; all three rows have 0 mismatches, and
every counter equals the model's.

- **`real_full_day`: the whole 2019-10-18 AAPL day through the RTL.** 1,658,958 book
  messages, 831,842 BBO updates, identical to the golden model (`_bounded.csv` has the
  same 831,842). `bbo_oow` = 4 as ADR 0002 predicts, `price_oow` = 16,099, every other
  counter 0, never stale. This is the simulation counterpart of the Phase D hardware
  full-day check, which still needs in-order delivery from the host.
- **`wide_200k`:** 204,162 messages spread over the whole window; the model counts 69,739
  next-best searches, up to 2,327 ticks, 42,226 of them across more than 64 ticks.
  103,421 BBO updates. (`over_reduce` is the stimulus's way of removing orders; stale = 1
  follows from it, as ADR 0006 defines.)
- **`random_1m`: weaker than its name.** It drove 970,835 book records (the generator's
  1,000,000 messages include non-book noise), so on its own it is below the Phase D "1M+
  random events" bar. At this geometry the generator's live orders grow until the table
  is full: 290,065 `table_overflow`, 268,092 `unknown_ref`, stale from then on, and only
  25,201 BBO updates. It therefore mostly exercises the overflow and unknown-ref paths;
  they matched the model exactly, but it is not a long healthy-book run.

Together the random runs total 1,174,997 records with 0 mismatches. A clean "1M+" for the
Phase D checklist needs a generator setting that keeps the table below capacity and at
least 1,000,000 book records in one run.
