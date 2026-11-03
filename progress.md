# Progress

Current state only. History belongs in `docs/log.md`, full design rationale in `docs/decisions/`.

## Where we are

**Phase A (Python golden model + data): every checklist item met, awaiting your sign-off.**
Phase A has no physical captures; its media is the generated BBO plot.

- Golden model: parser, unbounded book, bounded book (d-left table, out-of-window flag),
  seeded generator. 24 tests, `make model-test` clean.
- Full day, AAPL 2019-10-18: peak 27,814 live orders; in the RTL configuration, zero table,
  ref, shares and unknown-ref errors, `bbo_oow` = 4. See `data/results/2019-10-18_AAPL.md`.
- README written to the §11.4 standard; ADRs 0001 and 0002 accepted; spec updated.

## Next steps

1. On sign-off: mark Phase A done in CLAUDE.md and README; tag `v0.1-model`.
2. Phase B (`fpga-eth-core`, local at `~/fpga-eth-core`; P3 is hard-gated on `eth-core-v0.1`):
   - Done: toolchain pinned (`docs/toolchain.md`); `async_fifo` formally proven + cocotb.
   - Waiting on you: create GitHub repo `RodeanAzari/fpga-eth-core` and push.
   - Next RTL: MII RX (nibble→byte, SFD) → CRC-32 check → Eth/IPv4/UDP filter with
     runtime-configurable group/port; then add as submodule here.
   - Still missing: clang-tidy/clang-format system-wide, tcpreplay.
3. Before RTL is tagged in Phase D: check the table sizing on a second, busier day.

## Important decisions

| Decision | Why |
|---|---|
| Day file `S101819-v50.txt.gz` (2019-10-18) | `12302019` was unreachable. No published checksum, so we record our own SHA-256. |
| Symbol AAPL | Liquid, penny ticks, well above $1. |
| Order table: 2-bank d-left, 2 × 8,192 × 4, 54-bit entries, 96 BRAM36 (ADR 0001) | A single-hash 4-way table overflowed at every feasible size on real refs. |
| Window: opening mid ($234.55) ± 2,048 ticks; out-of-window BBOs flagged and counted (ADR 0002) | `bbo_oow == 0` is unreachable: 2 empty-book moments at the session edges. |
| Repo lives in WSL at `~/OrderBook`; day files stay in `D:\itch` (`/mnt/d/itch`), AAPL extract in `/mnt/d/itch/derived` | Tools live in WSL; `/mnt/c` was slow and CRLF-prone. Day files never enter the repo. |
| Host is WSL2: the Arty reaches it over a cable through usbipd-win | Confirmed working by the user (2026-11-03). |
| `BoundedBook` subclasses `OrderBook` through small hooks | Bounded and unbounded logic can't drift apart. |

## Open questions

None.
