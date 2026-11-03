# Run from Linux/WSL. Targets from CLAUDE.md §2 are added as their phase makes them real.
VENV := .venv
PY := $(VENV)/bin/python

ITCH_DAY ?= /mnt/d/itch/S101819-v50.txt.gz

.PHONY: setup model-test day-stats plots clean

setup:
	python3 -m venv $(VENV)
	$(PY) -m pip install -q -r requirements.txt

model-test:
	$(PY) -m ruff check model analysis
	$(PY) -m ruff format --check model analysis
	$(PY) -m mypy
	$(PY) -m pytest -q

# Full-day replay of one symbol through the golden model (~minutes; needs the day file).
day-stats:
	$(PY) -m analysis.itch_stats $(ITCH_DAY) AAPL data/results/2019-10-18_AAPL

plots:
	$(PY) -m analysis.plots

clean:
	rm -rf build .mypy_cache .ruff_cache .pytest_cache
