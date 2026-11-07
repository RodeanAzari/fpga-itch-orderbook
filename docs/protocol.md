# Wire protocol: FPGA ↔ host (version 1)

The single source of truth for the RTL (`rtl/out/`, `rtl/top/`), the golden encoder
(`model/protocol.py`) and the host tools (`host/capture`). Rationale: ADR 0007.

The market-data feed itself (host → FPGA, MoldUDP64 over UDP multicast) is specified in
`docs/buildplan.md` §5 and is not repeated here.

## Addressing

| Stream | Direction | IP / port | Notes |
|---|---|---|---|
| Feed | host → FPGA | multicast group : feed port (configured, default `239.1.1.1:26400`) | MoldUDP64 |
| Control | host → FPGA | FPGA unicast IP : `CTRL_PORT` (build parameters, default `192.168.10.2:7000`) | The FPGA does not answer ARP: the host adds a static neighbour entry for the FPGA MAC (default `02:00:0a:0b:0c:0d`). |
| Output | FPGA → host | the source MAC / IP / port of the last accepted control packet | Nothing is sent before the first accepted control packet. Source port `CTRL_PORT`. |

All multi-byte fields are **big-endian** (network order, as in ITCH and MoldUDP64).
Offsets are in bytes from the start of the UDP payload.

## Common header (12 bytes, every packet in both directions)

| Offset | Size | Field | Value |
|---|---|---|---|
| 0 | 2 | `magic` | `0x4F42` ("OB") |
| 2 | 1 | `version` | `1` |
| 3 | 1 | `type` | see below |
| 4 | 4 | `pkt_seq` | FPGA → host: datagram number since the last config ack (the ack is 0), +1 per datagram of any type. A jump means the host lost output datagrams. Host → FPGA: any value, echoed in the ack. |
| 8 | 2 | `count` | number of records that follow |
| 10 | 2 | `record_len` | bytes per record (fixed per type; lets a reader check the length) |

The UDP payload length is exactly `12 + count × record_len`. Packet types:

| `type` | Name | Direction | `record_len` | `count` |
|---|---|---|---|---|
| `0x01` | BBO batch | FPGA → host | 64 | 1 … 22 |
| `0x02` | Trace batch | FPGA → host | 40 | 1 … 36 |
| `0x03` | Stats | FPGA → host | 8 | `N_STATS` (one u64 per counter) |
| `0x10` | Config | host → FPGA | 24 | 1 |
| `0x11` | Config ack | FPGA → host | 24 | 1 |

## Batching (types 0x01 and 0x02)

Records wait in a FIFO. When the transmitter is free and the FIFO is not empty, the
builder starts a datagram with `count = min(records waiting, max count)` and sends exactly
those records, oldest first. Under light load each datagram carries one record and adds
no delay; under load records batch by themselves. There is no timer.

The output path honours backpressure end to end: when the TX link is the bottleneck,
the book and the feed stall, and frames are lost (and counted, `rx_fifo_overflow`) only
at the MII input. **No record is ever dropped after it is produced.** Records leave in the
order they were produced; across datagrams, `pkt_seq` gives that order.

## BBO record (type 0x01, 64 bytes)

One per BBO change published by the book (golden model: `BoundedBook`, one `Bbo` per
message whose best price, size or out-of-window flag changed).

| Offset | Size | Field | Meaning |
|---|---|---|---|
| 0 | 2 | `symbol` | symbol index; always 0 (single symbol, ADR 0001) |
| 2 | 2 | `flags` | bit 0 `stale`, bit 1 `crossed`, bit 2 `locked`, bit 3 `out_of_window`, bit 4 `cut_through`; other bits 0 |
| 4 | 4 | `bid_px` | ITCH price units (1/10,000 $); 0 when the side is empty |
| 8 | 4 | `ask_px` | as `bid_px` |
| 12 | 6 | `bid_sz` | shares at the best bid; 0 when empty |
| 18 | 6 | `ask_sz` | as `bid_sz` |
| 24 | 6 | `itch_ts` | ITCH timestamp of the cause message (ns since midnight) |
| 30 | 2 | — | 0 |
| 32 | 8 | `mold_seq` | MoldUDP64 sequence number of the cause message |
| 40 | 8 | `t0` | core-clock cycle count at the cause frame's SFD (§6.5); 0 until Phase E |
| 48 | 8 | `t1` | cycle count at the cause message's last byte; 0 until Phase E |
| 56 | 8 | `t2` | cycle count when this update was valid at the book output; 0 until Phase E |

Flags:
- `stale`: the feed decoder was stale when it decoded the cause message (a gap or a
  malformed message before it, ADR 0003), or the book is stale after applying it (a
  capacity limit, ADR 0006). The feed's bit travels with the record through the book, so
  a gap found while an earlier message is still in the book does not flag that earlier
  update. Sticky until the next config. Golden model: `model.protocol.expected`.
- `crossed`: both sides non-empty and `bid_px > ask_px`. `locked`: both non-empty and
  `bid_px == ask_px`. Same definitions as `model.book.Bbo`.
- `out_of_window`: ADR 0002. Set exactly when this BBO differs from the unbounded book.
- `cut_through`: the bitstream was built in cut-through mode (§6.2); 0 until Phase E.

## Trace record (type 0x02, 40 bytes)

Sent instead of BBO records when the config's `mode` is 1. One per record the feed
decoder delivers to the book (golden model: `FeedDecoder.records`, in order). The book is
not fed in trace mode.

