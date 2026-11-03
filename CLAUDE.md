# CLAUDE.md — Ethernet-to-Order-Book Pipeline

## What this is

A low-latency market-data pipeline in SystemVerilog on the Digilent Arty A7-100T. Raw Ethernet frames come in. Best bid/offer (BBO) updates come out, each with hardware timestamps.

```
host replays a NASDAQ ITCH 5.0 day ──UDP multicast (MoldUDP64)──▶ Arty A7
   MII MAC → CDC → IPv4/UDP filter → MoldUDP64 → ITCH decoder → order table → price levels → BBO
                                                                                    │
host ◀──────────────── UDP: BBO updates + T0/T1/T2 timestamps ◀────────────────────┘
```

**This is a portfolio project.** Working RTL is half the deliverable. The other half is evidence: measured numbers, plots, screenshots, video, and a README a hiring engineer can absorb in 60 seconds. §11 is mandatory, not optional polish. **A phase without its numbers and media is not done.**

**Languages:** SystemVerilog (RTL), Python (golden model, cocotb, analysis), C++20 (host replayer and capture).

**Reuse contract:** `rtl/eth/` (MII MAC, CRC-32, async FIFO, IPv4/UDP RX/TX) lives in its own repo, **`fpga-eth-core`**, pulled in as a git submodule. The CCSDS receiver project reuses it for IQ replay. It must stay **payload-agnostic**: no ITCH knowledge below the UDP payload interface, and nothing hardcoded to this project's packet format.

---

## Current status

<!-- Claude: update as phases complete. Never mark a phase done until its §9 checklist
     passes in full, INCLUDING the portfolio items. -->

| Phase | Status | Key result |
|---|---|---|
| A — Python golden model + data | NOT STARTED | — |
| B — MAC + IPv4/UDP on hardware | NOT STARTED | — |
| C — MoldUDP64 + ITCH decoder | NOT STARTED | — |
| D — Order book engine | NOT STARTED | — |
| E — Timestamping + latency measurement | NOT STARTED | — |
| F — Stress, coverage closure, portfolio | NOT STARTED | — |

---

## Rules for Claude

- **Never invent a number.** Unmeasured values are `[TBD]`. Every reported number links to its CSV in `data/results/`. No estimates dressed as results.
- **Golden model before RTL.** No RTL block is written until the Python model produces its expected output.
- **"Done" means verified.** A block is done when its tests pass, `make lint` is clean, and `make ci` passes locally. A phase is done when its full §9 checklist passes.
- **Never silently produce a wrong book.** Every capacity limit and error condition is detected and counted, and either flagged on the output or used to mark the book stale.
- **Record decisions.** Every architectural choice gets an ADR in `docs/decisions/` (§4.5), including rejected alternatives.
- **Portfolio moments.** At the end of every phase, and whenever something striking works (first packet on hardware, first correct BBO, first latency histogram, zero-mismatch full day), do all of the following:
  1. Generate the plots yourself.
  2. List exactly what physical captures the user should make.
  3. Add them to `docs/portfolio-todo.md`.
  4. Wait for the user to confirm before marking the phase done.
- **Lab notebook.** Append a `docs/log.md` entry at the end of every working session (§11.3).
- **Update the status table and README** results at the end of every phase.
- **Stay in scope.** If a task drifts into §10, say so first.
- **Commit small and often** (§4.4).

---

## 1. Hardware

**Board:** Digilent Arty A7-100T, part `xc7a100tcsg324-1` (free Vivado ML Standard).

| Resource | Available | Use here |
|---|---|---|
| LUTs / FFs | 63,400 / 126,800 | Parsers, book logic |
| Block RAM | 135 × BRAM36 (4,860 Kb) | Order table, price-level arrays, bitmaps, FIFOs, histogram |
| Ethernet PHY | TI DP83848J, **10/100 MII** | The only network port |
| Clock | 100 MHz oscillator | → MMCM: core clock + 25 MHz PHY ref clock |
| UART | USB-UART | Debug console, fallback config/counters |
| Pmod | GPIO | Two pins pulse at T1 and T2 for external scope cross-check (§6.5) |

**Ethernet specifics that bite:**

- **The FPGA must generate the PHY's 25 MHz reference clock** on `eth_ref_clk`. The board has no PHY crystal fitted. No reference clock means no link, and nothing else will tell you why.
- MII signals: `eth_rx_clk` (25 MHz from the PHY), `eth_rxd[3:0]`, `eth_rx_dv`, `eth_rxerr`, `eth_crs`, `eth_col`, `eth_tx_clk`, `eth_txd[3:0]`, `eth_tx_en`, `eth_mdc`, `eth_mdio`, `eth_rstn`. **Use Digilent's master XDC for pins.** Don't type them from memory.
- 100 Mbps means 4 bits per 25 MHz `rx_clk`, **low nibble first**.

