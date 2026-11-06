# Progress

Current state only. History belongs in `docs/log.md`, full design rationale in `docs/decisions/`.

## Phase status

| Phase | Status | Key result |
|---|---|---|
| A — Python golden model + data | COMPLETE (`v0.1-model`) | Peak 27,814 live orders; d-left table 0 overflows over the day |
| B — MAC + IPv4/UDP on hardware | IN PROGRESS | Link up 100 Mbps FD; RX path clean on hardware; v0.1 gate run in progress |
| C — MoldUDP64 + ITCH decoder | IN PROGRESS (sim) | RTL matches the model record for record; OOC WNS +0.896 ns at 125 MHz |
| D — Order book engine | NOT STARTED | — |
| E — Timestamping + latency measurement | NOT STARTED | — |
| F — Stress, coverage closure, portfolio | NOT STARTED | — |

## Next steps

1. Phase B (`fpga-eth-core`, local at `~/fpga-eth-core`; P3 is hard-gated on v0.1 and v0.2):
   - v0.1 gate (`rx_stats`, >= 10 min at 8,000 datagrams/s, zero drops, FIFO high-water
     mark): judged on loss (`udp_ok` = sent, `ovf_lost` = 0), matching the registered test.
     Then: CSV + sidecar in fpga-eth-core, CHANGELOG, ask the user before tagging.
   - v0.2 gate: program `udp_echo`, run `host/udp_echo_check`. The host has only the
     link-local address 169.254.235.36 on `eth0`; the echo reply needs a Windows firewall
     rule for inbound UDP.
   - Then add the submodule here at the tag.
2. Phase C: annotated waveform (media); hardware full-day decode once the eth-core tags exist.
   The 64-bit message aligner is deferred to Phase E (ADR 0005), so "every alignment"
   coverage waits for it.
3. Before Phase D on hardware: fix host-side reordering (below). Before RTL is tagged in
   Phase D: check the table sizing on a second, busier day.

## Open problem: the host reorders datagrams (blocks a zero-mismatch full day)

- ~0.05% of datagrams reach the board out of order. WSL egress is in order (tcpdump,
  80,000/80,000); pktmon on Windows shows order intact through vmswitch and first broken at
  `netadaptercx.sys` (I225-LM miniport). Evidence: `C:\Users\rodea\fpga_capture\`.
- WSL `ethtool -L eth0 combined 1` did not help (restored to 16 at the user's request).
- MoldUDP64 turns reordering into gaps + stale book, so Phase D needs in-order delivery.
  Options: Intel driver queue setting (try first, free); USB Ethernet adapter passed to
  WSL via usbipd (reliable); Windows-side pinned sender.

## Important decisions

| Decision | Why |
|---|---|
| Day file `S101819-v50.txt.gz` (2019-10-18), symbol AAPL | `12302019` unreachable; AAPL is liquid with penny ticks. Own SHA-256 in MANIFEST. |
| Order table: 2-bank d-left, 2 × 8,192 × 4, 54-bit entries, 96 BRAM36 (ADR 0001) | A single-hash 4-way table overflowed at every feasible size on real refs. |
| Window: opening mid ($234.55) ± 2,048 ticks; out-of-window BBOs flagged and counted (ADR 0002) | `bbo_oow == 0` is unreachable: 2 empty-book moments at the session edges. |
| Repo in WSL at `~/OrderBook`; day files in `/mnt/d/itch` (AAPL extract in `derived/`) | `/mnt/c` was slow and CRLF-prone. Day files never enter the repo. |
| Host = WSL2 with mirrored networking; Arty JTAG/UART over usbipd-win | NAT can't reach the wired NIC; keeps the C++ sender's pacing on Linux. |
| GitHub repo `fpga-itch-orderbook` (private); local folder stays `~/OrderBook` | fpga-coord and tooling reference the local path. |
| `tb-common` is its own repo; shared Ethernet core keeps the name `fpga-eth-core` | P3/P4 consume both; a trading name would contradict payload-agnostic. |
| `BoundedBook` subclasses `OrderBook` through small hooks | Bounded and unbounded logic can't drift apart. |
| `udp_tx` counts the payload length from tlast; no length input | TX buffers a whole payload anyway, so length and tlast can't disagree. |
| `async_fifo` is always block RAM, with `DONT_TOUCH` Gray registers | Distributed RAM and a merged Gray bit produced unconstrained crossings. |
| v0.1 "zero drops" = no loss; host reordering reported separately (2026-11-05) | Registered test says zero drops; reordering is proven host-side. |

## Open questions

None.