| Offset | Size | Field | Meaning |
|---|---|---|---|
| 0 | 1 | `msg_type` | ITCH type byte: `A F E C X D U` |
| 1 | 1 | `flags` | bit 0 `sell` (A/F only); other bits 0 |
| 2 | 6 | `itch_ts` | ITCH timestamp |
| 8 | 8 | `mold_seq` | MoldUDP64 sequence number of this message |
| 16 | 8 | `ref` | order reference (`U`: the original) |
| 24 | 8 | `new_ref` | `U` only, else 0 |
| 32 | 4 | `shares` | A/F/U: shares; E/C/X: shares removed; D: 0 |
| 36 | 4 | `price` | A/F/U only, else 0 (`C`'s execution price is not carried; §12) |

## Stats (type 0x03)

One datagram per second of core clock, and none before the first config. `count` =
`N_STATS` = 40; each record is one u64 counter, in this order. All counters are snapshotted
on the same cycle, and all are cleared by a config. Each is a count of events since the
last config, except where noted.

| # | Counter | Source |
|---|---|---|
| 0 | `uptime_cycles` | core cycles since the last config (the snapshot time) |
| 1 | `rx_frames_ok` | `eth_rx.frame_ok_o` |
| 2 | `rx_crc_err` | `eth_rx.crc_err_o` |
| 3 | `rx_err` | `eth_rx.rx_err_o` |
| 4 | `rx_runt` | `eth_rx.runt_o` |
| 5 | `rx_oversize` | `eth_rx.oversize_o` |
| 6 | `rx_fifo_overflow` | frames lost at the MII input, `eth_rx.ovf_frames_o` |
| 7 | `feed_datagrams` | feed `udp_rx.ok_o` |
| 8 | `feed_filtered` | feed `udp_rx.filtered_o` (includes control and other traffic) |
| 9 | `ip_csum_err` | feed `udp_rx.ip_csum_err_o` |
| 10 | `udp_malformed` | feed `udp_rx.malformed_o` |
| 11 | `mold_packets` | `SeqCounters.packets` |
| 12 | `mold_heartbeats` | `SeqCounters.heartbeats` |
| 13 | `mold_end_of_session` | `SeqCounters.end_of_session` |
| 14 | `mold_gaps` | `SeqCounters.gaps` |
| 15 | `mold_gap_messages` | `SeqCounters.gap_messages` |
| 16 | `mold_duplicates` | `SeqCounters.duplicates` |
| 17 | `mold_session_changes` | `SeqCounters.session_changes` |
| 18 | `mold_malformed_packets` | `SeqCounters.malformed_packets` |
| 19 | `itch_unknown_types` | `FeedCounters.unknown_types` |
| 20 | `itch_malformed_messages` | `FeedCounters.malformed_messages` |
| 21 | `itch_other_locate` | `FeedCounters.other_locate` |
| 22 | `itch_directory_matches` | `FeedCounters.directory_matches` |
| 23 | `itch_locate_changes` | `FeedCounters.locate_changes` |
| 24 | `locate` | current locate; bit 16 = known (a value, not a count) |
| 25 | `book_records` | records into the book (or into the trace in mode 1) |
| 26 | `ref_overflow` | `BoundedBook` counters (ADR 0001) |
| 27 | `duplicate_ref` | |
| 28 | `shares_overflow` | |
| 29 | `table_overflow` | |
| 30 | `unknown_ref` | |
| 31 | `over_reduce` | |
| 32 | `price_oow` | |
| 33 | `price_subpenny` | |
| 34 | `bbo_oow` | |
| 35 | `stale` | bit 0 feed stale, bit 1 book stale (a value) |
| 36 | `out_records` | BBO or trace records sent |
| 37 | `out_datagrams` | datagrams sent, all types (= the next `pkt_seq`) |
| 38 | `ctrl_ok` | config packets accepted (not cleared by a config) |
| 39 | `ctrl_bad` | packets to `CTRL_PORT` rejected: wrong magic, version, type, count, `record_len` or length (not cleared by a config) |

## Config (type 0x10, host → FPGA) and config ack (type 0x11)

One 24-byte record. A config is applied atomically: the FPGA latches the new output
destination (the packet's source), resets the feed decoder, the book and every counter
except `ctrl_*`, clears the book's RAMs, sets `pkt_seq` to 0, and then sends the ack
(`pkt_seq` 0) with the record echoed. **Start the replay only after the ack**: messages
that arrive before it are dropped while the pipeline is in reset (counted as frames, not
decoded). A rejected packet changes nothing and gets no ack.

| Offset | Size | Field | Meaning |
|---|---|---|---|
| 0 | 4 | `feed_group` | multicast group of the feed (class D) |
| 4 | 2 | `feed_port` | UDP port of the feed |
| 6 | 1 | `mode` | 0 = BBO records, 1 = trace records; other values rejected |
| 7 | 1 | `locate_valid` | 1 = use `locate` (host fallback, ADR 0004); 0 = learn it from `R`; other values rejected |
| 8 | 8 | `symbol` | ASCII, left-justified, space-padded (ITCH stock field) |
| 16 | 4 | `lo_price` | price window's low edge in ITCH price units, (base − 2,048) × 100 (ADR 0002) |
| 20 | 2 | `locate` | used when `locate_valid` = 1 |
| 22 | 2 | — | 0 |

Until the first accepted config the pipeline stays in reset and nothing is sent.
