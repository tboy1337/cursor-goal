"""Tests for scripts/check_coverage_metrics.py."""

from __future__ import annotations

import importlib.util
import json
import sys
from pathlib import Path
from types import ModuleType

import pytest

REPO_ROOT = Path(__file__).resolve().parents[1]
MODULE_PATH = REPO_ROOT / "scripts" / "check_coverage_metrics.py"


def _load_module() -> ModuleType:
    spec = importlib.util.spec_from_file_location("check_coverage_metrics", MODULE_PATH)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    sys.modules["check_coverage_metrics"] = module
    spec.loader.exec_module(module)
    return module


@pytest.fixture(scope="module")
def ccm() -> ModuleType:
    return _load_module()


def _temp_pkg_report(
    tmp_path: Path,
    *,
    statements: float = 100.0,
    branches: float = 100.0,
    combined: float = 100.0,
    execute_all: bool = True,
) -> tuple[dict, Path]:
    """Create a one-file package and a matching coverage report."""
    pkg = tmp_path / "pkg"
    pkg.mkdir()
    mod = pkg / "mod.py"
    mod.write_text(
        "def alpha():\n    return 1\n\ndef beta():\n    return 2\n",
        encoding="utf-8",
    )
    executed = list(range(1, 6)) if execute_all else [1, 2]
    report = {
        "totals": {
            "percent_statements_covered": statements,
            "percent_branches_covered": branches,
            "percent_covered": combined,
        },
        "files": {
            str(mod.resolve()): {
                "executed_lines": executed,
                "summary": {},
            }
        },
    }
    return report, pkg


def test_parse_args_defaults(ccm: ModuleType) -> None:
    args = ccm.parse_args([])
    assert args.threshold == 95.0
    assert args.json.name == "coverage.json"


def test_evaluate_passes_when_all_metrics_meet_threshold(
    ccm: ModuleType, tmp_path: Path
) -> None:
    report, pkg = _temp_pkg_report(tmp_path)
    assert ccm.evaluate(report, source=pkg, threshold=95.0) == 0


def test_evaluate_fails_when_branch_below_threshold(
    ccm: ModuleType, tmp_path: Path
) -> None:
    report, pkg = _temp_pkg_report(tmp_path, branches=90.0, combined=96.0)
    assert ccm.evaluate(report, source=pkg, threshold=95.0) == 1


def test_evaluate_fails_when_function_coverage_low(
    ccm: ModuleType, tmp_path: Path
) -> None:
    report, pkg = _temp_pkg_report(tmp_path, execute_all=False)
    assert ccm.evaluate(report, source=pkg, threshold=95.0) == 1


def test_main_missing_json(ccm: ModuleType, tmp_path: Path) -> None:
    missing = tmp_path / "nope.json"
    assert ccm.main(["--json", str(missing)]) == 2


def test_main_reads_json_and_passes(ccm: ModuleType, tmp_path: Path) -> None:
    report, pkg = _temp_pkg_report(tmp_path)
    path = tmp_path / "coverage.json"
    path.write_text(json.dumps(report), encoding="utf-8")
    assert (
        ccm.main(
            [
                "--json",
                str(path),
                "--threshold",
                "95",
                "--source",
                str(pkg),
            ]
        )
        == 0
    )


def test_main_source_missing(ccm: ModuleType, tmp_path: Path) -> None:
    report, _pkg = _temp_pkg_report(tmp_path)
    path = tmp_path / "coverage.json"
    path.write_text(json.dumps(report), encoding="utf-8")
    missing_src = tmp_path / "no-such-pkg"
    assert (
        ccm.main(
            ["--json", str(path), "--source", str(missing_src), "--threshold", "95"]
        )
        == 2
    )
