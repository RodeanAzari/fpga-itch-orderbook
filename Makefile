# Run from Linux/WSL. Targets from CLAUDE.md §2 are added as their phase makes them real.
VENV := .venv
PY := $(VENV)/bin/python

.PHONY: setup model-test clean

setup:
	python3 -m venv $(VENV)
	$(PY) -m pip install -q -r requirements.txt

model-test:
	$(PY) -m ruff check model analysis
	$(PY) -m ruff format --check model analysis
	$(PY) -m mypy
	$(PY) -m pytest -q

clean:
	rm -rf build .mypy_cache .ruff_cache .pytest_cache
