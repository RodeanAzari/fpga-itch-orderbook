# Lab notebook

## 2026-11-02: Phase A, golden model and first real-data numbers

**Done**
- Repo, Makefile (WSL), ITCH 5.0 parser, unbounded book, capacity-limited book, seeded
  adversarial generator. 19 tests, ruff and `mypy --strict` clean.
- Downloaded `S101819-v50.txt.gz` (2019-10-18, 3.95 GB). The first choice (`12302019`) was
  unreachable and its listed `.md5sum` returns 404.
- Full-day replay: 302,347,067 messages in 4 min 08 s. AAPL (locate 14): 1,669,591
  messages, peak 27,814 live orders, zero self-consistency errors.

**Finding: the specified order table cannot reach zero overflow**
- Symptom: replaying real refs against a 4-way table, the worst bucket held 18 live orders
  at 16,384 sets.
- Root cause:
  - Low ref bits are not evenly distributed for one symbol.
  - Even with a good multiplicative hash, the worst bucket reaches 8. At the peak, load is
    Poisson with λ = 1.7, and that is simply the maximum over 16,384 buckets.
- Fix (proposed, ADR 0001): 2-bank d-left hashing, 65,536 entries. Zero overflow all day,
  versus 36,545 overflow inserts for a single table of the same size.

**Finding: `bbo_oow` is 2, not 0**
- The ±2,048-tick window around the $234.55 opening mid missed two BBOs. Both were in an
  almost empty book: 04:00:11 (bid $175.12) and 20:00:00 (ask $260.00).
- Proposed treatment in ADR 0002.

**Bug: generator never produced a locked book**
- Symptom: the coverage test `test_generator_actually_reaches_the_adversarial_cases` failed
  on `locked`.
- Root cause:
  - Crossing orders were never removed, so the book stayed permanently crossed.
  - `U` replacements also ignored side, priced bids above the base, and deepened the cross.
- Fix: crossed and locked states became brief explicit episodes (add both sides, then delete
  both), and replacements keep their side.

**Numbers:** `data/results/2019-10-18_AAPL.md`.