**Host:** Linux with a gigabit NIC, cabled directly to the Arty. The NIC autonegotiates down to 100 Mbps full duplex. Add a multicast route to that interface for the feed group. A direct cable means no switch and no IGMP.

**State the line-rate limit plainly.** The Arty has no transceivers and cannot do 10GbE. The README says:
> Validated on Arty A7 at 100 Mbps. The datapath is parameterized by width; the on-chip injector sustains [X] M msg/s through the pipeline, so the 100 Mbps PHY, not the pipeline, is the throughput limit. Pipeline latency (last message byte → BBO): p50 [X] ns / p99 [X] ns / max [X] ns.

---

## 2. Toolchain and build process

| Tool | Use |
|---|---|
| Vivado ML Standard (batch Tcl) | Synthesis, implementation, bitstream, ILA |
| Verilator | Lint, fast simulation |
| cocotb + cocotb-coverage | Testbenches, functional coverage |
| SymbiYosys + Yosys | Formal proofs on small blocks (§8.3) |
| Python 3 (NumPy, pandas, matplotlib, pytest) | Golden model, analysis, plots |
| C++20, CMake, GoogleTest | Host replayer and capture tools |
| `tcpreplay` | Quick-start replay of Python-generated pcaps |
| GTKWave / Surfer | Waveforms |

**Pin versions** of every tool in `docs/toolchain.md`. The Tcl scripts warn on a Vivado version mismatch.

### Makefile targets — all must work from a clean checkout

```
make setup         # .venv + pinned requirements.txt; git submodule update --init
make lint          # verilator --lint-only -Wall, zero warnings
make sim           # fast cocotb regression (< 5 min), DATA_W = 8 and 64
make sim-full      # 1M+ event stress runs
make cov           # coverage run + HTML report → docs/reports/coverage/
make formal        # SymbiYosys (§8.3)
make model-test    # pytest on the golden model
make host          # cmake build of host tools (Release + Debug/ASan/UBSan)
make host-test     # GoogleTest
make synth impl prog
make reports       # copy timing + utilization into docs/reports/<tag>/
make plots         # regenerate every plot in docs/media/ from data/results/
make ci            # everything CI runs, locally
make clean
```

- **Vivado runs in batch mode** from Tcl in `syn/`. Never commit a project directory. No GUI-only steps.
- **Vivado isn't in hosted CI.** Run it locally and commit the reports for every tagged milestone. A self-hosted runner is optional.
- **Timing closure is required.** No milestone is tagged with negative slack. Report WNS and Fmax.

### CI on every push

`make lint sim formal model-test host host-test`, plus clang-tidy and ruff, all clean. **A broken CI gets fixed before anything else.**

---

## 3. Repository layout

```
├── CLAUDE.md
├── README.md                 # portfolio front page (§11.4)
├── rtl/                      # synthesizable SystemVerilog ONLY
│   ├── eth/                  # submodule: fpga-eth-core (MII RX/TX, CRC32, async FIFO, IPv4/UDP RX/TX)
│   ├── lib/                  # cdc_*, fifos, xilinx/ primitive wrappers
│   ├── feed/                 # MoldUDP64 deframer, gap detector, ITCH splitter/decoder, locate filter
│   ├── book/                 # order table, price-level arrays, bitmaps, BBO tracker
│   ├── meas/                 # timestamp counter, event syncs, histogram, injector
│   ├── out/                  # BBO/stats packet builder, control regs
│   └── top/                  # top level, clocking
├── tb/
│   ├── cocotb/               # drivers, monitors, scoreboard, tests
│   └── formal/               # SBY configs + SVA property files
├── model/                    # Python golden model + seeded adversarial generator
├── host/                     # C++: replay, capture (CMake)
├── analysis/                 # Python: compare, latency stats, live view, plots
├── syn/  constr/  scripts/
├── data/
│   ├── samples/              # SMALL committed fixtures (< 5 MB total)
│   ├── results/              # CSVs behind every reported number
│   └── MANIFEST.md           # large off-repo files: name, source, date, SHA-256
├── docs/
│   ├── design.md             # architecture, CDC list, ADR summary
│   ├── decisions/            # ADRs
│   ├── protocol.md           # output packet formats (RTL ↔ host contract)
│   ├── latency.md            # per-stage cycle budget, timestamp calibration
│   ├── toolchain.md
│   ├── log.md                # lab notebook (§11.3)
│   ├── portfolio-todo.md     # outstanding captures for the user
│   ├── reports/              # timing, utilization, coverage per tag
│   └── media/
├── Makefile
└── .github/workflows/
```

