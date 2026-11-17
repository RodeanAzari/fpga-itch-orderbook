"""Merge the regression's coverage files and report closure (buildplan §8.2).

Usage: python tb/cov_report.py [COV_DIR] [OUT_DIR]   (defaults build/cov, docs/reports/coverage)

Reads every *.json that tb/cocotb/cov.py wrote (one per parameter set), merges them with
tb_common.coverage, and writes OUT_DIR/index.html (every bin, every waiver) and
OUT_DIR/summary.txt (counts and holes). Exit 1 if any bin outside EXCLUDED is unhit.
"""

from __future__ import annotations

import sys
from pathlib import Path

from tb_common import coverage

ROOT = Path(__file__).resolve().parents[1]

# {item: {repr(bin): written reason}}. Every entry must name a real bin (else the report
# fails), so a waiver cannot outlive what it waives.
EXCLUDED: dict[str, dict[str, str]] = {}


def main(argv: list[str]) -> int:
    src = Path(argv[0]) if argv else ROOT / "build" / "cov"
    out = Path(argv[1]) if len(argv) > 1 else ROOT / "docs" / "reports" / "coverage"
    files = sorted(src.glob("*.json"))
    if not files:
        print(f"no coverage files in {src}; run the regression first", file=sys.stderr)
        return 1
    merged = coverage.merge(coverage.load(f) for f in files)
    holes = coverage.holes(merged, EXCLUDED)
    out.mkdir(parents=True, exist_ok=True)
    (out / "index.html").write_text(
        coverage.report_html(merged, EXCLUDED, title="Functional coverage (buildplan 8.2)")
    )
    n_bins = sum(len(i["bins"]) for i in merged.values())
    n_excl = sum(len(b) for b in EXCLUDED.values())
    lines = [
        f"{len(files)} parameter sets: " + ", ".join(f.stem for f in files),
        f"{len(merged)} items, {n_bins} bins, {n_excl} excluded, "
        f"{sum(len(b) for b in holes.values())} holes",
    ]
    lines += [f"HOLE {name}: {' '.join(bins)}" for name, bins in holes.items()]
    (out / "summary.txt").write_text("\n".join(lines) + "\n")
    print("\n".join(lines))
    return 1 if holes else 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
