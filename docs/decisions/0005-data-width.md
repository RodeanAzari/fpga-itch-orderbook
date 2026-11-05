# 0005: `DATA_W` default for the feed pipeline

**Status: Accepted 2026-11-05.** `DATA_W = 8` is implemented in `rtl/feed/`; the 64-bit variant
is planned for the Phase E capacity measurement.

## Context

CLAUDE.md §4.1 asks for a parameterized datapath that starts at 8 bits, and §6.7 asks
for pipeline capacity at `DATA_W` = 8 and 64. The MAC (fpga-eth-core) delivers one
byte per core cycle. At 125 MHz that is 1 Gbit/s, ten times what the 100 Mbps link can
carry, so the link, not the datapath, limits real traffic. Only the on-chip injector
(§6.7) can push the pipeline harder than the link.

## Options considered

| Option | Capacity at 125 MHz | Cost / risk |
|---|---|---|
| **8 bits** | 125 MB/s: roughly 3.9 M ITCH messages/s at ~32 B per framed message, already ~11× the line-rate message rate | Byte-serial parsing: simple, every field at a byte offset, easy to verify |
| 64 bits | 8× the bytes per cycle; message capacity then limited by one message per cycle in the decoder | Messages start at any byte lane; needs an aligner, plus straddle cases at every alignment (§8.1) |
| 64 bits only | as above | Gives up the simple, already-verified path, for capacity the link can't use |

## Decision

- **`DATA_W = 8` is the default** and the only width the Phase C RTL implements. The
  modules take `DATA_W` as a parameter and stop elaboration with an error for any other
  value, so nothing silently runs at an untested width.
- **The 64-bit path comes in Phase E**, for the injector measurement. Its plan:
  - A message aligner after `mold_deframer` puts each message's first byte in lane 0.
  - `itch_decoder` already extracts fields at fixed offsets from a right-aligned window
    of the message, so it only needs a wider shift.
- Phase C's "every alignment" coverage (§8.1) applies once 64 bits exists. At 8 bits
  every message starts in lane 0, so there is only one alignment.

## Consequences

- `make sim` runs at `DATA_W = 8` only for now. CLAUDE.md §2 lists 8 and 64; 64 joins it
  in Phase E.
- The capacity comparison (§6.7) waits on the aligner.

## Measured evidence

`tb/cocotb/test_feed.py` at `DATA_W = 8`: 14,917 records match the golden model field by
field across clean, adversarial and configured-locate streams. Capacity numbers:
`[TBD]` (Phase E injector).