---

## 4. Coding standards

### 4.1 SystemVerilog

- `logic` only.
- `always_ff` for sequential and `always_comb` for combinational logic. Never bare `always`.
- `<=` in sequential blocks, `=` in combinational ones. Never mix them within a block.
- **No inferred latches.** Default every `always_comb` output at the top.
- One module per file; the filename matches the module name. Named port connections only.
- Every file starts with `` `default_nettype none `` and restores it at the end.
- **Suffixes:** `_i`, `_o`, `_n` (active-low), `_q` (registered), `_d` (next state). Parameters are `UPPER_CASE`.
- **Reset:** synchronous and active-high, with a **reset synchronizer per clock domain**. Reset control state only; datapath registers are not reset unless required.
- **Parameterize everything:** `DATA_W` (start at 8), table sizes, price window, symbol count.
  - No magic numbers.
  - Protocol offsets are `localparam`s whose comment cites the spec section, e.g. `// ITCH 5.0 Add Order, offset 11`.
- **Inter-block interface is AXI4-Stream style:** `tdata`, `tkeep`, `tvalid`, `tready`, `tlast`, `tuser`.
  - Every block honours backpressure **except the MII input**, which can't be backpressured.
  - The MII input gets a FIFO with an **overflow counter**, never a silent drop.
- **CDC:**
  - Crossings go only through `rtl/lib/cdc_*` or the `fpga-eth-core` async FIFO.
  - Every crossing is listed in `docs/design.md` with its synchronizer type and constrained in XDC (`set_max_delay -datapath_only` on Gray pointers).
  - No unlisted crossings.
- **Vendor primitives** live only in `rtl/lib/xilinx/` wrappers, so everything simulates in Verilator.
- **SVA** sits next to the RTL it checks, visible to both Verilator and SymbiYosys. This applies especially to FIFOs and handshakes.
- `rtl/` contains no `$display`, no delays, and no `initial` blocks except BRAM init.
- **Every pipeline stage documents its latency in cycles** in the module header. `docs/latency.md` sums the cycle budget from T1 to T2.
- **Lint:** zero Verilator `-Wall` warnings. Any waiver is inline, justified and minimal.

### 4.2 Python

- Python 3.11+, type hints, `mypy --strict` on `model/`.
- `ruff` for lint and format, `pytest` for tests.
- The golden model is pure Python/NumPy, deterministic, with no hidden state and no plotting or I/O side effects.

### 4.3 C++

- C++20 with `-Wall -Wextra -Werror`, clang-format and clang-tidy.
- ASan/UBSan in Debug builds.
- No raw `new`/`delete`. Explicit endian handling for wire formats.

### 4.4 Commits and tags

- Small, frequent, meaningful commits that reference the phase, e.g. `feed: handle MoldUDP64 heartbeat (count=0)`. The history is portfolio evidence.
- Tag milestones: `v0.1-model`, `v0.2-packets-on-hw`, `v0.3-itch-decode`, `v0.4-book`, `v0.5-latency`, `v1.0`.
- Never commit build output, Vivado project directories or ITCH day files.

### 4.5 Decision records (`docs/decisions/NNNN-title.md`)

**Each record has these sections:** Context, Options considered, Decision, Consequences, Measured evidence.

**Required ADRs:**
- Order-table structure and sizing
- Price-level storage
- Price-window base selection
- Cut-through vs. store-and-forward
- Core clock frequency
- Timestamp design (§6.5, including why there is no separate high-speed timestamp clock)
- `DATA_W` default
- Locate-filter population
- Gap/stale semantics

`docs/design.md` summarizes them and links each one.

---

## 5. Protocol specification

### 5.1 Ethernet / IPv4 / UDP

- **Framing:** preamble 7 × `0x55`, SFD `0xD5`. The FCS is CRC-32 (reflected, poly `0x04C11DB7`). The minimum inter-frame gap is 96 bit times.
- **RX filter:**
  - Destination MAC = multicast `01:00:5E` + the low 23 bits of the group IP.
  - Destination IP = the configured group (e.g. `239.1.1.1`).
  - Destination UDP port = the configured port.
- **IPv4:** validate version and IHL (skip options when IHL > 5) and protocol 17. Verify the header checksum; failures are dropped and counted.
- **UDP checksum:** optional in IPv4. Ignore it on RX and send 0 on TX.
- **TX (results to host):** unicast UDP. **Learn the host MAC/IP from the source fields of received feed packets**, so no ARP is needed.

