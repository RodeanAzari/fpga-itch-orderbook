# Progress

Current state only. History belongs in `docs/log.md`, full design rationale in `docs/decisions/`.

## Where we are

**Phase A (Python golden model + data): in progress.** No real-data numbers yet.

- Done and tested: ITCH parser, unbounded book, capacity-limited book, seeded generator (`make model-test` passes).
- `analysis/itch_stats.py` checked on a hand-built day only, not yet on a real one.
- Waiting on: `S101819-v50.txt.gz` (3.95 GB) downloading to `D:\itch`.

## Next steps

1. When the download finishes: `gzip -t` it, record its SHA-256 in `data/MANIFEST.md`.
2. Full-day AAPL replay through `analysis/itch_stats.py`; save CSVs and sidecars in `data/results/`.
3. Size the order table and price window from those numbers in `docs/design.md`; write their ADRs.
4. Best bid/ask plot via `make plots`; first `docs/log.md` entry; close the Phase A checklist.
5. Before Phase B: settle the host machine (see open questions) and install Yosys, SymbiYosys, clang-tidy, tcpreplay and Vivado.

## Important decisions

| Decision | Why |
|---|---|
| Day file `S101819-v50.txt.gz` (2019-10-18) | First choice `12302019` was unreachable and its `.md5sum` link returns 404. No published checksum for this file either, so integrity rests on `gzip -t` plus our own SHA-256. |
| Symbol AAPL | Liquid, penny ticks, well above $1. |
| Window base: mid of first two-sided, uncrossed BBO after the open | §6.4 rules out first trade. ADR still to write. |
| WSL Ubuntu runs Makefile and Python; day files stay in `D:\itch` | Windows has no `make`; day files never enter the repo. |
| `BoundedBook` subclasses `OrderBook` through one hook | Bounded and unbounded logic can't drift apart. |
| Force LF in `.gitattributes` | CRLF breaks the Makefile under WSL. |

## Open questions

- **Which machine is the Phase B host?** WSL2's virtual networking may not reach the Arty directly over a cable, and `CLAUDE.md` assumes a Linux host.
- **Token budgets:** the 4,000/30,000 limits in the global rules don't fit Phase A. Awaiting a call on whether to split work across sessions.
