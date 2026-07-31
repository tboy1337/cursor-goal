#!/usr/bin/env python3
"""Assert pytest-cov JSON metrics meet the project floor.

Checks statement, branch, function, and combined coverage each >= threshold.
Designed to run after ``pytest --cov-report=json:coverage.json``.

Usage:
  py -3 scripts/check_coverage_metrics.py
  py -3 scripts/check_coverage_metrics.py --threshold 95 --json coverage.json
"""

from __future__ import annotations

import argparse
import ast
import json
import logging
import sys
from pathlib import Path
from typing import Any

LOG = logging.getLogger("check_coverage_metrics")

DEFAULT_THRESHOLD = 95.0
DEFAULT_JSON = "coverage.json"
DEFAULT_SOURCE = Path("src/cursor_goal")


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--json",
        type=Path,
        default=Path(DEFAULT_JSON),
        help="Path to coverage.py JSON report (default: coverage.json)",
    )
    parser.add_argument(
        "--threshold",
        type=float,
        default=DEFAULT_THRESHOLD,
        help=f"Minimum percent for each metric (default: {DEFAULT_THRESHOLD})",
    )
    parser.add_argument(
        "--source",
        type=Path,
        default=DEFAULT_SOURCE,
        help="Package source tree for function-coverage analysis",
    )
    parser.add_argument("-v", "--verbose", action="store_true")
    return parser.parse_args(argv)


def _function_coverage(report: dict[str, Any], source: Path) -> tuple[float, int, int]:
    """Return (percent, covered, total) function coverage from executed lines."""
    measured: dict[Path, set[int]] = {}
    for file_path, info in report.get("files", {}).items():
        executed = set(info.get("executed_lines", []))
        measured[Path(file_path).resolve()] = executed

    total = 0
    covered = 0
    missing: list[str] = []
    if not source.is_dir():
        raise FileNotFoundError(f"Source tree not found: {source}")

    for path in sorted(source.rglob("*.py")):
        tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
        executed = measured.get(path.resolve(), set())
        for node in ast.walk(tree):
            if not isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
                continue
            total += 1
            end = node.end_lineno or node.lineno
            lines = set(range(node.lineno, end + 1))
            if lines & executed:
                covered += 1
            else:
                missing.append(f"{path.name}:{node.name}:{node.lineno}")

    percent = 100.0 if total == 0 else (100.0 * covered / total)
    if missing:
        LOG.debug("Uncovered functions: %s", ", ".join(missing))
    return percent, covered, total


def evaluate(report: dict[str, Any], *, source: Path, threshold: float) -> int:
    totals = report["totals"]
    statement = float(totals["percent_statements_covered"])
    branch = float(totals["percent_branches_covered"])
    combined = float(totals["percent_covered"])
    function_pct, fn_covered, fn_total = _function_coverage(report, source)

    metrics = {
        "statement": statement,
        "branch": branch,
        "function": function_pct,
        "combined": combined,
    }
    LOG.info(
        "Coverage metrics: statement=%.2f%% branch=%.2f%% function=%.2f%% "
        "(%s/%s) combined=%.2f%% (threshold=%.2f%%)",
        statement,
        branch,
        function_pct,
        fn_covered,
        fn_total,
        combined,
        threshold,
    )

    failed = [name for name, value in metrics.items() if value < threshold]
    if failed:
        LOG.error(
            "Coverage below %.2f%% for: %s",
            threshold,
            ", ".join(f"{name}={metrics[name]:.2f}%" for name in failed),
        )
        return 1
    LOG.info("All coverage metrics meet threshold")
    return 0


def main(argv: list[str] | None = None) -> int:
    args = parse_args(argv)
    logging.basicConfig(
        level=logging.DEBUG if args.verbose else logging.INFO,
        format="[coverage-metrics] %(levelname)s: %(message)s",
        stream=sys.stderr,
    )
    if not args.json.is_file():
        LOG.error(
            "Coverage JSON not found: %s (run pytest with --cov-report=json)",
            args.json,
        )
        return 2
    report = json.loads(args.json.read_text(encoding="utf-8"))
    try:
        return evaluate(report, source=args.source, threshold=args.threshold)
    except FileNotFoundError as exc:
        LOG.error("%s", exc)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