### 5.2 MoldUDP64 (transport, big-endian)

| Field | Bytes | Notes |
|---|---|---|
| Session | 10 | ASCII |
| Sequence number | 8 | Sequence of the **first** message in this packet |
| Message count | 2 | `0` = heartbeat, `0xFFFF` = end of session |
| Then, per message: length | 2 | Big-endian |
| message | *length* | One ITCH message |

**Gap detection.** Let `expected = seq + count`, and compare the next packet's sequence number against it:
- **Next seq > expected → gap.** Count the missing messages, raise the gap flag, and mark the book **stale**. Retransmission is out of scope.
- **Next seq < expected → duplicate or late.** Drop it and count it.
- **A session change** resets tracking.

### 5.3 ITCH 5.0 subset (big-endian; every message carries a stock locate)

**Common header:** type (1 byte), stock locate (2), tracking number (2), timestamp (6, ns since midnight).

| Type | Message | Length | Book action |
|---|---|---|---|
| `S` | System Event | 12 | Log only |
| `R` | Stock Directory | 39 | Learn the symbol → stock-locate mapping |
| `A` | Add Order | 36 | Insert: ref (8), side (1), shares (4), stock (8), price (4) |
| `F` | Add Order w/ MPID | 40 | Same as `A` |
| `E` | Order Executed | 31 | Reduce shares; remove at zero |
| `C` | Executed w/ Price | 36 | Reduce shares. **The book uses the order's resting price, not the execution price.** |
| `X` | Order Cancel | 23 | Reduce shares |
| `D` | Order Delete | 19 | Remove |
| `U` | Order Replace | 35 | Remove the original ref; add the new ref at the new price and shares, **same side** as the original |

- **All other types are skipped by their length field**, counted, and never mis-parsed.
- **Price:** 4 bytes, 4 implied decimals.
- **`E`, `C`, `X`, `D` and `U` carry only the order reference**, with no price and no side. The order table (§6.3) is therefore mandatory.
- **Filter on stock locate as early as possible.** Locate codes change daily.
  - Learn them from `R` messages for the configured symbol(s). `R` only appears at the start of day, so replay must start from the beginning of the file.
  - Fallback: the host computes the locate and configures it over UDP/UART.

### 5.4 Market data source

- **Real data:** NASDAQ's public ITCH 5.0 sample day files (historically at `emi.nasdaq.com/ITCH/`; verify they're still hosted there).
  - They're multi-GB, full days, already length-prefixed.
  - **Never commit them.** Record each file's name, date and SHA-256 in `data/MANIFEST.md`.
- **Symbol choice:** a liquid stock priced above $1 (penny ticks). Phase A measures its peak concurrent live orders and its price range, and those numbers size the hardware.
- **Rate reality:** NASDAQ bursts far exceed 100 Mbps. A "real-time" replay is rate-limited during bursts. Log the achieved send rate, and don't claim real-time fidelity.
- **Synthetic data:** a seeded Python generator for adversarial cases (§8.1).

---

## 6. Architecture

```
 eth_rx_clk (25 MHz, PHY)       │ core clk (start 125 MHz; push Fmax)
                                │
 MII RX ─▶ nibble→byte, SFD ─[T0 event]─▶ toggle sync ─▶ capture core counter
        └─▶ async FIFO (Gray ptrs) ─────▶ CRC check / frame valid
                                              │
                    Eth/IPv4/UDP parse + filter ─▶ MoldUDP64 deframer ─▶ gap detector
                    ── fpga-eth-core boundary (payload-agnostic above) ──
                                              │                  ◀── injector mux (§6.7)
                                ITCH splitter + decoder ──[T1: last byte of message]
                                              │
                                     stock-locate filter
                                              ▼
                       ORDER TABLE (BRAM, 4-way set-assoc: ref → side/price idx/shares)
                                              │ level delta (side, idx, ±shares)
                                              ▼
                       PRICE-LEVEL ARRAYS (bid, ask) + two-level occupancy bitmaps
                                              ▼
                       BBO tracker ──[T2]──▶ result packet builder ──▶ UDP TX ──▶ MII TX
```

### 6.1 Clocking

- An MMCM generates the **core clock** (start at 125 MHz, then push Fmax and record it) and the **25 MHz `eth_ref_clk`**.
- **There is no separate timestamp clock.** See §6.5 for why.

### 6.2 Cut-through vs. store-and-forward

The FCS arrives at the **end** of the frame.

