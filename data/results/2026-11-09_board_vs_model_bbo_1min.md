# Board vs golden model, last BBO per minute (2026-11-09)

CSV: `2026-11-09_board_vs_model_bbo_1min.csv`; plot
`docs/media/2026-11-09_phase-d_board-vs-model-bbo-day.png` (`make plots`).

| | |
|---|---|
| Commit | the commit that adds this file (`analysis/board_bbo.py`) |
| Input | run `day500c` of `2026-11-09_full_day_board.md` (capture log `data/runs/day500c.log`, SHA-256 prefix `b184e8fd0d7da6a8`; feed `data/runs/slice0.pkts` = `replay --out` of the whole AAPL extract) |
| Command | `python -m analysis.board_bbo --log data/runs/day500c.log --packets data/runs/slice0.pkts --out data/results/2026-11-09_board_vs_model_bbo_1min.csv` |

## Interpretation

650 minutes with a BBO; in all 650 the board's last BBO (price and size, both sides) equals
the bounded golden model's (`board_bbo` prints "0 differ"). The plot leaves out one minute
with an empty side. This is a per-minute view for the eye; the proof is record level:
`compare` finds all 831,842 BBOs identical in that run.
