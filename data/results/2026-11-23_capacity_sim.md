# Simulation, 2026-11-23: sustained capacity at DATA_W 64, and the stage that limits it (Phase E)

CSV: `2026-11-23_capacity_sim.csv` (SHA-256 prefix `6aa791ea5a285495`): stage, input,
type, records, cycles. One row per record type and an "all" row per stage and input.

| | |
|---|---|
| Commit | RTL at `8455bcf` (unchanged); benches `tb/cocotb/test_rate_book.py`, `test_rate_feed.py` and `test_book.run(wait_idle)` committed with this file |
| Tools | Verilator 5.050, cocotb 2.1.0; `book_engine` at the board's geometry (`SET_BITS` 11, 16 ways, ADR 0010), `feed_frontend` at `DATA_W` 64 (ADR 0015); 125 MHz clock (8 ns) |
| Input | (1) The injector image (`analysis/injector_image.py`): the first 1,043 messages of the committed fixture, 987 book records, 22 packets; the same bytes the board's inject run measures through the whole chain. (2) The whole AAPL day 2019-10-18: AAPL extract of `S101819-v50.txt.gz` (MANIFEST), `data/runs/2019-10-18_AAPL.itch` SHA-256 prefix `dcca1ce4b5645aba`, 1,669,591 messages, 1,658,958 book records; packetized as `host/replay` sends it (35,312 packets, 51,427,519 payload bytes) |
| Command | `make capacity-sim ITCH_EXTRACT=$PWD/data/runs/2019-10-18_AAPL.itch` (wall time: book 1,300 s, feed 482 s) |

Method. Each stage runs alone with input offered every cycle and output always taken:
- **Book:** records start once `busy_o` falls (both RAM clears done, as `out_ctrl` waits on
  the board). The handshake is sampled on settled values each cycle; the bench must leave
  `s_valid` low on 0 cycles. A type's cycles are the gaps after its records; "all" = last
  accept − first accept.
- **Feed:** first input beat to last record, + 1.

Every BBO equals `model.bounded.BoundedBook`'s and every decoded record equals
`model.moldudp64.FeedDecoder`'s (the usual scoreboards), so a rate is only written for a
correct run.

## Result

| Stage alone, 125 MHz | Input | Cycles per book record | Book records/s |
|---|---|---|---|
| `book_engine` | whole day | **9.27** | **13.5 M** |
| `book_engine` | injector image | 8.87 | 14.1 M |
| `feed_frontend`, `DATA_W` 64 | whole day | 5.09 (6.09 payload bytes per cycle) | 24.6 M |
| `feed_frontend`, `DATA_W` 64 | injector image | 5.28 | 23.7 M |

Book cycles per record over the day, by type: Add (A) 9.00, Add with MPID (F) 8.05, Delete
(D) 8.64, Executed (E) 8.83, Executed with price (C) 9.06, Cancel (X) 8.44, **Replace (U)
16.24**. Over the whole day the feed front end's input waited (`tready` low) on 1,998,011
of its 8,440,624 cycles.

Check that the stages compose: inside the full chain (`test_core`, `ob_core` at
`DATA_W` 64, injector run) the book accepts the image's 987 records over exactly 8,757
cycles, the same as alone, record for record. The chain's measured 9,059 cycles (simulation =
board, `2026-11-15_injector_board_w64.md`) is that plus about 300 cycles of pipeline fill
and drain.

## Interpretation

At `DATA_W` 64 the order book is the limit, not the input. `order_table` takes one message
at a time (its `s_ready_o` is high only when idle, about 8–9 cycles per message: hash,
address, BRAM read and register, match, act, beat), and a Replace is a removal plus an
insert, so it takes two passes. The day's 6 % of Replaces lift the average from 8.83
(every other type) to 9.27 cycles. Sustained over the whole real day, that is **13.5 M book records/s
(13.6 M ITCH messages/s counting the 10,633 non-book AAPL messages)**, close to the 14.4 M
msg/s burst the board measured. The front end at 64 has about 1.8× headroom over the book.
The book's rate is about 36× what the 100 Mb/s link can deliver (374 k msg/s,
`2026-11-15_line_rate_board.md`), so on this board the link sets the rate. A faster input
would next be limited by the one-message-at-a-time table; pipelining it (several messages
in flight with hazard checks on the same set) is the next step, not built.

This is simulation, cycle-exact against the RTL; on the board only the injector's burst
is measured, and it equals simulation to the cycle.
