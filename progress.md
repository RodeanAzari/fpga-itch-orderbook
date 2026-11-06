# Progress

Current state only. History belongs in `docs/log.md`, full design rationale in `docs/decisions/`.

## Phase status

| Phase | Status | Key result |
|---|---|---|
| A — Python golden model + data | COMPLETE (`v0.1-model`) | Peak 27,814 live orders; d-left table 0 overflows over the day |
| B — MAC + IPv4/UDP on hardware | IN PROGRESS: only the user's physical captures left | `eth-core-v0.1`/`v0.2` tagged: RX 4.88 M datagrams zero drops; TX 9 M echoes intact; submodule at v0.2 |
| C — MoldUDP64 + ITCH decoder | IN PROGRESS (sim) | RTL matches the model record for record; OOC WNS +0.896 ns at 125 MHz |
| D — Order book engine | IN PROGRESS (sim) | ADR 0006 (levels) accepted; model has sub-penny rule + book stale; no RTL yet |
| E — Timestamping + latency measurement | NOT STARTED | — |
| F — Stress, coverage closure, portfolio | NOT STARTED | — |

## Next steps

1. Phase B (`fpga-eth-core`, local at `~/fpga-eth-core`; P3 is hard-gated on v0.1 and v0.2):
   - Both tags released; `rtl/eth/` is the fpga-eth-core submodule at `eth-core-v0.2`.
   - Left for the Phase B DoD: the user's board photo (link LEDs) and ILA screenshot
     (`docs/portfolio-todo.md`), then the README Phase B section, and the "10 M packets from
     `replay`" hardware run (`host/replay` now exists; needs the board and a frame-counter
     bitstream that accepts the feed's group/port).
   - Host setup that the runs need: `eth0` has only link-local 169.254.235.36; a Hyper-V
     firewall rule (`fpga-udp-echo`) admits UDP from 192.168.10.2; fpga-eth-core `make ci`
     needs `PY=~/OrderBook/.venv/bin/python`.
2. Phase C: annotated waveform (media); hardware full-day decode needs a top level
   (eth-core RX → feed_frontend → decoded-stream report to host). `host/replay` is done
   (`make host host-test`; byte-identical to `model.moldudp64.packetize` on the AAPL day).
   The 64-bit message aligner is deferred to Phase E (ADR 0005), so "every alignment"
   coverage waits for it.
3. Phase D (sim): order-table RTL + cocotb vs `BoundedBook` next, then level arrays/bitmap/BBO
   (ADR 0006). Before Phase D on hardware: fix host-side reordering (below). Before RTL is tagged in
   Phase D: check the table sizing on a second, busier day.

## Open problem: the host reorders datagrams (blocks a zero-mismatch full day)

- ~0.05% of datagrams reach the board out of order. WSL egress is in order (tcpdump,
  80,000/80,000); pktmon on Windows shows order intact through vmswitch and first broken at
  `netadaptercx.sys` (I225-LM miniport). Evidence: `C:\Users\rodea\fpga_capture\`.
- WSL `ethtool -L eth0 combined 1` did not help (restored to 16 at the user's request).
- MoldUDP64 turns reordering into gaps + stale book, so Phase D needs in-order delivery.
- Driver is Intel `e2fn.sys` 2.1.4.3 (Lenovo OEM): no RSS, VMQ or queue-count setting exists
  (checked 2026-11-06). Likely cause (unproven): sends reach the miniport from different
  CPUs and land in different hardware TX queues.
- **Agreed plan, next time the user is home with the board** (rig: `rx_stats` bitstream +
  `udp_seq_send`, count `seq_err`/`seq_lost`; claim the board first):
  1. Rate sweep: 60 s each at several rates (e.g. 100/85/70/50% of line rate), current settings.
  2. User disables "Packet Priority & VLAN" (Windows adapter settings, admin), repeat at the
     worst rate; optionally Interrupt Moderation off too.
- Fallbacks if neither gives zero: USB 3 Ethernet adapter (RTL8153/AX88179) to WSL via usbipd
  (in order by construction; verify 100 Mbps through usbipd with a 10-min sustained run);
  newer Intel driver; Windows-native pinned sender; native Linux / separate Linux host.
  Rejected: a reorder buffer in the FPGA (changes ADR 0003, hides a host defect).

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
