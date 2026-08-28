"""Shared pytest fixtures for cursor-goal harness tests."""

from __future__ import annotations

import io
import json
from contextlib import redirect_stderr, redirect_stdout
from pathlib import Path

import pytest

from cursor_goal import __version__
from cursor_goal.cli import main


@pytest.fixture()
def goal_home(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    """Isolated CURSOR_GOAL_DATA for unit tests.

    Defaults disable ACL harden and workdir jail for ephemeral tmp dirs.
    Security-sensitive tests MUST ``delenv`` ``CURSOR_GOAL_SKIP_ACL`` and/or
    ``CURSOR_GOAL_ALLOW_ANY_WORKDIR`` (see ``tests/test_hardening.py``) so new
    refuse gates are exercised under production knobs.

    Sets ``CURSOR_GOAL_HOME`` to a temp skill tree with a matching VERSION so
    doctor does not hard-fail against a stale classic install under the real
    user profile (Windows ``expanduser`` ignores ``HOME``).
    """
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
    monkeypatch.delenv("CURSOR_GOAL_NATIVE", raising=False)
    # Allow tmp workdirs outside process cwd in unit tests.
    monkeypatch.setenv("CURSOR_GOAL_ALLOW_ANY_WORKDIR", "1")
    (tmp_path / "home").mkdir()
    skill = tmp_path / "skill"
    scripts = skill / "scripts"
    scripts.mkdir(parents=True)
    (scripts / "run_goal.py").write_text("# test harness stub\n", encoding="utf-8")
    (skill / "VERSION").write_text(f"{__version__}\n", encoding="utf-8")
    monkeypatch.setenv("CURSOR_GOAL_HOME", str(skill))
    # Windows expanduser ignores HOME — keep doctor hooks/marketplace scans isolated.
    from cursor_goal import doctor as doctor_mod

    monkeypatch.setattr(doctor_mod, "_user_home", lambda: tmp_path / "home")
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


def write_explored_tree(root: Path) -> list[Path]:
    """Create six files in three directories for broad CLEAR evidence."""
    rels = [
        Path("src") / "a.py",
        Path("src") / "b.py",
        Path("ci") / "c.yml",
        Path("ci") / "d.yml",
        Path("docs") / "e.md",
        Path("docs") / "f.md",
    ]
    files: list[Path] = []
    for rel in rels:
        path = root / rel
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text("x\n", encoding="utf-8")
        files.append(path)
    return files


def explored_clear_text(
    files: list[Path],
    *,
    root: Path,
    reason: str = "nothing remains",
) -> str:
    """Auditor body with EXPLORED cites plus a CLEAR verdict line."""
    rels = [path.relative_to(root).as_posix() for path in files]
    listed = "\n".join(f"- {rel}" for rel in rels)
    return f"EXPLORED:\n{listed}\nCLEAR: {reason}\n"
