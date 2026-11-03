# Toolchain

Pinned versions. Recorded 2026-11-03 on WSL2 Ubuntu (kernel 6.18). The Vivado Tcl scripts
warn when `version -short` differs from the version below.

| Tool | Version | Where / how to get it on PATH |
|---|---|---|
| Vivado ML Standard | 2025.2 | `~/Xilinx/2025.2`; run `vivado-env` (in `~/.bashrc`) first |
| Verilator | 5.053 devel (rev v5.052-319-g704ec556e) | `~/.local/bin/verilator` (first on PATH). Debian's 5.032 in `/usr/bin` is **not** used |
| Yosys | 0.69+208 | oss-cad-suite, `~/.local/opt/oss-cad-suite/bin` |
| SymbiYosys | 0.69 | oss-cad-suite |
| Python | 3.14.4 | system; project venv in `.venv` |
| cocotb / cocotb-coverage | 2.1.0 / 2.0 | `requirements.txt` (pinned with `pip freeze`) |
| pytest / ruff / mypy | 9.2.0 / 0.17.3 / 2.4.1 | `requirements.txt` |
| CMake | 4.2.3 | system |
| g++ | 15.2.0 | system |
| clang-tidy / clang-format | — | **not installed system-wide yet** (needed for `make host`, Phase B+) |
| tcpreplay | — | **not installed yet** (`sudo apt install tcpreplay`) |
| GTKWave | system package | waveforms |

The Makefile prepends `~/.local/opt/oss-cad-suite/bin` to PATH so non-interactive
shells find Yosys and SBY.
