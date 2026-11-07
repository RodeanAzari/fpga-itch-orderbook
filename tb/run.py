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

# test module -> (toplevel, sources, parameter sets)
TESTS = {
    "test_feed": ("feed_frontend", FEED, [{"DATA_W": 8}]),
    # 512 sets: the generator's bucket collisions overflow it; 8,192: ADR 0001's geometry.
    "test_order_table": ("order_table", BOOK, [{"SET_BITS": 9}, {"SET_BITS": 13}]),
    "test_book": ("book_engine", ENGINE, [{"SET_BITS": 9}, {"SET_BITS": 13}]),
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
