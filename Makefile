# Run from Linux/WSL. Targets from CLAUDE.md §2 are added as their phase makes them real.
VENV := .venv
PY := $(VENV)/bin/python

ITCH_DAY ?= /mnt/d/itch/S101819-v50.txt.gz

OSS := $(HOME)/.local/opt/oss-cad-suite/bin
export PATH := $(PATH):$(OSS)
RTL := $(wildcard rtl/*/*.sv)

.PHONY: setup lint sim model-test day-stats plots clean

setup:
	git submodule update --init
	python3 -m venv $(VENV)
	$(PY) -m pip install -q -r requirements.txt

# Verilator -Wall on every module as its own top, zero warnings.
lint:
	@for f in $(RTL); do \
	  verilator --lint-only -Wall --assert $(RTL) --top-module $$(basename $$f .sv) || exit 1; \
	done
	@echo "lint: $(words $(RTL)) modules clean"

# cocotb + Verilator regression (tb/run.py [test_module ...] for a subset).
sim:
	$(PY) tb/run.py

model-test:
	$(PY) -m ruff check model analysis tb
	$(PY) -m ruff format --check model analysis tb
	$(PY) -m mypy
	$(PY) -m pytest -q

# Full-day replay of one symbol through the golden model (~minutes; needs the day file).
day-stats:
	$(PY) -m analysis.itch_stats $(ITCH_DAY) AAPL data/results/2019-10-18_AAPL

plots:
	$(PY) -m analysis.plots

clean:
	rm -rf build .mypy_cache .ruff_cache .pytest_cache
