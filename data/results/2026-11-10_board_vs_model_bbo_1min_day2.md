# Day 2 (2025-12-12) board vs golden model, last BBO per minute

| | |
|---|---|
| Commit | `analysis/board_bbo.py` unchanged since `e9ba322`; plot in `analysis/plots.py` as committed with this file |
| Input | run `d2_16way` of `2026-11-10_full_day_board_16way.md` (capture log `data/runs/d2_16way.log`, SHA-256 prefix `3bbc0d2ff710ef89`; feed `data/runs/2025-12-12_AAPL_slice0.pkts` = `replay --out` of the whole AAPL extract, session `20251212AA`) |
| Command | `python -m analysis.board_bbo --log data/runs/d2_16way.log --packets data/runs/2025-12-12_AAPL_slice0.pkts --out data/results/2026-11-10_board_vs_model_bbo_1min_day2.csv`, then `make plots` → `docs/media/2026-11-10_phase-d_board-vs-model-bbo-day2.png` |
| Tools | Python 3.14.4, matplotlib 3.11.2 |

## Interpretation

850 minutes in which either side published a BBO; in none of them does the board's last
BBO differ from the model's. The plot leaves out minutes with an empty side, which leaves
849. This is a per-minute view of the record-level result (916,429 / 916,429 identical,
`analysis.compare`), drawn to show the whole day at once.
