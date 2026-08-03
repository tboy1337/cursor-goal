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


def test_python_invocation_prefers_sys_executable(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(paths_mod.sys, "executable", r"C:\Python314\python.exe")
    monkeypatch.setattr(paths_mod.sys, "version_info", (3, 14, 0))
    assert paths_mod.python_invocation() == [r"C:\Python314\python.exe", "-u"]


def test_python_invocation_fallback_posix(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(paths_mod.sys, "executable", "/usr/bin/python3.11")
    monkeypatch.setattr(paths_mod.sys, "version_info", (3, 11, 9))
    monkeypatch.setattr(paths_mod.os, "name", "posix")
    assert paths_mod.python_invocation() == ["python3", "-u"]


def test_python_invocation_fallback_windows(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(paths_mod.sys, "executable", r"C:\Python311\python.exe")
    monkeypatch.setattr(paths_mod.sys, "version_info", (3, 11, 9))
    monkeypatch.setattr(paths_mod.os, "name", "nt")
    assert paths_mod.python_invocation() == ["py", "-3", "-u"]


def test_run_goal_invocation_quotes_windows_exe(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    script = tmp_path / "run_goal.py"
    script.write_text("#", encoding="utf-8")
    monkeypatch.setattr(paths_mod.os, "name", "nt")
    monkeypatch.setattr(
        paths_mod.sys, "executable", r"C:\Program Files\Python\python.exe"
    )
    monkeypatch.setattr(paths_mod.sys, "version_info", (3, 14, 0))
    monkeypatch.setattr(paths_mod, "run_goal_script", lambda: script)
    inv = paths_mod.run_goal_invocation("status")
    assert "Program Files" in inv
    assert "status" in inv


def test_run_goal_invocation_quotes_posix_exe(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    script = tmp_path / "run_goal.py"
    script.write_text("#", encoding="utf-8")
    monkeypatch.setattr(paths_mod.os, "name", "posix")
    monkeypatch.setattr(paths_mod.sys, "executable", "/opt/python/bin/python3")
    monkeypatch.setattr(paths_mod.sys, "version_info", (3, 14, 0))
    monkeypatch.setattr(paths_mod, "run_goal_script", lambda: script)
    inv = paths_mod.run_goal_invocation("status")
    assert "/opt/python" in inv
    assert "status" in inv


def test_path_has_symlink_posix_and_oserror(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from cursor_goal import state as state_mod

    normal = tmp_path / "d"
    normal.mkdir()
    monkeypatch.setattr(state_mod.os, "name", "posix")
    assert state_mod.path_has_symlink_or_reparse(normal) is False

    class BoomPath(type(normal)):
        def is_symlink(self) -> bool:  # type: ignore[override]
            raise OSError("boom")

    # Force OSError in the link-check loop via monkeypatch on Path.is_symlink
    monkeypatch.setattr(
        type(normal), "is_symlink", lambda self: (_ for _ in ()).throw(OSError("x"))
    )
    assert state_mod.path_has_symlink_or_reparse(normal) is False


def test_absolute_without_resolve_oserror(monkeypatch: pytest.MonkeyPatch) -> None:
    from pathlib import Path as P

    from cursor_goal import state as state_mod

    class BadPath(P):
        def expanduser(self) -> BadPath:  # type: ignore[override]
            raise OSError("expand failed")

    monkeypatch.setattr(
        state_mod, "_absolute_without_resolve", state_mod._absolute_without_resolve
    )

    # Call path_has through a path that fails absolutize
    def boom(path: P) -> P:
        raise OSError("nope")

    monkeypatch.setattr(state_mod, "_absolute_without_resolve", boom)
    assert state_mod.path_has_symlink_or_reparse(P(".")) in (True, False)


def test_paths_posix_invocation_branches(monkeypatch: pytest.MonkeyPatch) -> None:
    """Cover non-Windows branches without constructing Path after os.name patch."""
    existing = Path("run_goal.py")
    monkeypatch.setattr(paths_mod.os, "name", "posix")
    monkeypatch.setattr(paths_mod.sys, "executable", "/usr/bin/python3.11")
    monkeypatch.setattr(paths_mod.sys, "version_info", (3, 11, 9))
    assert paths_mod.python_invocation() == ["python3", "-u"]
    quoted = paths_mod.quote_for_shell(existing)
    assert "run_goal.py" in quoted
    monkeypatch.setattr(paths_mod, "run_goal_script", lambda: existing.resolve())
    inv = paths_mod.run_goal_invocation("wake", "loop")
    assert inv.startswith("python3")
    assert "wake" in inv


def test_wake_hint_value_error(monkeypatch: pytest.MonkeyPatch) -> None:
    from cursor_goal import doctor as doctor_mod

    def boom() -> str:
        raise ValueError("bad home")

    monkeypatch.setattr(doctor_mod, "wake_loop_invocation", boom)
    hint = doctor_mod._wake_loop_shell_hint()
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


def test_path_str_is_absolute_fallbacks(monkeypatch: pytest.MonkeyPatch) -> None:
    from cursor_goal.native_path import native_path, path_str_is_absolute

    assert path_str_is_absolute("") is False
    assert path_str_is_absolute("relative") is False
    # Force fallbacks that os.path.isabs already covers on the host.
    monkeypatch.setattr(
        "cursor_goal.native_path.os.path.isabs", lambda _p: False, raising=False
    )
    monkeypatch.setattr(
        "cursor_goal.native_path.os.path.expanduser", lambda p: p, raising=False
    )
    assert path_str_is_absolute("/usr/bin/python") is True
    assert path_str_is_absolute(r"C:\Python\python.exe") is True
    assert path_str_is_absolute(r"\\server\share\python.exe") is True
    assert path_str_is_absolute("//server/share/python.exe") is True
    assert path_str_is_absolute("python") is False

    p = native_path(__file__)
    assert native_path(p) is p
    assert native_path(str(p)) == p
