#!/usr/bin/env python3
"""Local verification pipeline for the cursor-goal harness.

Runs isort/black/pyproject-fmt (or optional auto-fix), mypy, pylint, complexipy,
bandit, pip-audit, version-sync, plugin-tree-sync, pytest with multi-metric
coverage (>=95% statement/branch/function/combined), wake-smoke, ShellCheck,
install-smoke, and on Windows PSScriptAnalyzer + Pester (>=95% command coverage).

Usage:
  python3 scripts/verify.py          # Windows: py -3
  python3 scripts/verify.py --fix
  python3 scripts/verify.py --skip-format
  python3 scripts/verify.py --skip-shell
"""

from __future__ import annotations

import argparse
import importlib.util
import logging
import os
import shutil
import subprocess
import sys
import time
from collections.abc import Sequence
from dataclasses import dataclass
from pathlib import Path

LOG = logging.getLogger("verify")

SRC_PATHS = ("src", "tests", "scripts")
MYPY_TARGET = "src/cursor_goal"
PYLINT_TARGET = "src/cursor_goal"
PYTEST_TARGET = "tests"
PYPROJECT_TOML = "pyproject.toml"
COVERAGE_JSON = "coverage.json"
COVERAGE_CHECK = "scripts/check_coverage_metrics.py"
POWERSHELL_TESTS = "scripts/run-powershell-tests.ps1"
PIP_AUDIT_REQUIREMENTS = "scripts/pip-audit-requirements.txt"
REQUIRED_DEV_MODULES = (
    "pytest",
    "mypy",
    "pylint",
    "black",
    "isort",
    "pyproject_fmt",
    "bandit",
    "pip_audit",
)


@dataclass(frozen=True)
class StepResult:
    name: str
    command: tuple[str, ...]
    exit_code: int
    duration_sec: float

    @property
    def ok(self) -> bool:
        return self.exit_code == 0


def repo_root() -> Path:
    """Resolve repository root from this script location."""
    return Path(__file__).resolve().parent.parent


def setup_logging(*, verbose: bool) -> None:
    """Configure only the verify logger so importing tools cannot spoof [verify] ERROR."""
    level = logging.DEBUG if verbose else logging.INFO
    handler = logging.StreamHandler(sys.stderr)
    handler.setFormatter(logging.Formatter("[verify] %(levelname)s: %(message)s"))
    LOG.handlers.clear()
    LOG.addHandler(handler)
    LOG.setLevel(level)
    LOG.propagate = False
    LOG.debug(
        "verify logger isolated from root (level=%s)", logging.getLevelName(level)
    )


def missing_dev_modules(
    names: Sequence[str] = REQUIRED_DEV_MODULES,
) -> list[str]:
    """Return import names that are not installed, without executing the modules.

    ``__import__("bandit")`` loads optional formatters and logs ``sarif_om``
    errors; ``python -m pyproject_fmt`` after a prior import warns about
    ``sys.modules``. ``find_spec`` only checks install presence.
    """
    missing: list[str] = []
    for mod in names:
        spec = importlib.util.find_spec(mod)
        if spec is None:
            LOG.debug("dev module missing: %s", mod)
            missing.append(mod)
        else:
            LOG.debug("dev module present: %s", mod)
    LOG.debug(
        "dev module presence check complete missing=%s checked=%s",
        missing,
        list(names),
    )
    return missing


def _child_env() -> dict[str, str]:
    """Environment for pipeline subprocesses.

    Force UTF-8 on Windows so isort/black can read files that contain arrows
    or ≠ (cp1252 ``charmap`` otherwise skips them as unparseable).
    """
    env = os.environ.copy()
    env["PYTHONUTF8"] = "1"
    env["PYTHONIOENCODING"] = "utf-8"
    LOG.debug("child env forced PYTHONUTF8=1 PYTHONIOENCODING=utf-8")
    return env


def run_step(name: str, command: Sequence[str], *, cwd: Path) -> StepResult:
    display = " ".join(command)
    LOG.info("START %s: %s", name, display)
    started = time.perf_counter()
    completed = subprocess.run(
        list(command),
        cwd=cwd,
        check=False,
        env=_child_env(),
    )
    duration = time.perf_counter() - started
    result = StepResult(
        name=name,
        command=tuple(command),
        exit_code=completed.returncode,
        duration_sec=duration,
    )
    if result.ok:
        LOG.info("PASS  %s (%.2fs)", name, duration)
    else:
        LOG.error("FAIL  %s (exit %s, %.2fs)", name, result.exit_code, duration)
    return result