- **Phases B–D: store-and-forward per frame.** Validate the FCS before acting on any message. Correct and simple.
- **Phase E: add cut-through as a build option.** Messages decode as bytes stream in, and book updates apply speculatively. A later bad FCS increments a counter and marks the book stale. There is no rollback.
- **Measure the latency difference** between the two modes with the same input. That comparison is one of the best interview stories in the project.

### 6.3 Order table

- **Structure:** a hash table in BRAM keyed on the 64-bit order reference, with **4-way set-associative buckets read in one cycle** through a wide BRAM word.
- **Hash:** the low bits of the reference. Refs increase through the day, and the low bits are well distributed even after symbol filtering.
- **Entry:** keep it narrow, because width multiplies BRAM cost.

  | Field | Notes |
  |---|---|
  | tag | Ref bits above the index. May be truncated, if Phase A shows the day's maximum ref can't alias within the tag + index range. ADR. |
  | price index | Relative to the window base, e.g. 12 bits. |
  | out-of-window flag | |
  | shares | 32 |
  | side | |
  | symbol index | |
  | valid | |

- **Size from data, not guesses:**
  - Phase A reports the chosen symbol's peak concurrent live orders. Size the table at ≥ 2× that.
  - Write the BRAM arithmetic into `design.md`. Illustrative example only, to be recomputed: an ~90-bit entry × 4 ways = 360 bits/set, which takes 5 BRAM36 at 512 × 72; that block gives 512 sets = 2,048 entries.
  - Check the total against the 135 BRAM36 available, leaving room for the levels, FIFOs and histogram.
- **Full bucket on insert** increments `table_overflow`. The order goes untracked, and its later cancel or execute counts as `unknown_ref`.
- **Both counters must read zero on the real-data run.**

### 6.4 Price levels and BBO — the key design decision

Implement the first option; document both.

| Option | How | Trade-off |
|---|---|---|
| **Price-indexed arrays + two-level occupancy bitmap** (chosen) | Per side, a BRAM array indexed by `price_ticks − base` holding aggregate shares. A 64×64 bitmap marks non-empty levels. When the top level empties, find-first-set over the bitmap gives the next best price in 2–3 cycles. | Full depth within the window; O(1) best-price update. Prices outside the window are not in the book. |
| Sorted register array (top-K) | Shift-insert sorted levels in registers | Single-cycle best, but levels below K are lost and the book goes wrong when the top depletes. Needs a backing store anyway. |

- **Window:** 4,096 ticks (±$20 at 1¢), parameterized.
- **Base selection (ADR):** host-configured from the prior close or the golden model's opening price. "First trade" doesn't work, because pre-market orders arrive hours before any trade.
- **Out-of-window orders:**
  - Still inserted into the order table, with the out-of-window flag set, so their later `E`/`X`/`D`/`U` resolve cleanly. They never touch the level arrays.
  - They are counted in `price_oow`. **Expect this to be non-zero on real data**, because of far-away stub quotes.
  - The real invariant is `bbo_oow == 0`: the true best bid/ask (per the unbounded golden model) never lay outside the window.
- **BBO output only on a change** of best bid/ask price or size.
- **Crossed or locked books** are flagged, never hidden.

### 6.5 Timestamps and latency definition

**There is one free-running 64-bit counter, in the core clock domain.**

| Point | Event | Precision |
|---|---|---|
| **T0** | SFD detected in the MII domain. A toggle synchronizer carries it to the core domain, which captures the counter. | **±40 ns.** The event is quantized to the 25 MHz nibble clock, plus a constant synchronizer delay (calibrated once with the ILA, documented, subtracted) with ±1 core-cycle jitter. |
| **T1** | Last byte of the ITCH message that caused the update, in the core domain | Cycle-exact |
| **T2** | BBO update valid at the output, in the core domain | Cycle-exact |

**Why there is no 400 MHz timestamp clock** (ADR, and a good interview answer):
- T1 and T2 are both events in the core domain, so their difference is an exact cycle count. A faster counter adds nothing.
- T0's precision is bounded by the 40 ns MII nibble clock. A 2.5 ns counter would report false precision there.
- A wide counter plus Gray conversion at 400 MHz also risks timing on the -1 speed grade, for no gain.

**Reported latencies:**
- **Headline metric: T2 − T1, the pipeline latency.** It's what the design controls. Report it in cycles and ns.
- **T2 − T0** includes 100 Mbps serialization: 80 ns per byte, so 8 µs for a 100-byte frame. Report it, labeled as line-rate bound.

**Distributions, not averages.**
- An on-chip histogram in BRAM records T2 − T1 with configurable bin width, plus exact min/max registers, and is read out over UDP.
- Per-update T0/T1/T2 also go to the host, so `latency.py` computes exact p50/p99/p99.9/max.

