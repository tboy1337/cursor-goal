"""Tests for cursor_goal.cli and package entry points."""

from __future__ import annotations

import io
import os
import runpy
import subprocess
import sys
from contextlib import redirect_stderr, redirect_stdout
from pathlib import Path

import pytest

import cursor_goal.cli as cli_mod
from tests.conftest import run_cli


def test_cli_help_flags(goal_home: Path) -> None:
    for flag in ("-h", "--help", "help"):
        code, out, _err = run_cli(flag)
        assert code == 0
        assert "Usage:" in out


def test_cli_version(goal_home: Path) -> None:
    from cursor_goal import __version__

    for flag in ("-V", "--version", "version"):
        code, out, _err = run_cli(flag)
        assert code == 0
        assert __version__ in out


def test_cli_no_args(goal_home: Path) -> None:
    code, out, _err = run_cli()
    assert code == 1
    assert "Usage:" in out


def test_cli_unknown_command(goal_home: Path) -> None:
    code, out, err = run_cli("wat")
    assert code == 1
    assert "Unknown command" in err
    assert "Usage:" in out


def test_cli_keyboard_interrupt(
    goal_home: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    def boom(_argv: list[str]) -> int:
        raise KeyboardInterrupt

    monkeypatch.setattr(cli_mod, "cmd_parse", boom)
    code, _out, err = run_cli("parse", "x")
    assert code == 130
    assert "Interrupted" in err


def test_python_m_cursor_goal_help(goal_home: Path) -> None:
    env = os.environ.copy()
    env["CURSOR_GOAL_DATA"] = str(goal_home)
    completed = subprocess.run(
        [sys.executable, "-m", "cursor_goal", "--help"],
        check=False,
        capture_output=True,
        text=True,
        timeout=30,
        env=env,
    )
    assert completed.returncode == 0
    assert "Usage:" in completed.stdout


def test_package_main_module_coverage(
    goal_home: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Exercise ``python -m cursor_goal`` via runpy so coverage sees __main__."""
    monkeypatch.setattr(sys, "argv", ["cursor_goal", "--help"])
    out = io.StringIO()
    err = io.StringIO()
    with redirect_stdout(out), redirect_stderr(err):
        with pytest.raises(SystemExit) as exc:
            runpy.run_module("cursor_goal", run_name="__main__")
    assert exc.value.code == 0
    assert "Usage:" in out.getvalue()


def test_cli_module_main_guard(
    goal_home: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Exercise ``cursor_goal.cli`` ``__main__`` guard for coverage."""
    monkeypatch.setattr(sys, "argv", ["cli.py", "--help"])
    out = io.StringIO()
    err = io.StringIO()
    cli_path = Path(cli_mod.__file__).resolve()
    with redirect_stdout(out), redirect_stderr(err):
        with pytest.raises(SystemExit) as exc:
            runpy.run_path(str(cli_path), run_name="__main__")
    assert exc.value.code == 0
    assert "Usage:" in out.getvalue()
