"""Build and run the cocotb tests with Verilator: [JOBS=n] python tb/run.py [test_module ...]

Each test module runs at every DATA_W in its parameter list. Exit status is non-zero if
any test fails.
"""

from __future__ import annotations

import os
import subprocess
import sys
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path

from cocotb_tools.runner import get_results, get_runner

ROOT = Path(__file__).resolve().parent.parent
DEFRAMER = [
    "rtl/feed/mold_deframer_w8.sv",
    "rtl/feed/mold_deframer_w64.sv",
    "rtl/feed/mold_deframer.sv",
]
FEED = [*DEFRAMER, "rtl/feed/itch_decoder.sv", "rtl/feed/feed_frontend.sv"]
BOOK = ["rtl/lib/sdp_ram.sv", "rtl/book/order_table.sv"]
ENGINE = [*BOOK, "rtl/book/prio_enc.sv", "rtl/book/book_levels.sv", "rtl/book/book_engine.sv"]
OUT = ["rtl/lib/sdp_ram.sv", "rtl/lib/sync_fifo.sv", "rtl/out/out_builder.sv"]
ETH = [
    f"rtl/eth/rtl/{m}.sv"
    for m in (
        "crc32_d8",
        "async_fifo",
        "mii_rx",
        "rx_ovf_guard",
        "eth_fcs_check",
        "frame_buffer",
        "rx_reset_bridge",
        "eth_rx",
        "udp_rx",
        "udp_tx",
        "mii_tx",
        "eth_tx",
    )
]
CORE = [
    *ETH,
    *FEED,
    *ENGINE,
    *OUT[1:],
    "rtl/lib/pkt_gate.sv",
    "rtl/lib/cdc_pulse.sv",
    "rtl/meas/t0_track.sv",
    "rtl/meas/injector.sv",
    "rtl/meas/inj_meter.sv",
    "rtl/out/out_mux.sv",
    "rtl/out/out_stats.sv",
    "rtl/out/out_ctrl.sv",
    "rtl/top/ob_core.sv",
]

# the injector ROM (ADR 0014; analysis/injector_image.py), a string parameter, so it is
# left out of the build directory's name
INJ = {"INJ_FILE": f'"{ROOT / "data/samples/injector_aapl.mem"}"'}

# test module -> (toplevel, sources, parameter sets)
TESTS = {
    "test_feed": ("feed_frontend", FEED, [{"DATA_W": 8}]),
    "test_deframer": ("mold_deframer", DEFRAMER, [{"DATA_W": 8}, {"DATA_W": 64}]),
    # 512 sets: the generator's bucket collisions overflow it; 2,048: ADR 0010's geometry.
    "test_order_table": ("order_table", BOOK, [{"SET_BITS": 9}, {"SET_BITS": 11}]),
    "test_book": ("book_engine", ENGINE, [{"SET_BITS": 9}, {"SET_BITS": 11}]),
    # ADDR_W 4: full and wrap every few dozen cycles.
    "test_sync_fifo": ("sync_fifo", OUT[:2], [{"ADDR_W": 4}]),
    "test_out": ("out_builder", OUT, [{}]),
    "test_stats": ("out_stats", ["rtl/out/out_stats.sv"], [{"TICK_CYCLES": 50}]),
    "test_mux": ("out_mux", ["rtl/out/out_mux.sv"], [{"LEVEL_W": 36}]),
    "test_ctrl": ("out_ctrl", ["rtl/out/out_ctrl.sv"], [{}]),
    "test_pkt_gate": ("pkt_gate", ["rtl/lib/pkt_gate.sv"], [{}]),
    # real depths, and 8-entry queues that fill and wrap
    "test_t0_track": (
        "t0_track",
        ["rtl/meas/t0_track.sv"],
        [
            {"PEND_AW": 6, "DONE_AW": 7, "CUT_THROUGH": 0},
            {"PEND_AW": 3, "DONE_AW": 3, "CUT_THROUGH": 0},
            {"PEND_AW": 6, "DONE_AW": 7, "CUT_THROUGH": 1},
            {"PEND_AW": 3, "DONE_AW": 3, "CUT_THROUGH": 1},
        ],
    ),
    # 30,000-cycle stats tick: stats datagrams interleave with the records.
    # both eth_rx modes (ADR 0013)
    "test_core": (
        "ob_core",
        CORE,
        [
            {"TICK_CYCLES": 30_000, "CUT_THROUGH": 0, **INJ},
            {"TICK_CYCLES": 30_000, "CUT_THROUGH": 1, **INJ},
        ],
    ),
    "test_injector": (
        "injector",
        ["rtl/meas/injector.sv"],
        [{"INIT_FILE": INJ["INJ_FILE"]}],
    ),
    # make sim-full only: minutes per run, real geometry.
    "test_book_full": ("book_engine", ENGINE, [{"SET_BITS": 11}]),
}
FULL_ONLY = {"test_book_full"}


def run_set(name: str, idx: int) -> int:
    """Build and run one parameter set of one test module; returns the failure count."""
    top, srcs, param_sets = TESTS[name]
    params = param_sets[idx]
    tag = "_".join(f"{k}{v}" for k, v in params.items() if isinstance(v, int)) or "default"
    build_dir = ROOT / "build" / "sim" / f"{name}_{tag}"
    runner = get_runner("verilator")
    runner.build(
        sources=[ROOT / s for s in srcs],
        hdl_toplevel=top,
        parameters=params,
        build_args=["--assert", "-Wall", "-Wno-fatal", "--timing"],
        build_dir=build_dir,
        always=True,
    )
    os.environ.update({k: str(v) for k, v in params.items()})
    os.environ["PYTHONPATH"] = f"{ROOT}:{os.environ.get('PYTHONPATH', '')}"
    results = runner.test(
        hdl_toplevel=top,
        test_module=name,
        test_dir=ROOT / "tb" / "cocotb",
        build_dir=build_dir,
    )
    _, fails = get_results(results)
    return fails


def run_child(name: str, idx: int) -> tuple[str, int, Path]:
    """One set in its own process (environment and output kept apart); its log file."""
    log = ROOT / "build" / "sim" / f"{name}_{idx}.log"
    log.parent.mkdir(parents=True, exist_ok=True)
    with log.open("w") as f:
        rc = subprocess.run(
            [sys.executable, __file__, "--set", name, str(idx)], stdout=f, stderr=subprocess.STDOUT
        ).returncode
    return name, rc, log


def main(names: list[str]) -> int:
    """JOBS > 1 (environment) runs that many sets at once, each in its own process; a
    failing set's log is printed in full, the others' summary lines only."""
    if names[:1] == ["--set"]:
        return 1 if run_set(names[1], int(names[2])) else 0
    names = names or [t for t in TESTS if t not in FULL_ONLY]
    sets = [(n, i) for n in names for i in range(len(TESTS[n][2]))]
    jobs = int(os.environ.get("JOBS", "1"))
    if jobs <= 1:
        return 1 if sum(run_set(n, i) for n, i in sets) else 0
    failed = 0
    with ThreadPoolExecutor(jobs) as pool:
        for fut in as_completed([pool.submit(run_child, n, i) for n, i in sets]):
            name, rc, log = fut.result()
            text = log.read_text()
            if rc:
                failed += 1
                print(text)
                print(f"FAILED: {name} (exit {rc}), log {log}")
            else:
                print(f"{name}: {log}")
                print("\n".join(ln for ln in text.splitlines() if "TESTS=" in ln))
            sys.stdout.flush()
    print(f"{len(sets)} sets, {failed} failed")
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
