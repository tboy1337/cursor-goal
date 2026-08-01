"""Tests for cursor_goal.paths harness resolution."""

from __future__ import annotations

from pathlib import Path

import pytest

from cursor_goal import paths as paths_mod


def test_skill_root_from_package_parent(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    skill = tmp_path / "goal"
    scripts = skill / "scripts"
    scripts.mkdir(parents=True)
    (scripts / "run_goal.py").write_text("#", encoding="utf-8")
    pkg = skill / "cursor_goal"
    pkg.mkdir()
    monkeypatch.delenv("CURSOR_GOAL_HOME", raising=False)
    monkeypatch.delenv("CURSOR_PLUGIN_ROOT", raising=False)
    monkeypatch.setattr(paths_mod, "_package_dir", lambda: pkg)
    assert paths_mod.skill_root() == skill
    assert paths_mod.run_goal_script() == scripts / "run_goal.py"
    inv = paths_mod.wake_loop_invocation()
    assert "wake" in inv and "loop" in inv


def test_skill_root_env_home_requires_absolute(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("CURSOR_GOAL_HOME", "relative/path")
    with pytest.raises(ValueError, match="absolute"):
        paths_mod.skill_root()

    abs_home = tmp_path / "custom"
    abs_home.mkdir()
    monkeypatch.setenv("CURSOR_GOAL_HOME", str(abs_home))
    assert paths_mod.skill_root() == abs_home


def test_harness_cmd_report_keys(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("CURSOR_GOAL_HOME", raising=False)
    report = paths_mod.harness_cmd_report()
    assert "run_goal" in report
    assert "wake_loop" in report
    assert "invocation" in report


def test_skill_root_from_plugin_env(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    plugin = tmp_path / "plugin"
    skill = plugin / "skills" / "goal"
    scripts = skill / "scripts"
    scripts.mkdir(parents=True)
    (scripts / "run_goal.py").write_text("#", encoding="utf-8")
    pkg = tmp_path / "other" / "cursor_goal"
    pkg.mkdir(parents=True)
    monkeypatch.delenv("CURSOR_GOAL_HOME", raising=False)
    monkeypatch.setenv("CURSOR_PLUGIN_ROOT", str(plugin))
    monkeypatch.setattr(paths_mod, "_package_dir", lambda: pkg)
    assert paths_mod.skill_root() == skill


def test_paths_posix_invocation_branches(monkeypatch: pytest.MonkeyPatch) -> None:
    """Cover non-Windows branches without constructing Path after os.name patch."""
    existing = Path("run_goal.py")
    monkeypatch.setattr(paths_mod.os, "name", "posix")
    assert paths_mod.python_invocation() == ["python3", "-u"]
    quoted = paths_mod.quote_for_shell(existing)
    assert "run_goal.py" in quoted
    monkeypatch.setattr(paths_mod, "run_goal_script", lambda: existing.resolve())
    inv = paths_mod.run_goal_invocation("wake", "loop")
    assert inv.startswith("python3")
    assert "wake" in inv


def test_wake_hint_value_error(monkeypatch: pytest.MonkeyPatch) -> None:
    from cursor_goal import manage as manage_mod

    def boom() -> str:
        raise ValueError("bad home")

    monkeypatch.setattr(manage_mod, "wake_loop_invocation", boom)
    hint = manage_mod._wake_loop_shell_hint()
    assert "unresolved" in hint


def test_harness_cmd_error(monkeypatch: pytest.MonkeyPatch) -> None:
    from cursor_goal import manage as manage_mod
    from tests.conftest import run_cli

    def boom() -> dict[str, str]:
        raise ValueError("bad home")

    monkeypatch.setattr(manage_mod, "harness_cmd_report", boom)
    code, _out, err = run_cli("manage", "harness-cmd")
    assert code == 1
    assert "bad home" in err
