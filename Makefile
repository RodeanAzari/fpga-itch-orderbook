# Run from Linux/WSL. Targets from CLAUDE.md §2 are added as their phase makes them real.
VENV := .venv
PY := $(VENV)/bin/python

ITCH_DAY ?= /mnt/d/itch/S101819-v50.txt.gz

OSS := $(HOME)/.local/opt/oss-cad-suite/bin
export PATH := $(PATH):$(OSS)
RTL := $(wildcard rtl/*/*.sv)
ETH_RTL := $(wildcard rtl/eth/rtl/*.sv)  # fpga-eth-core: sources for our tops, not linted as tops
# Vendor wrappers, with eth-core's simulation models of the primitives they use
XIL_RTL := $(wildcard rtl/lib/xilinx/*.sv) $(wildcard rtl/eth/tb/models/*.sv)

.PHONY: demo-gif demo-gif-day2 prog setup lint sim sim-full formal model-test host host-test host-lint ci day-stats plots clean

setup:
	git submodule update --init
	python3 -m venv $(VENV)
	$(PY) -m pip install -q -r requirements.txt

# Verilator -Wall on every module as its own top, zero warnings.
lint:
	@for f in $(RTL); do \
	  verilator --lint-only -Wall --assert $(RTL) $(ETH_RTL) $(XIL_RTL) --top-module $$(basename $$f .sv) || exit 1; \
	done
	verilator --lint-only -Wall --assert $(RTL) $(ETH_RTL) $(XIL_RTL) --top-module ob_core -GCUT_THROUGH=1
	verilator --lint-only -Wall --assert $(RTL) $(ETH_RTL) $(XIL_RTL) --top-module ob_core -GDATA_W=64 -GCUT_THROUGH=1
	@for t in mold_deframer itch_decoder feed_frontend injector ob_core; do \
	  verilator --lint-only -Wall --assert $(RTL) $(ETH_RTL) $(XIL_RTL) --top-module $$t -GDATA_W=64 || exit 1; \
	done
	@echo "lint: $(words $(RTL)) modules clean, ob_core in cut-through (ADR 0013), feed, injector and ob_core at DATA_W 64 (ADR 0015)"

# cocotb + Verilator regression (tb/run.py [test_module ...] for a subset); JOBS parameter
# sets at once, each in its own process (build/sim/<test>_<n>.log).
JOBS ?= 4
sim:
	JOBS=$(JOBS) $(PY) tb/run.py

# SymbiYosys proofs (buildplan §8.3).
formal:
	cd tb/formal && sby -f bitmap_search.sby

# Phase D stress: book_engine on 1M+ events and the whole real day (minutes). Appends one
# row per passing test to SIM_FULL_CSV; SEED makes it reproducible.
SEED ?= 20261106
SIM_FULL_CSV ?= build/sim_full.csv
ITCH_EXTRACT ?= /mnt/d/itch/derived/2019-10-18_AAPL.itch
sim-full:
	SEED=$(SEED) SIM_FULL_CSV=$(abspath $(SIM_FULL_CSV)) ITCH_EXTRACT=$(ITCH_EXTRACT) \
	  $(PY) tb/run.py test_book_full

model-test:
	$(PY) -m ruff check model analysis tb host
	$(PY) -m ruff format --check model analysis tb host
	$(PY) -m mypy
	$(PY) -m pytest -q

# Host tools (CMake): Release for real runs, Debug with ASan/UBSan for the unit tests.
HOST_SRC := $(wildcard host/*.cpp host/*.hpp)
host:
	cmake -S host -B build/host-Release -DCMAKE_BUILD_TYPE=Release -Wno-dev >/dev/null
	cmake --build build/host-Release -j
	cmake -S host -B build/host-Debug -DCMAKE_BUILD_TYPE=Debug -Wno-dev >/dev/null
	cmake --build build/host-Debug -j

# GoogleTest under sanitizers, then replay's output checked against the Python model.
host-test: host
	ctest --test-dir build/host-Debug --output-on-failure
	REPLAY=build/host-Release/replay $(PY) -m pytest -q host/test_replay.py
	CAPTURE=build/host-Release/capture $(PY) -m pytest -q host/test_capture.py

# clang-format + clang-tidy, zero findings. Tests skip bugprone-unchecked-optional-access:
# it does not recognize gtest's ASSERT_TRUE(opt) as a guard.
host-lint: host
	$(VENV)/bin/clang-format --dry-run -Werror $(HOST_SRC)
	$(VENV)/bin/clang-tidy -p build/host-Release --quiet host/mold.cpp host/replay.cpp host/capture.cpp
	$(VENV)/bin/clang-tidy -p build/host-Release --quiet \
	  --checks=-bugprone-unchecked-optional-access host/test_mold.cpp

# Everything CI runs, locally.
ci: lint sim formal model-test host-test host-lint

# Full-day replay of one symbol through the golden model (~minutes; needs the day file).
day-stats:
	$(PY) -m analysis.itch_stats $(ITCH_DAY) AAPL data/results/2019-10-18_AAPL

# Program the board over JTAG (claim it first: coord.py board claim P2). BIT= overrides.
prog:
	bash -c 'eval "$$(sed -n "/^vivado-env()/,/^}/p" ~/.bashrc)" && vivado-env && \
	  vivado -mode batch -nojournal -nolog -source syn/prog.tcl -tclargs $(BIT)'

# README demo GIF from the zero-mismatch run day500c (data/runs/ is local; see
# data/results/2026-11-09_full_day_board.md for the run).
DEMO_LOG ?= data/runs/day500c.log
demo-gif:
	$(PY) -m analysis.bbo_view --log $(DEMO_LOG) --packets data/runs/slice0.pkts --fps 10 \
	  --gif docs/media/2026-11-09_phase-d_board-full-day-bbo.gif

# Day-2 (2025-12-12) media from the clean run d2_shoot (data/results/2026-11-10_full_day_board_16way.md).
demo-gif-day2:
	$(PY) -m analysis.bbo_view --log data/runs/d2_shoot.log \
	  --packets data/runs/2025-12-12_AAPL_slice0.pkts --lo-price 2575300 --fps 10 \
	  --gif docs/media/2026-11-10_phase-d_board-full-day2-bbo.gif
	$(PY) -m analysis.term_shot --text data/runs/d2_shoot.compare.txt --prompt '~/OrderBook$$ ' \
	  --command 'ITCH=~/itch-cache/2025-12-12_AAPL.itch LO_PRICE=2575300 SESSION=20251212AA scripts/board_run.sh d2_shoot bbo 500 0' \
	  --png docs/media/2026-11-10_phase-d_board-compare-day2.png

plots:
	$(PY) -m analysis.plots

clean:
	rm -rf build .mypy_cache .ruff_cache .pytest_cache tb/formal/*_prove tb/formal/*_cover
