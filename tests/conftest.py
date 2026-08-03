"""Shared pytest fixtures for cursor-goal harness tests."""

from __future__ import annotations

import io
import json
from contextlib import redirect_stderr, redirect_stdout
from pathlib import Path

import pytest

from cursor_goal.cli import main


@pytest.fixture()
def goal_home(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    data = tmp_path / "data"
    data.mkdir()
    monkeypatch.setenv("CURSOR_GOAL_DATA", str(data))
    monkeypatch.setenv("HOME", str(tmp_path / "home"))
    # Keep stop-hook tests fast; production defaults are ~100ms (Unix) / ~250ms (Windows).
    monkeypatch.setenv("CURSOR_GOAL_STOP_DRAIN_MS", "0")
    # Keep wake arming out of unrelated manage tests unless a test opts in.
    monkeypatch.setenv("CURSOR_GOAL_WAKE", "0")
    # Avoid icacls on ephemeral pytest dirs (can race / lock out writers).
    monkeypatch.setenv("CURSOR_GOAL_SKIP_ACL", "1")
    # Allow tmp workdirs outside process cwd in unit tests.
    monkeypatch.setenv("CURSOR_GOAL_ALLOW_ANY_WORKDIR", "1")
    (tmp_path / "home").mkdir()
    return data


def run_cli(*args: str) -> tuple[int, str, str]:
    out = io.StringIO()
    err = io.StringIO()
    with redirect_stdout(out), redirect_stderr(err):
        code = main(list(args))
    return code, out.getvalue(), err.getvalue()


def run_cli_stdin(stdin_text: str, *args: str) -> tuple[int, str, str]:
    """Like run_cli but feeds *stdin_text* to sys.stdin for --stdin commands."""
    import sys
    from unittest.mock import patch

    out = io.StringIO()
    err = io.StringIO()
    with (
        patch.object(sys, "stdin", io.StringIO(stdin_text)),
        redirect_stdout(out),
        redirect_stderr(err),
    ):
        code = main(list(args))
    return code, out.getvalue(), err.getvalue()


def load_goal_json(data_dir: Path) -> dict:
    return json.loads((data_dir / "goal.json").read_text(encoding="utf-8"))
