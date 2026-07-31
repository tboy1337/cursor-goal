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
    # Keep stop-hook tests fast; production default remains 100ms drain.
    monkeypatch.setenv("CURSOR_GOAL_STOP_DRAIN_MS", "0")
    (tmp_path / "home").mkdir()
    return data


def run_cli(*args: str) -> tuple[int, str, str]:
    out = io.StringIO()
    err = io.StringIO()
    with redirect_stdout(out), redirect_stderr(err):
        code = main(list(args))
    return code, out.getvalue(), err.getvalue()


def load_goal_json(data_dir: Path) -> dict:
    return json.loads((data_dir / "goal.json").read_text(encoding="utf-8"))