def isort_invocation(py: str) -> list[str]:
    """Return argv prefix for isort.

    isort 9 on CPython 3.14 installs a native ``isort/__init__.pyd``, so
    ``python -m isort`` fails with "isort is a package and cannot be directly
    executed". CI invokes the console script; do the same, with a
    ``isort.main`` fallback when Scripts/ is not on PATH.
    """
    found = shutil.which("isort")
    if found:
        LOG.debug("isort console script: %s", found)
        return [found]
    LOG.warning(
        "isort console script not on PATH; using isort.main (python -m isort "
        "is broken on isort 9 / CPython 3.14 native builds)"
    )
    return [
        py,
        "-c",
        "from isort.main import main; raise SystemExit(main())",
    ]


def pyproject_fmt_invocation(py: str) -> list[str]:
    """Return argv prefix for pyproject-fmt.

    ``python -m pyproject_fmt`` emits a RuntimeWarning because the package is
    already imported when ``-m`` runs. Prefer the console script.
    """
    found = shutil.which("pyproject-fmt")
    if found:
        LOG.debug("pyproject-fmt console script: %s", found)
        return [found]
    LOG.warning(
        "pyproject-fmt console script not on PATH; using python -m pyproject_fmt"
    )
    return [py, "-m", "pyproject_fmt"]


def bash_scripts(root: Path) -> list[Path]:
    """Return sorted bash scripts under scripts/ plus the skill tree's wake loop.

    The marketplace/plugin skill tree ships its own bash entry point
    (.cursor/skills/cursor-goal/scripts/wake_loop.sh) that CI lints alongside
    scripts/*.sh; include it here too so `verify.py` matches CI exactly.
    """
    scripts_dir = root / "scripts"
    found = list(scripts_dir.glob("*.sh")) + list(scripts_dir.glob("*.bash"))
    wake_loop_sh = (
        root / ".cursor" / "skills" / "cursor-goal" / "scripts" / "wake_loop.sh"
    )
    if wake_loop_sh.is_file():
        found.append(wake_loop_sh)
    plugin_wake = (
        root
        / "plugins"
        / "cursor-goal"
        / "skills"
        / "cursor-goal"
        / "scripts"
        / "wake_loop.sh"
    )
    if plugin_wake.is_file():
        found.append(plugin_wake)
    return sorted(path for path in found if path.is_file())


def build_steps(
    *,
    root: Path,
    fix: bool,
    skip_format: bool,
    skip_shell: bool,
) -> list[tuple[str, list[str]]]:
    py = sys.executable
    steps: list[tuple[str, list[str]]] = []

    if not skip_format:
        if fix:
            steps.append(("isort (fix)", [*isort_invocation(py), *SRC_PATHS]))
            steps.append(("black (fix)", [py, "-m", "black", *SRC_PATHS]))
            steps.append(
                (
                    "pyproject-fmt (fix)",
                    [*pyproject_fmt_invocation(py), PYPROJECT_TOML],
                )
            )
        else:
            steps.append(
                (
                    "isort (check)",
                    [
                        *isort_invocation(py),
                        "--check-only",
                        "--diff",
                        *SRC_PATHS,
                    ],
                )
            )
            steps.append(
                (
                    "black (check)",
                    [py, "-m", "black", "--check", "--diff", *SRC_PATHS],
                )
            )
            steps.append(
                (
                    "pyproject-fmt (check)",
                    [*pyproject_fmt_invocation(py), "--check", PYPROJECT_TOML],
                )
            )

    steps.append(("mypy", [py, "-m", "mypy", MYPY_TARGET]))
    steps.append(("pylint", [py, "-m", "pylint", PYLINT_TARGET]))
    complexipy = shutil.which("complexipy")
    if complexipy is None:
        LOG.error('complexipy not on PATH; install with: pip install -e ".[dev]"')
        steps.append(
            ("complexipy", [py, "-c", "raise SystemExit('complexipy missing')"])
        )
    else:
        steps.append(
            (
                "complexipy",
                [complexipy, MYPY_TARGET, "-mx", "15", "--quiet"],
            )
        )
    steps.append(
        (
            "bandit",
            [py, "-m", "bandit", "-r", MYPY_TARGET, "-c", "pyproject.toml", "-q"],
        )
    )
    steps.append(
        (
            "pip-audit",
            [
                py,
                "-m",
                "pip_audit",
                "-r",
                str(root / PIP_AUDIT_REQUIREMENTS),
                "--progress-spinner",
                "off",
            ],
        )
    )
    steps.append(
        (
            "version-sync",
            [py, str(root / "scripts" / "check_version_sync.py")],
        )
    )
    plugin_sync = root / "scripts" / "sync-plugin-tree.py"
    if plugin_sync.is_file():
        steps.append(
            (
                "plugin-tree-sync",
                [py, str(plugin_sync), "--check"],
            )
        )
    steps.append(
        (
            "pytest",
            [
                py,
                "-m",
                "pytest",
                PYTEST_TARGET,
                "-q",
                "--tb=short",
                f"--cov-report=json:{COVERAGE_JSON}",
            ],
        )
    )
    steps.append(
        (
            "coverage-metrics",
            [py, COVERAGE_CHECK, "--json", COVERAGE_JSON, "--threshold", "95"],
        )
    )
    wake_smoke = root / "scripts" / "wake-smoke.py"
    if wake_smoke.is_file():
        steps.append(
            (
                "wake-smoke",
                [py, str(wake_smoke)],
            )
        )

    if not skip_shell:
        shellcheck = shutil.which("shellcheck")
        scripts = bash_scripts(root)
        if shellcheck is None:
            # shellcheck-py installs a console script when .[dev] is installed.
            LOG.error(
                "shellcheck not on PATH; install with: pip install shellcheck-py "
                "(or ./scripts/run-shellcheck.sh)"
            )
            steps.append(
                ("shellcheck", [py, "-c", "raise SystemExit('shellcheck missing')"])
            )
        elif not scripts:
            LOG.warning("No bash scripts found under scripts/; skipping shellcheck")
        else:
            steps.append(
                (
                    "shellcheck",
                    [shellcheck, "--severity=warning", *[str(p) for p in scripts]],
                )
            )

        if sys.platform == "win32":
            powershell = shutil.which("powershell") or shutil.which("pwsh")
            if powershell is None:
                LOG.warning("powershell not found; skipping PSScriptAnalyzer/Pester")
            else:
                steps.append(
                    (
                        "powershell-tests",
                        [
                            powershell,
                            "-NoProfile",
                            "-ExecutionPolicy",
                            "Bypass",
                            "-File",
                            str(root / POWERSHELL_TESTS),
                        ],
                    )
                )
                smoke = root / "scripts" / "install-smoke.ps1"
                if smoke.is_file():
                    steps.append(
                        (
                            "install-smoke",
                            [
                                powershell,
                                "-NoProfile",
                                "-ExecutionPolicy",
                                "Bypass",
                                "-File",
                                str(smoke),
                            ],
                        )
                    )
        else:
            smoke_sh = root / "scripts" / "install-smoke.sh"
            if smoke_sh.is_file():
                steps.append(
                    (
                        "install-smoke",
                        ["bash", str(smoke_sh)],
                    )
                )

    return steps


