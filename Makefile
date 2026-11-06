# Run from Linux/WSL. Targets from CLAUDE.md §2 are added as their phase makes them real.
VENV := .venv
PY := $(VENV)/bin/python

ITCH_DAY ?= /mnt/d/itch/S101819-v50.txt.gz

OSS := $(HOME)/.local/opt/oss-cad-suite/bin
export PATH := $(PATH):$(OSS)
RTL := $(wildcard rtl/*/*.sv)

.PHONY: setup lint sim model-test host host-test host-lint ci day-stats plots clean

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

# clang-format + clang-tidy, zero findings. Tests skip bugprone-unchecked-optional-access:
# it does not recognize gtest's ASSERT_TRUE(opt) as a guard.
host-lint: host
	$(VENV)/bin/clang-format --dry-run -Werror $(HOST_SRC)
	$(VENV)/bin/clang-tidy -p build/host-Release --quiet host/mold.cpp host/replay.cpp
	$(VENV)/bin/clang-tidy -p build/host-Release --quiet \
	  --checks=-bugprone-unchecked-optional-access host/test_mold.cpp

# Everything CI runs, locally. formal joins when this repo has it.
ci: lint sim model-test host-test host-lint

# Full-day replay of one symbol through the golden model (~minutes; needs the day file).
day-stats:
	$(PY) -m analysis.itch_stats $(ITCH_DAY) AAPL data/results/2019-10-18_AAPL

plots:
	$(PY) -m analysis.plots

clean:
	rm -rf build .mypy_cache .ruff_cache .pytest_cache