**External cross-check:**
- Pulse two Pmod pins at T1 and T2.
- Measure the delta with a scope or logic analyzer, and compare it with the internal numbers.
- Document the agreement, or explain the discrepancy.

### 6.6 Output packets (FPGA → host, UDP)

Formats are defined in `docs/protocol.md`, the single source of truth for both the RTL and `host/capture`.

**Per BBO update:**
- symbol index
- bid price/size, ask price/size
- ITCH timestamp
- MoldUDP64 sequence number of the cause
- T0, T1, T2
- flags: stale, crossed, locked, cut-through

**Stats packet, once per second.** Every counter:
- frames, CRC errors, IPv4 checksum errors, filtered
- gaps, duplicates
- FIFO overflow
- `table_overflow`, `unknown_ref`, `price_oow`, `bbo_oow` (when checked)
- unknown ITCH types, malformed messages

### 6.7 Internal pipeline-capacity test

The 100 Mbps link caps the feed at roughly 350k messages/s. To show the pipeline's real capacity, a **BRAM-backed injector** feeds stored MoldUDP64 payloads into the parser at core-clock rate, bypassing the MAC.

Report three numbers separately:
- **messages/s at line rate**
- **messages/s at pipeline capacity**
- **pipeline capacity at `DATA_W` = 8 vs. 64**

### 6.8 Debug

ILA builds sit behind a flag: `make impl ILA=1`. Functional builds never depend on debug cores.

---

## 7. Host tools

| Tool | Language | Does |
|---|---|---|
| `host/replay` | C++ | Reads the ITCH file and batches messages into MoldUDP64 packets (≤ 1,472-byte UDP payload), sent as multicast. Paces at real time, at a fixed rate, or flat out. Can inject gaps, duplicates and reordering. Logs the achieved send rate on every run. |
| `host/capture` | C++ | Receives BBO and stats packets and writes binary logs |
| `analysis/itch_extract.py` | Python | Filters a day file by symbol and time window into small fixtures |
| `analysis/compare.py` | Python | Diffs the FPGA BBO stream against the golden model: first mismatch with context, total mismatches |
| `analysis/latency.py` | Python | Histograms and p50/p99/p99.9/max for T2−T1 and T2−T0 |
| `analysis/bbo_view.py` | Python | **Live view for demos:** best bid/ask ladder updating in real time, plus a rolling latency histogram |

- **Pacing:** the C++ replayer busy-waits on `clock_gettime(CLOCK_MONOTONIC)`. Python can't pace reliably at line rate. For a quick start before the C++ replayer exists, generate a pcap in Python and send it with `tcpreplay`.
- **Bad-FCS injection from the host depends on the NIC driver.** `SO_NOFCS` only works on drivers that support it, and many don't. Check the T490's NIC.
  - If unsupported, cover FCS-error paths in simulation, and with a corrupt-frame mode on an MII-level test pattern generator in the FPGA.
  - Never claim hardware FCS-error testing without it.

---

## 8. Verification

### 8.1 cocotb environment

- **Drivers:**
  - An MII-level frame driver (nibbles, preamble, FCS, inter-frame gap).
  - A bypass driver into the UDP payload stream.
- **Golden model scoreboard:**
  - Every BBO update from the DUT is compared to the Python model, in order, field by field.
  - The model applies **the same capacity limits and window semantics** as the RTL configuration.
  - A separate unbounded model checks `bbo_oow`.
  - The first mismatch dumps context: the last N messages, plus model and DUT state.
- **Stimulus:** constrained-random, seeded and logged, so any failure reproduces.

**Scenarios that must be covered:**
- Normal add/cancel/execute/replace/delete sequences
- Sequence gaps, duplicates, out-of-order packets, session changes
- Heartbeats (count 0) and end of session (`0xFFFF`)
- Malformed or truncated messages, inconsistent lengths, unknown types, bad FCS (in both FCS modes), bad IPv4 checksum
- Order-table bucket overflow; out-of-window prices and their later cancels
- Crossed or locked book
- Rapid top-of-book churn; best level emptying, which exercises the bitmap search at varying distances
- Back-to-back minimum-gap frames; FIFO near-full
- Messages straddling bus words at every alignment, for each tested `DATA_W`

### 8.2 Functional coverage (cocotb-coverage)

**Cover:**
- each ITCH type × side
- the gap, duplicate, heartbeat and session paths
- bucket fill levels 0–4
- bitmap search distance
- both FCS modes
- bus alignments
- FIFO occupancy bins
- backpressure per interface

**Closure means 100% of defined bins**, with a written justification for any exclusion.

