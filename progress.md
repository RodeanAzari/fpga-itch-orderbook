# Progress

Current state only. History belongs in `docs/log.md`, full design rationale in `docs/decisions/`.

## Where we are

**Phase A done** (signed off 2026-11-03, tag `v0.1-model`). **Phase B in progress**
(waiting on the cable). **Phase C started in parallel** (sim only).

- Golden model: parser, unbounded book, bounded book (d-left table, out-of-window flag),
  seeded generator. 24 tests, `make model-test` clean.
- Full day, AAPL 2019-10-18: peak 27,814 live orders; in the RTL configuration, zero table,
  ref, shares and unknown-ref errors, `bbo_oow` = 4. See `data/results/2019-10-18_AAPL.md`.
- README written to the §11.4 standard; ADRs 0001 and 0002 accepted; spec updated.

## Next steps

1. Phase B (`fpga-eth-core`, local at `~/fpga-eth-core`; P3 is hard-gated on v0.1 and v0.2):
   - RX path done (sim, Vivado, board programs). TX path done in sim and Vivado
     (2026-11-05): `mii_tx`, `eth_tx`, `udp_tx`, `examples/udp_echo`, `host/udp_echo_check`;
     `make ci` green; udp_echo WNS +0.312 ns at 125 MHz. Neither is tagged.
   - Host: WSL2 mirrored networking works (I225-LM is `eth0`; Arty reprograms over
     usbipd). **Waiting on the Ethernet cable.**
   - Once the cable is in: link LEDs → `eth0` carrier → v0.1 gate (`rx_stats`, >= 10 min,
     zero drops, FIFO high-water mark) → v0.2 gate (`udp_echo` + `udp_echo_check`). Pin
     the senders with `taskset`. The host needs an address on 192.168.10.0/24 (or a relaxed
     rp_filter) and a Windows firewall rule for inbound UDP. Then tag, and add the submodule here.
   - Consumer guide written in the fpga-eth-core README (2026-11-05).
   - Still missing: tcpreplay (needs sudo).
2. Phase C (MoldUDP64 + ITCH decoder):
   - Done: golden model `model/moldudp64.py` (ADR 0003 gap/stale, ADR 0004 locate,
     ADR 0005 `DATA_W` = 8). Full day through it: 1,658,958 records = Phase A's count,
     0 gaps (`data/results/2019-10-18_AAPL_feed.md`).
   - Done: RTL `rtl/feed/` (`mold_deframer`, `itch_decoder`, `feed_frontend`); 9 cocotb
     tests match the model record for record; 6 planted bugs caught.
   - Next: Vivado OOC build of `feed_frontend` for timing at 125 MHz; annotated waveform
     (Phase C media); then hardware full-day decode once the fpga-eth-core tags exist.
   - Open: the 64-bit path (message aligner) is deferred to Phase E (ADR 0005), so §8.1's
     "every alignment" coverage waits for it.
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
| WSL2 mirrored networking (`C:\Users\rodea\.wslconfig`), chosen 2026-11-04 | NAT mode can't reach the wired NIC; mirrored keeps the C++ sender and its pacing on Linux. A Windows-side sender was rejected (port + worse pacing). |
| GitHub repo renamed `OrderBook` → `fpga-itch-orderbook` (private); local folder stays `~/OrderBook` | Name carries the FPGA/ITCH keywords; local path is what fpga-coord and tooling reference. |
| `tb-common` built directly as its own repo, not in `tb/common/` first | P3 had already started and was waiting on it; CLAUDE.md §8.1 updated. |
| Shared Ethernet core keeps the name `fpga-eth-core` | P3/P4 consume it too; a trading name would contradict its payload-agnostic contract. |
| `BoundedBook` subclasses `OrderBook` through small hooks | Bounded and unbounded logic can't drift apart. |
| `udp_tx` counts the payload length from tlast; there is no length input (2026-11-05) | TX must buffer a whole payload anyway (the MII cannot stall), so consumers never compute a length and length/tlast can't disagree. |
| `async_fifo` is always block RAM, with `DONT_TOUCH` Gray registers | Distributed RAM and a merged Gray bit produced unconstrained crossings in Vivado. |

## Open questions

None.