def parse_args(argv: Sequence[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description=(
            "Run the local ship gate: isort, black, pyproject-fmt, mypy, pylint, "
            "complexipy, bandit, pip-audit, version-sync, plugin-tree-sync, "
            "pytest (+ multi-metric coverage), wake-smoke, shellcheck, "
            "install-smoke, and on Windows PSScriptAnalyzer/Pester."
        )
    )
    parser.add_argument(
        "--fix",
        action="store_true",
        help="Apply isort/black/pyproject-fmt fixes instead of check-only mode.",
    )
    parser.add_argument(
        "--skip-format",
        action="store_true",
        help="Skip isort, black, and pyproject-fmt steps.",
    )
    parser.add_argument(
        "--skip-shell",
        action="store_true",
        help="Skip ShellCheck, install-smoke, and on Windows PSScriptAnalyzer/Pester.",
    )
    parser.add_argument(
        "-v",
        "--verbose",
        action="store_true",
        help="Enable debug logging.",
    )
    return parser.parse_args(argv)


def main(argv: Sequence[str] | None = None) -> int:
    args = parse_args(argv)
    setup_logging(verbose=args.verbose)

    if args.fix and args.skip_format:
        LOG.error("Cannot combine --fix with --skip-format")
        return 2

    root = repo_root()
    LOG.info("Repository root: %s", root)
    LOG.info(
        "Mode: fix=%s skip_format=%s skip_shell=%s python=%s",
        args.fix,
        args.skip_format,
        args.skip_shell,
        sys.executable,
    )

    missing_mods = missing_dev_modules()
    LOG.debug("required dev modules missing=%s", missing_mods)
    if missing_mods:
        LOG.error(
            'Missing tools: %s. Install with: pip install -e ".[dev]"',
            ", ".join(missing_mods),
        )
        return 2

    steps = build_steps(
        root=root,
        fix=args.fix,
        skip_format=args.skip_format,
        skip_shell=args.skip_shell,
    )
    LOG.info("Planned steps: %s", ", ".join(name for name, _ in steps))

    results: list[StepResult] = []
    for name, command in steps:
        results.append(run_step(name, command, cwd=root))
        if not results[-1].ok:
            # Continue remaining steps so the developer sees the full picture.
            LOG.warning("Continuing after failure so remaining checks still run")

    failed = [r for r in results if not r.ok]
    LOG.info("----- summary -----")
    for result in results:
        status = "PASS" if result.ok else "FAIL"
        LOG.info(
            "%s  %-18s  %6.2fs  (exit %s)",
            status,
            result.name,
            result.duration_sec,
            result.exit_code,
        )

    if failed:
        LOG.error(
            "%s step(s) failed: %s", len(failed), ", ".join(r.name for r in failed)
        )
        return 1

    LOG.info("All verification steps passed")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
