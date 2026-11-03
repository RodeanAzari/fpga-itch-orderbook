# Progress

Current state only. History belongs in `docs/log.md`, full design rationale in `docs/decisions/`.

## Where we are

**Phase A (Python golden model + data): blocked on two decisions (ADRs 0001, 0002).**

- Done: parser, unbounded book, bounded book, seeded generator (19 tests); full-day AAPL
  replay; table sizing study; BBO plot; sidecar, design.md sizing, log entry.
- Key numbers (2019-10-18 AAPL): 27,814 peak live orders, max ref 287,271,257, 2 of 831,839
  two-sided BBOs outside the ±2,048-tick window. See `data/results/2019-10-18_AAPL.md`.

## Next steps

1. User decides ADR 0001 (d-left order table) and ADR 0002 (out-of-window BBO treatment).
2. Update CLAUDE.md §6.3 / §6.4 / §9 to match the accepted decisions.
3. `BoundedBook`: switch to the accepted table placement and add `ref_overflow` /
   `shares_overflow`, with tests.
4. Check a second, busier day's sizing before RTL. Not required for Phase A.
5. README to the §11.4 standard; tag `v0.1-model`; mark Phase A done.
6. Before Phase B: settle the host machine and install Yosys, SymbiYosys, clang-tidy,
   tcpreplay and Vivado.

## Important decisions

| Decision | Why |
|---|---|
| Day file `S101819-v50.txt.gz` (2019-10-18) | `12302019` was unreachable. No published checksum, so we record our own SHA-256. |
| Symbol AAPL | Liquid, penny ticks, well above $1. |
| Window base: golden model's opening mid ($234.55) | §6.4 rules out first trade (ADR 0002). |
| Proposed: 2-bank d-left order table, 65,536 entries, 96 BRAM36 | A single-hash 4-way table overflowed at every feasible size on real refs (ADR 0001). |
| WSL Ubuntu runs Makefile and Python; day files in `D:\itch`, AAPL extract in `D:\itch\derived` | Windows has no `make`; day files never enter the repo. |
| `BoundedBook` subclasses `OrderBook` through one hook | Bounded and unbounded logic can't drift apart. |

## Open questions

- **Which machine is the Phase B host?** WSL2's virtual networking may not reach the Arty
  directly over a cable.
- Your uncommitted edits to `CLAUDE.md` (README standard §11.4, the intro line) are left for
  you to commit.
