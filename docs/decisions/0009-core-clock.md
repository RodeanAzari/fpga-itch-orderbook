# 0009: Core clock 125 MHz

**Status: Accepted 2026-11-08.** Implemented by `rtl/lib/xilinx/clk_gen.sv` (`CORE_DIV`)
and `rtl/top/arty_top.sv`; built by `syn/build_top.tcl`.

## Context

Everything after fpga-eth-core's async FIFOs runs on one core clock from the MMCM
(1,000 MHz VCO ÷ `CORE_DIV`). The pipeline's latency (T2 − T1, the headline) is counted
in core cycles, so a faster clock shortens it in nanoseconds. The input is a 100 Mb/s MII
stream: one byte per 80 ns, so the core needs only 12.5 M byte-cycles per second for
throughput, and any clock above that keeps up with the line. The book's OOC builds had
already shown < 1 % slack at 8 ns (`docs/reports/ooc_book_engine/`).

## Options considered

1. **125 MHz (÷8).** eth-core is built and timed at this clock. Shortest latency of the
   options. Risk: the order table's price-index path has almost no margin.
2. **100 MHz (÷10).** Comfortable margin. Every cycle in the T1→T2 budget costs 25 %
   more nanoseconds.
3. **125 MHz with one more pipeline stage at the price-index multiply** (the constant
   reciprocal multiply in `order_table`'s S_HASH/quotient stages). More margin, one more
   cycle of latency per message, and a new test/OOC cycle on a verified block.

## Decision

Option 1: 125 MHz. The full chip meets timing at 8 ns with no critical warnings, so the
shorter latency is free today. Option 3 is the fallback if a later change breaks timing,
before Option 2: one cycle at 8 ns (8 ns) costs less than 25 % on every cycle.

## Consequences

- WNS is +0.059 ns. Any change on the book's price-index path, a placer seed change,
  or a fuller device (BRAM is at 80 %) can make timing fail. Each full-chip build reports
  WNS (`docs/reports/arty_top/summary.txt`); a negative slack blocks the bitstream
  (`syn/build_top.tcl`) and a tag (CLAUDE.md).
- `out_stats`' tick and `arty_top`'s timers take `CLK_HZ` from the same divider, so the
  stats period stays 1 s if the divider changes (`build_top.tcl` passes both).

## Measured evidence

`docs/reports/arty_top/` (Vivado 2025.2, xc7a100tcsg324-1, commit of this ADR):

| | Value |
|---|---|
| Core clock period | 8.000 ns |
| WNS / WHS | +0.059 ns / +0.010 ns |
| Fmax (core, worst path) | 1 / (8.000 − 0.059) ns ≈ 125.9 MHz |
| Worst path | `order_table` `off_q` → `qprod_q` (constant reciprocal multiply): 7.806 ns, 8 levels (7 CARRY4) |
| LUT / FF | 8,445 / 13,259 |
| BRAM36 / DSP | 107.5 / 10 |