### 8.3 Formal (SymbiYosys)

- **Async FIFO:** no overflow or underflow; Gray pointer properties; data integrity.
- **Bitmap find-first-set:** always returns the best non-empty level.
- **MoldUDP64 sequence/gap logic.**
- **Order table:** never returns a stale entry after a delete.
- **AXI-Stream handshake properties** at block boundaries.

### 8.4 Hardware verification

1. Replay a full trading day: the whole file, so the locate filter and `R` learning are exercised. Roughly 20 minutes flat-out at 100 Mbps.
2. Capture the FPGA's BBO stream.
3. Run `compare.py` against the golden model. **It must report zero mismatches.** This is the hardware scoreboard and the project's credibility claim.

---

## 9. Phases and definitions of done

Every phase's DoD includes **README updated, `docs/log.md` entry written, that phase's media captured (§11.1), and the user confirming the physical captures**.

### Phase A — Python golden model + data
- [ ] ITCH 5.0 parser for the §5.3 subset; skips other types by length
- [ ] Order book model producing a BBO event stream; unbounded and capacity-limited variants
- [ ] Replayed against a full sample day for the chosen symbol. Report:
  - message counts by type
  - **peak concurrent live orders**
  - maximum order ref
  - price range
  - how often a ±2,048-tick window around the chosen base would have contained the BBO
- [ ] Self-consistency checks pass: no negative shares, every cancel/execute finds its order
- [ ] Synthetic adversarial generator, seeded
- [ ] Order-table and window sizing written into `design.md` from these numbers
- [ ] Media: plot of the reconstructed best bid/ask over the day

### Phase B — MAC + IPv4/UDP on hardware
- [ ] 25 MHz ref clock out; link up at 100 Mbps full duplex
- [ ] MII RX, CRC-32 check, async FIFO into the core domain, filter, UDP payload out
- [ ] UDP TX path with host MAC/IP learning: an echo or counter packet reaches the host
- [ ] cocotb tests for frames, CRC and filtering; async FIFO formally proven
- [ ] `fpga-eth-core` split into its own repo and consumed here as a submodule
- [ ] On hardware: 10 million packets from `replay`, with frame counters matching the host
- [ ] Media: ILA screenshot of a frame crossing MII → FIFO → parser; photo of the board cabled to the host

### Phase C — MoldUDP64 + ITCH decoder
- [ ] Deframer, gap/duplicate detector, ITCH splitter and decoder, locate filter (learned from `R`)
- [ ] Decoded message stream matches the golden parser: in simulation on a slice, on hardware on the full day
- [ ] Media: annotated waveform of one packet splitting into messages across bus words

### Phase D — Order book engine
- [ ] Order table, price arrays, bitmaps, BBO tracker
- [ ] 1M+ random events in sim with zero scoreboard mismatches
- [ ] **Hardware full-day replay: zero mismatches**
  - `table_overflow`, `unknown_ref` and `bbo_oow` all zero
  - `price_oow` reported, with an explanation
- [ ] `design.md` records the price-level decision with both options
- [ ] Media: plot of the FPGA BBO overlaid on the golden BBO for the day; `bbo_view.py` screen recording; `compare.py` "0 mismatches" run

### Phase E — Timestamping + latency
- [ ] T0/T1/T2 capture; T0 synchronizer offset calibrated and documented in `latency.md`
- [ ] Per-stage cycle budget in `latency.md` matches the measured T2 − T1
- [ ] Latency distributions: p50/p99/p99.9/max for T2−T1 and T2−T0
- [ ] Pmod scope cross-check agrees with the internal timestamps, or the discrepancy is explained
- [ ] Cut-through mode added; latency compared with store-and-forward
- [ ] Injector: pipeline capacity in messages/s at `DATA_W` = 8 and 64
- [ ] Media:
  - latency histogram (log-y) with percentile markers
  - cut-through vs. store-and-forward plot
  - scope screenshot

### Phase F — Stress, coverage closure, portfolio
- [ ] Coverage closure (§8.2); report committed
- [ ] Timing closed at the final core clock; reports committed
- [ ] README complete per §11.4, with every number traceable to `data/results/`
- [ ] **Demo video recorded** (§11.1)

---

## 10. Out of scope — do not build

- Encryption of any kind. Real feeds are plaintext multicast, and crypto on a latency path is the wrong design.
- FIR or other DSP. Order events aren't sampled signals.
- Order entry, strategy or trading logic.
- Retransmission requests or snapshot recovery. A gap only sets a flag and marks the book stale.
- 10G, optical, or any other PHY. That's a separate project; keep `DATA_W` parameterized and stop there.
- ITCH messages beyond §5.3.
- Recentering the price window during the day.

