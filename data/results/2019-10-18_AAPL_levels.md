# 2019-10-18 AAPL: price-level sizing (`_levels.csv`)

| | |
|---|---|
| Commit | `ab59b26` (`analysis/levels.py`, model `model/bounded.py` unchanged since `v0.1-model`) |
| Input | AAPL extract of `S101819-v50.txt.gz` (SHA-256 `390acb4d…5d8da4`, `data/MANIFEST.md`); extract SHA-256 `dcca1ce4…40ca8a76` |
| Tools | Python 3.14.4 (WSL Ubuntu); full pins in `requirements.txt` |
| Run time | 48 s wall |

## Command

```
.venv/bin/python -m analysis.itch_extract /mnt/d/itch/S101819-v50.txt.gz AAPL /mnt/d/itch/derived/2019-10-18_AAPL.itch
.venv/bin/python -m analysis.levels /mnt/d/itch/derived/2019-10-18_AAPL.itch 23455 data/results/2019-10-18_AAPL
```

## Interpretation

The bounded book (ADR 0001 table, ADR 0002 window at base 23,455 ticks ± 2,048) replayed
the whole day. The numbers cover in-window levels only, which is what the RTL arrays hold.

- **Largest aggregate on one level: 294,065 shares (19 bits), on the ask side.** The level
  word is 36 bits anyway: the table holds at most 65,536 orders of at most 2^20 − 1 shares,
  so no level can reach 2^36. That bound, not this measurement, sets the width (ADR 0006).
- **Depth: up to 922 live bid levels and 843 ask levels at once.** A top-K register array of
  any feasible K would lose most of the book.
- **The best level emptied with other levels still live 47,197 times (bid) and 49,571
  times (ask)**: about 5.8 % of the day's ~1.66 M book messages. The next-best search is a
  common path. Each side emptied completely once.
- **Distance from an emptied best to the next best: at most 188 ticks (bid), 108 (ask);
  more than 64 ticks once per side.** A linear scan would have handled this day in a few
  hundred cycles, but its worst case is the full 4,096-level window. The bitmap search is
  bounded whatever the data.

A separate one-off count over the same extract found 0 in-window sub-penny prices
(Add/Replace with `price % 100 != 0`) and 10 out-of-window ones; ADR 0006 records the rule.
