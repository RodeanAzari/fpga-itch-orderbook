Project ID: P2
@~/fpga-coord/PROTOCOL.md

Current state of the work:
progress.md

# CLAUDE.md — Ethernet-to-Order-Book Pipeline

## What this is

A low-latency market-data pipeline in SystemVerilog on the Digilent Arty A7-100T. Raw Ethernet frames come in. Best bid/offer (BBO) updates come out, each with hardware timestamps.

```
host replays a NASDAQ ITCH 5.0 day ──UDP multicast (MoldUDP64)──▶ Arty A7
   MII MAC → CDC → IPv4/UDP filter → MoldUDP64 → ITCH decoder → order table → price levels → BBO
host ◀──────────────── UDP: BBO updates + T0/T1/T2 timestamps ◀──────────────┘
```

**Portfolio project.** Working RTL is half the deliverable; the other half is evidence (measured numbers, plots, media, a full README). **A phase without its numbers and media is not done.**

**Languages:** SystemVerilog (RTL), Python (golden model, cocotb, analysis), C++20 (host tools).

**Reuse contract:** `rtl/eth/` is the **`fpga-eth-core`** submodule (MII MAC, CRC-32, async FIFO, IPv4/UDP RX/TX), shared with P3/P4. It stays **payload-agnostic**: no ITCH knowledge below the UDP payload interface. The MII boundary is a documented interface (signals, timing, nibble order in `fpga-eth-core`), so another PCS can replace the PHY without MAC changes. P3/P4's requirements on it: `docs/buildplan.md` (top). Cross-project rules: the imported `PROTOCOL.md`.

## Build spec (not loaded): `docs/buildplan.md`

The detailed spec lives in `docs/buildplan.md` with the original section numbers. **Read the relevant section before working on it.**

| § | Contents |
|---|---|
| top | fpga-eth-core reuse requirements; README line-rate wording; required ADR list |
| 5 | Protocol: Ethernet/IPv4/UDP, MoldUDP64 + gap rules, ITCH 5.0 subset and book actions, data source |
| 6 | Architecture: clocking, cut-through, order table, price levels/BBO, timestamps T0/T1/T2, output packets, injector, debug |
| 7 | Host tools: replay, capture, analysis scripts |
| 8 | Verification: cocotb env and required scenarios, coverage bins, formal targets, hardware full-day check |
| 9 | Phases A–F and their definitions of done |
| 10 | Out of scope (crypto, DSP, trading logic, retransmission, other PHYs, extra ITCH types, window recentering) |
| 11 | Portfolio: capture list, media rules, lab notebook, README standard (§11.4) |

Phase status lives in `progress.md`; per-phase results live in the README.

## Rules for Claude

- **Never invent a number.** Unmeasured values are `[TBD]`. Every reported number links to its CSV in `data/results/`. No estimates dressed as results.
- **Golden model before RTL.** No RTL block is written until the Python model produces its expected output.
- **"Done" means verified.** A block is done when its tests pass, `make lint` is clean, and `make ci` passes locally. A phase is done when its full checklist (buildplan §9) passes, including its media.
- **Never silently produce a wrong book.** Every capacity limit and error condition is detected and counted, and either flagged on the output or used to mark the book stale.
- **Record decisions.** Every architectural choice gets an ADR in `docs/decisions/` (§4.5), including rejected alternatives.
- **Portfolio moments.** At the end of every phase, and whenever something striking works (first packet on hardware, first correct BBO, first latency histogram, zero-mismatch full day): generate the plots yourself, list exactly what physical captures the user should make, add them to `docs/portfolio-todo.md`, and wait for the user to confirm before marking the phase done.
- **Lab notebook.** Append a dated `docs/log.md` entry at the end of every working session: what was done, numbers measured, bugs as **symptom → root cause → fix**.
- **Progress file.** Read `progress.md` at the start of every session and update it whenever progress, next steps or an important decision changes. It holds only current progress, phase status, next steps, important decisions and open questions. Replace or remove outdated items instead of appending. History goes in `docs/log.md`, full rationale in `docs/decisions/`.
- **Keep always-loaded docs lean.** CLAUDE.md ≤ 250 lines and `progress.md` ≤ 150 lines. Trim them at the end of every session that edits them, and at once if either is over. Keep only what a future session would get wrong without being told (current state, open work, rules, traps that cost real time). Cut finished-work narratives, dated history, reasons the code, tests, commits or reports already record, and duplicates of other docs. Move what must be kept but isn't needed every turn (the spec, append-only measurement tables, long verification detail) to files that aren't imported, such as `docs/buildplan.md` and `docs/measurements.md`, and leave a one-line pointer. Never edit the shared `~/fpga-coord/PROTOCOL.md`; propose trims to the user instead.
- **Update the phase status (`progress.md`) and README** results at the end of every phase.
- **Stay in scope.** If a task drifts into buildplan §10, say so first.
- **Commit small and often** (§4.4).

## Measurement rules

- Every result in `data/results/` has a sidecar `.md`: commit hash, exact command, tool versions, input (MANIFEST entry or seed), and an interpretation paragraph.
- Plots come only from `make plots` over `data/results/`: reproducible, never hand-edited, one style, axes labeled with units. Media go in `docs/media/` as `YYYY-MM-DD_phase-x_description.ext`; GIFs ≤ 8 MB; videos are never committed.
- **Latency is always a distribution** (p50/p99/p99.9/max), never an average alone, and always labeled T2−T1 (pipeline, the headline) or T2−T0 (line-rate bound, includes 80 ns/byte serialization).
- Never claim hardware FCS-error testing unless the NIC really sends bad FCS (`SO_NOFCS`); otherwise say it is covered in simulation.
- A "real-time" replay is rate-limited in bursts: log the achieved send rate, don't claim real-time fidelity.

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

The README wording for the line-rate limit is in `docs/buildplan.md`.

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
├── progress.md               # current state: progress, next steps, key decisions
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
│   ├── buildplan.md          # detailed build spec, §5–§11 (not imported)
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

The list of required ADRs is in `docs/buildplan.md`. `docs/design.md` summarizes them and links each one.

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
- **Vivado aborts with a locale error** (`_S_create_c_locale`) unless `vivado-env` (in `~/.bashrc`) is loaded first; in a non-interactive shell, `eval` its definition from `~/.bashrc` before calling it.
- **Closing `/dev/ttyUSB1` with HUPCL set resets the Arty design** (DTR drop), wiping the counters. Run `stty -F /dev/ttyUSB1 115200 raw -echo -hupcl` and keep one reader open for the whole run.