---

## 11. Portfolio deliverables — mandatory

### 11.1 What to capture

Claude generates plots and terminal recordings itself. For photos, phone video and anything on the physical board or scope, **Claude lists exactly what to capture at the end of each phase, adds it to `docs/portfolio-todo.md`, and stops until the user confirms**.

| Capture | Type | Phase |
|---|---|---|
| **Hero GIF:** `bbo_view.py` showing the live book updating from the FPGA, with the latency histogram filling in | Screen recording → GIF | D/E |
| **Demo video (60–120 s):** board on the desk, `replay` starting, BBO stream live, histogram, scope trace, closing on the zero-mismatch result | OBS + phone footage, unlisted YouTube | F |
| Board photo cabled to the host, link LEDs visible | Photo | B |
| ILA capture: frame through MAC → FIFO → parser | Screenshot | B |
| Annotated waveform: one MoldUDP64 packet → messages → BBO | Screenshot | C/D |
| FPGA BBO vs. golden BBO across the trading day | Plot | D |
| `compare.py` → "0 mismatches over N events" | asciinema / GIF | D |
| Latency histogram with p50/p99/p99.9/max | Plot | E |
| Cut-through vs. store-and-forward latency | Plot | E |
| Scope screenshot: T1/T2 Pmod pulses with the measured delta | Screenshot | E |
| Pipeline capacity vs. line rate (`DATA_W` 8 vs. 64) | Plot | E |
| Vivado device view (floorplan) + per-block utilization table | Screenshot + table | F |
| Coverage report summary | Screenshot | F |

### 11.2 Media rules

- Store media in `docs/media/`, named `YYYY-MM-DD_phase-x_description.ext`.
- **Plots are generated by `make plots` from `data/results/`.**
  - Reproducible; never hand-edited.
  - One consistent style; axes labeled with units.
- Every result in `data/results/` has a sidecar `.md` containing:
  - commit hash
  - exact command
  - tool versions
  - input (MANIFEST entry or seed)
  - an interpretation paragraph
- **Latency is always reported as a distribution** (p50/p99/p99.9/max), never an average alone, and always labeled T2−T1 or T2−T0.
- GIFs in the repo stay ≤ 8 MB. Videos go to unlisted YouTube or GitHub attachments, linked from the README, never committed.
- Screenshots are cropped to the point and annotated where it isn't obvious.

### 11.3 Lab notebook — `docs/log.md`

- Write dated entries covering what was done and the numbers measured.
- Record bugs as **symptom → root cause → fix**. These entries are interview material.
- Claude appends an entry at the end of every session.

### 11.4 README structure

1. One-sentence description
2. **Hero GIF**
3. Results table, with measured values only (`[TBD]` until measured):
   - **pipeline latency T2−T1** p50/p99/p99.9/max
   - T2−T0, labeled line-rate bound
   - pipeline capacity (msg/s) vs. line-rate throughput
   - cut-through saving
   - Fmax/WNS
   - utilization
   - events verified
   - coverage %
   - hardware full-day mismatches (0)
4. Architecture diagram (image, not ASCII)
5. Media gallery and demo video link
6. Design decisions (order table, price levels, cut-through, CDC, timestamping) linking to `docs/design.md`
7. The 100 Mbps limitation, stated plainly (§1)
8. Build and run instructions

---

## 12. Known failure modes

- **No `eth_ref_clk`:** the link never comes up
- **Nibble order reversed** in MII RX: everything after the SFD is garbage
- **CRC mismatch** from bit ordering (CRC-32 here is reflected)
- **Async FIFO pointer bugs** that show up only under burst traffic. Prove the FIFO formally first.
- **Parsing a message by the wrong length:** one unknown type desynchronizes everything after it. Always skip by the length field.
- **Using the execution price from `C`** to update the book, instead of the resting price
- **`U` (Replace) forgetting the side**, which comes from the original order
- **Dropping out-of-window orders from the table**, so their cancels become `unknown_ref`
- **Price-window base set from the first trade:** pre-market orders land out of window
- **Stale locate codes** from a different day's file, or replay starting after the `R` messages
- **T0 synchronizer offset not calibrated:** latencies are off by a constant
- **Reporting T2−T0 as "pipeline latency":** it's serialization-dominated
- **Order-table entry too wide:** the BRAM budget blows up. Narrow the tag and use a relative price index.
- **Python replayer can't pace at line rate**, which is why the replayer is C++
- **Editing `rtl/eth/` in place:** the submodule diverges from `fpga-eth-core`
