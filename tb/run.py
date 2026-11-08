"""Build and run the cocotb tests with Verilator: python tb/run.py [test_module ...]

Each test module runs at every DATA_W in its parameter list. Exit status is non-zero if
any test fails.
"""

from __future__ import annotations

import os
import sys
from pathlib import Path

from cocotb_tools.runner import get_results, get_runner

ROOT = Path(__file__).resolve().parent.parent
FEED = ["rtl/feed/mold_deframer.sv", "rtl/feed/itch_decoder.sv", "rtl/feed/feed_frontend.sv"]
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
    "rtl/out/out_mux.sv",
    "rtl/out/out_stats.sv",
    "rtl/out/out_ctrl.sv",
    "rtl/top/ob_core.sv",
]

# test module -> (toplevel, sources, parameter sets)
TESTS = {
    "test_feed": ("feed_frontend", FEED, [{"DATA_W": 8}]),
    # 512 sets: the generator's bucket collisions overflow it; 8,192: ADR 0001's geometry.
    "test_order_table": ("order_table", BOOK, [{"SET_BITS": 9}, {"SET_BITS": 13}]),
    "test_book": ("book_engine", ENGINE, [{"SET_BITS": 9}, {"SET_BITS": 13}]),
    # ADDR_W 4: full and wrap every few dozen cycles.
    "test_sync_fifo": ("sync_fifo", OUT[:2], [{"ADDR_W": 4}]),
    "test_out": ("out_builder", OUT, [{}]),
    "test_stats": ("out_stats", ["rtl/out/out_stats.sv"], [{"TICK_CYCLES": 50}]),
    "test_mux": ("out_mux", ["rtl/out/out_mux.sv"], [{"LEVEL_W": 36}]),
    "test_ctrl": ("out_ctrl", ["rtl/out/out_ctrl.sv"], [{}]),
    "test_pkt_gate": ("pkt_gate", ["rtl/lib/pkt_gate.sv"], [{}]),
    # 30,000-cycle stats tick: stats datagrams interleave with the records.
    "test_core": ("ob_core", CORE, [{"TICK_CYCLES": 30_000}]),
    # make sim-full only: minutes per run, real geometry.
    "test_book_full": ("book_engine", ENGINE, [{"SET_BITS": 13}]),
}
FULL_ONLY = {"test_book_full"}


def main(names: list[str]) -> int:
    failed = 0
    for name in names or [t for t in TESTS if t not in FULL_ONLY]:
        top, srcs, param_sets = TESTS[name]
        for params in param_sets:
            tag = "_".join(f"{k}{v}" for k, v in params.items()) or "default"
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
            failed += fails
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
