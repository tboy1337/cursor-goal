"""Extra coverage for production-hardening paths."""

from __future__ import annotations

import io
import json
import sys
from pathlib import Path
from unittest.mock import patch

import pytest
from pytest_mock import MockerFixture

from cursor_goal import hooks_config as hooks_mod
from cursor_goal import win_acl
from cursor_goal.hooks_config import write_hooks_file
from cursor_goal.state import (
    GoalLockTimeoutError,
    GoalState,
    _apply_field,
    load_goal,
    save_goal,
    update_goal_fields,
)
from cursor_goal.stop import _drain_ms
from cursor_goal.validation import redact_command, run_validation
from tests.conftest import run_cli


def test_apply_field_variants(goal_home: Path) -> None:
    state = GoalState(condition="c", created_at="t", status="pursuing")
    _apply_field(state, "condition", "new")
    _apply_field(state, "validation_command", None)
    _apply_field(state, "created_at", "t2")
    _apply_field(state, "turn_budget", 10)
    _apply_field(state, "turns_used", 2)
    _apply_field(state, "status", "paused")
    _apply_field(state, "last_reason", None)
    _apply_field(state, "last_validation_output", None)
    _apply_field(state, "last_validation_exit_code", None)
    _apply_field(state, "last_validation_exit_code", "")
    _apply_field(state, "last_validation_exit_code", 3)
    _apply_field(state, "last_eval_verdict", None)
    _apply_field(state, "last_audit_verdict", None)
    _apply_field(state, "last_block_reason", None)
    _apply_field(state, "block_streak", 2)
    _apply_field(state, "last_block_turn_key", "1:0")
    _apply_field(state, "condition_updated_pending", True)
    _apply_field(state, "status", "blocked")
    _apply_field(state, "active", False)
    assert state.active is False
    assert state.condition_updated_pending is True
    assert state.block_streak == 2
    with pytest.raises(ValueError, match="turns_used"):
        _apply_field(state, "turns_used", -1)
    with pytest.raises(ValueError, match="unknown"):
        _apply_field(state, "not_a_field", 1)
    with pytest.raises(ValueError, match="status"):
        _apply_field(state, "status", "bogus")
    with pytest.raises(ValueError, match="block_streak"):
        _apply_field(state, "block_streak", -1)
    with pytest.raises(ValueError, match="condition_updated_pending"):
        _apply_field(state, "condition_updated_pending", "yes")


def test_load_goal_negative_turns(goal_home: Path) -> None:
    (goal_home / "goal.json").write_text(
        json.dumps(
            {
                "condition": "c",
                "turn_budget": 5,
                "turns_used": -1,
                "status": "pursuing",
                "active": True,
                "schema_version": 1,
            }
        ),
        encoding="utf-8",
    )
    assert load_goal() is None


def test_windows_lock_timeout_message(monkeypatch: pytest.MonkeyPatch) -> None:
    from cursor_goal import fs_lock as fs_lock_mod
    from cursor_goal import state as state_mod

    class Handle:
        def fileno(self) -> int:
            return 3

        def seek(self, *_a: object, **_k: object) -> None:
            return None

        def tell(self) -> int:
            return 1

        def write(self, *_a: object, **_k: object) -> int:
            return 1

        def flush(self) -> None:
            return None

    def boom(*_a: object, **_k: object) -> None:
        raise OSError("lock busy")

    fake_msvcrt = type(sys)("msvcrt")
    fake_msvcrt.locking = boom  # type: ignore[attr-defined]
    fake_msvcrt.LK_LOCK = 1  # type: ignore[attr-defined]
    fake_msvcrt.LK_NBLCK = 2  # type: ignore[attr-defined]
    monkeypatch.setitem(sys.modules, "msvcrt", fake_msvcrt)
    monkeypatch.setattr(sys, "platform", "win32")
    monkeypatch.setattr(state_mod, "LOCK_TIMEOUT_SEC", 0.05)
    with pytest.raises(GoalLockTimeoutError, match="goal.lock"):
        state_mod._lock_acquire(Handle())
    # Ensure fs_lock path is what raised.
    assert fs_lock_mod.GoalLockTimeoutError is GoalLockTimeoutError


def test_save_goal_tmp_cleanup_on_replace_fail(
    goal_home: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from cursor_goal import state as state_mod

    state = GoalState(condition="c", created_at="t", status="pursuing")
    real_replace = Path.replace

    def boom_replace(self: Path, target: Path) -> None:
        raise OSError("replace failed")

    monkeypatch.setattr(Path, "replace", boom_replace)
    with pytest.raises(OSError):
        state_mod._save_goal_unlocked(state)
    leftovers = list(goal_home.glob("goal.*.tmp"))
    assert leftovers == []
    monkeypatch.setattr(Path, "replace", real_replace)


def test_write_hooks_tmp_cleanup(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    path = tmp_path / "hooks.json"

    def boom_replace(self: Path, target: Path) -> None:
        raise OSError("nope")

    monkeypatch.setattr(Path, "replace", boom_replace)
    with pytest.raises(OSError):
        write_hooks_file(path, {"version": 1, "hooks": {"stop": []}})
    assert list(tmp_path.glob("hooks.json.*.tmp")) == []


def test_drain_ms_negative(goal_home: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("CURSOR_GOAL_STOP_DRAIN_MS", "-5")
    assert _drain_ms() == 0


def test_redact_bearer_and_log_secrets(monkeypatch: pytest.MonkeyPatch) -> None:
    assert "<redacted>" in redact_command("Authorization Bearer tokensecret")
    assert "<redacted>" in redact_command("export client_secret=abc123")
    assert "<redacted>" in redact_command("AKIAIOSFODNN7EXAMPLE")
    monkeypatch.setenv("CURSOR_GOAL_LOG_SECRETS", "1")
    result = run_validation(f"{sys.executable} -c print(1)")
    assert result.exit_code == 0


def test_manage_create_refuses_insecure(
    goal_home: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from cursor_goal import manage as manage_mod

    monkeypatch.setattr(
        manage_mod,
        "refuse_if_data_dir_insecure",
        lambda: "[goal] Error: data directory is group/world-writable (/tmp)",
    )
    code, _out, err = run_cli("manage", "create", "x")
    assert code == 1
    assert "writable" in err


def test_eval_validate_refuses_insecure(
    goal_home: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from cursor_goal import evaluate as evaluate_mod

    run_cli("manage", "create", "g", "--test", "echo")
    monkeypatch.setattr(
        evaluate_mod,
        "refuse_if_data_dir_insecure",
        lambda: "[goal] Error: data directory is group/world-writable (/tmp)",
    )
    code, _out, err = run_cli("eval", "validate")
    assert code == 1
    assert "writable" in err
    assert evaluate_mod.cmd_validate([]) == 1


def test_eval_signal_refuses_insecure(
    goal_home: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from cursor_goal import evaluate as evaluate_mod

    run_cli("manage", "create", "g")
    monkeypatch.setattr(
        evaluate_mod,
        "refuse_if_data_dir_insecure",
        lambda: "[goal] Error: data directory is group/world-writable (/tmp)",
    )
    code, _out, err = run_cli("eval", "signal", "--force")
    assert code == 1
    assert "writable" in err
    assert evaluate_mod.cmd_signal(["--force"]) == 1


def test_eval_parse_result_refuses_insecure(
    goal_home: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from cursor_goal import evaluate as evaluate_mod

    run_cli("manage", "create", "g")
    monkeypatch.setattr(
        evaluate_mod,
        "refuse_if_data_dir_insecure",
        lambda: "[goal] Error: data directory is group/world-writable (/tmp)",
    )
    code, _out, err = run_cli("eval", "parse-result", "YES: ok")
    assert code == 1
    assert "writable" in err
    assert evaluate_mod.cmd_parse_result(["YES: ok"]) == 1


def test_eval_parse_audit_refuses_insecure(
    goal_home: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from cursor_goal import evaluate as evaluate_mod

    run_cli("manage", "create", "g")
    monkeypatch.setattr(
        evaluate_mod,
        "refuse_if_data_dir_insecure",
        lambda: "[goal] Error: data directory is group/world-writable (/tmp)",
    )
    code, _out, err = run_cli("eval", "parse-audit", "CLEAR: ok")
    assert code == 1
    assert "writable" in err
    assert evaluate_mod.cmd_parse_audit(["CLEAR: ok"]) == 1


@pytest.mark.parametrize(
    "eval_args",
    [
        ("prompt",),
        ("audit-prompt",),
        ("check",),
        ("spawn-config",),
        ("audit-spawn-config",),
    ],
)
def test_eval_read_paths_refuse_insecure(
    goal_home: Path, mocker: MockerFixture, eval_args: tuple[str, ...]
) -> None:
    from cursor_goal import evaluate as evaluate_mod

    run_cli("manage", "create", "g")
    mocker.patch.object(
        evaluate_mod,
        "refuse_if_data_dir_insecure",
        return_value="[goal] Error: data directory is group/world-writable (/tmp)",
    )
    code, _out, err = run_cli("eval", *eval_args)
    assert code == 1
    assert "writable" in err


def test_eval_help_skips_insecure_refuse(
    goal_home: Path, mocker: MockerFixture
) -> None:
    from cursor_goal import evaluate as evaluate_mod

    mocker.patch.object(
        evaluate_mod,
        "refuse_if_data_dir_insecure",
        return_value="[goal] Error: data directory is group/world-writable (/tmp)",
    )
    code, out, err = run_cli("eval", "help")
    assert code == 0
    assert "Usage:" in out
    assert "writable" not in err


def test_eval_unknown_command_before_refuse(
    goal_home: Path, mocker: MockerFixture
) -> None:
    from cursor_goal import evaluate as evaluate_mod

    mocker.patch.object(
        evaluate_mod,
        "refuse_if_data_dir_insecure",
        return_value="[goal] Error: data directory is group/world-writable (/tmp)",
    )
    code, _out, err = run_cli("eval", "not-a-command")
    assert code == 1
    assert "unknown" in err.lower()
    assert "writable" not in err


def test_eval_validate_timeout(
    goal_home: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from cursor_goal import evaluate as evaluate_mod
    from cursor_goal.validation import ValidationResult

    run_cli("manage", "create", "g", "--test", "echo")
    monkeypatch.setattr(
        evaluate_mod,
        "run_validation",
        lambda *_a, **_k: ValidationResult(
            exit_code=124, output="slow", timed_out=True
        ),
    )
    code, out, _err = run_cli("eval", "validate")
    assert code == 1
    assert "timed out" in out.lower()


def test_eval_parse_result_stdin_empty_usage(goal_home: Path) -> None:
    code, _out, err = run_cli("eval", "parse-result")
    assert code == 1
    assert "Usage" in err


def test_eval_parse_result_at_missing_file(goal_home: Path) -> None:
    missing = goal_home / "no-such-file.txt"
    code, _out, err = run_cli("eval", "parse-result", f"@{missing}")
    assert code == 1
    assert "could not read" in err.lower() or "Error" in err


def test_eval_parse_result_stdin_oserror(
    goal_home: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from cursor_goal import evaluate as evaluate_mod

    class Boom:
        def read(self, *_a: object, **_k: object) -> str:
            raise OSError("stdin broke")

    monkeypatch.setattr(sys, "stdin", Boom())
    code = evaluate_mod.cmd_parse_result(["--stdin"])
    assert code == 1


def test_manage_lock_timeout_on_create(
    goal_home: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from cursor_goal import manage as manage_mod

    def boom(*_a: object, **_k: object) -> tuple[None, str]:
        raise GoalLockTimeoutError("locked")

    monkeypatch.setattr(manage_mod, "create_goal_atomic", boom)
    code, _out, err = run_cli("manage", "create", "x")
    assert code == 1
    assert "locked" in err


def test_update_goal_fields_typed(goal_home: Path) -> None:
    save_goal(GoalState(condition="c", created_at="t", status="pursuing"))
    updated = update_goal_fields(turns_used=3, last_reason="r")
    assert updated is not None
    assert updated.turns_used == 3


def test_parse_result_oversize_stdin(
    goal_home: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from cursor_goal import evaluate as evaluate_mod

    monkeypatch.setattr(evaluate_mod, "MAX_PARSE_RESULT_BYTES", 8)
    with patch.object(sys, "stdin", io.StringIO("x" * 20)):
        assert evaluate_mod.cmd_parse_result(["--stdin"]) == 1


def test_parse_result_oversize_file(goal_home: Path) -> None:
    from cursor_goal import evaluate as evaluate_mod

    path = goal_home / "big.txt"
    path.write_text("y" * 100, encoding="utf-8")
    with patch.object(evaluate_mod, "MAX_PARSE_RESULT_BYTES", 8):
        assert evaluate_mod.cmd_parse_result([f"@{path}"]) == 1


def test_parse_result_oversize_argv(
    goal_home: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from cursor_goal import evaluate as evaluate_mod

    monkeypatch.setattr(evaluate_mod, "MAX_PARSE_RESULT_BYTES", 8)
    code, _out, err = run_cli("eval", "parse-result", "x" * 20)
    assert code == 1
    assert "exceeds" in err


def test_parse_audit_oversize_argv(
    goal_home: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from cursor_goal import evaluate as evaluate_mod

    monkeypatch.setattr(evaluate_mod, "MAX_PARSE_RESULT_BYTES", 8)
    code, _out, err = run_cli("eval", "parse-audit", "x" * 20)
    assert code == 1
    assert "exceeds" in err


def test_eval_validate_no_goal(goal_home: Path) -> None:
    code, _out, err = run_cli("eval", "validate")
    assert code == 1
    assert "No active goal" in err


def test_eval_validate_persist_fail(
    goal_home: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from cursor_goal import evaluate as evaluate_mod
    from cursor_goal.validation import ValidationResult

    run_cli("manage", "create", "g", "--test", "echo")
    monkeypatch.setattr(
        evaluate_mod,
        "run_validation",
        lambda *_a, **_k: ValidationResult(exit_code=0, output="ok"),
    )
    monkeypatch.setattr(evaluate_mod, "update_goal_fields", lambda **_k: None)
    code, _out, err = run_cli("eval", "validate")
    assert code == 1
    assert "persist" in err.lower()


def test_eval_signal_lock_timeout(
    goal_home: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from cursor_goal import evaluate as evaluate_mod

    run_cli("manage", "create", "g")
    run_cli("eval", "parse-result", "YES: ok")

    def boom(**_k: object) -> None:
        raise GoalLockTimeoutError("locked")

    monkeypatch.setattr(evaluate_mod, "set_eval_signal", boom)
    # Force path: last_eval_verdict already YES
    code, _out, err = run_cli("eval", "signal")
    assert code == 1
    assert "locked" in err


def test_manage_lock_timeouts(goal_home: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    from cursor_goal import manage as manage_mod

    run_cli("manage", "create", "g")

    def boom_mutate(_m: object) -> None:
        raise GoalLockTimeoutError("locked")

    monkeypatch.setattr(manage_mod, "mutate_goal", boom_mutate)
    assert run_cli("manage", "pause")[0] == 1
    assert run_cli("manage", "resume")[0] == 1

    def boom_done(**_k: object) -> tuple[None, str]:
        raise GoalLockTimeoutError("locked")

    monkeypatch.setattr(manage_mod, "mark_goal_achieved", boom_done)
    assert run_cli("manage", "done", "--force")[0] == 1

    def boom_clear() -> bool:
        raise GoalLockTimeoutError("locked")

    monkeypatch.setattr(manage_mod, "clear_goal_files", boom_clear)
    assert run_cli("manage", "clear")[0] == 1


def test_manage_status_lock_timeout(
    goal_home: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from cursor_goal import manage as manage_mod

    def boom(**_k: object) -> None:
        raise GoalLockTimeoutError("locked")

    monkeypatch.setattr(manage_mod, "snapshot_goal", boom)
    code, _out, err = run_cli("manage", "status")
    assert code == 1
    assert "locked" in err


def test_create_rejects_long_validation_command(goal_home: Path) -> None:
    from cursor_goal.state import MAX_FIELD_CHARS

    long_cmd = "x" * (MAX_FIELD_CHARS + 1)
    code, _out, err = run_cli("manage", "create", "ok", "--test", long_cmd)
    assert code == 1
    assert "validation command exceeds" in err


def test_from_dict_clamps_oversized_condition(goal_home: Path) -> None:
    from cursor_goal.state import MAX_FIELD_CHARS, GoalState

    del goal_home
    state = GoalState.from_dict(
        {
            "active": True,
            "condition": "c" * (MAX_FIELD_CHARS + 50),
            "validation_command": "v" * (MAX_FIELD_CHARS + 10),
            "turn_budget": 5,
            "turns_used": 0,
            "status": "pursuing",
            "schema_version": 1,
        }
    )
    assert len(state.condition) == MAX_FIELD_CHARS
    assert len(state.validation_command) == MAX_FIELD_CHARS


def test_update_rejects_oversized_condition(goal_home: Path) -> None:
    from cursor_goal.state import (
        MAX_FIELD_CHARS,
        GoalState,
        save_goal,
        update_goal_fields,
    )

    save_goal(GoalState(condition="ok", created_at="t", status="pursuing"))
    with pytest.raises(ValueError, match="condition exceeds"):
        update_goal_fields(condition="c" * (MAX_FIELD_CHARS + 1))


def test_from_dict_clamps_turns_over_budget(goal_home: Path) -> None:
    from cursor_goal.state import GoalState

    state = GoalState.from_dict(
        {
            "active": True,
            "condition": "ok",
            "turn_budget": 3,
            "turns_used": 99,
            "wake_ticks": 0,
            "status": "pursuing",
            "schema_version": 1,
        }
    )
    assert state.turns_used == 3
    assert state.status == "budget-limited"
    assert state.active is False


def test_quarantine_corrupt_goal(goal_home: Path) -> None:
    (goal_home / "goal.json").write_text("{not-json", encoding="utf-8")
    assert load_goal() is None
    quarantined = list(goal_home.glob("goal.json.corrupt.*"))
    assert len(quarantined) == 1
    assert not (goal_home / "goal.json").exists()


def test_unix_lock_timeout_nonblocking(
    goal_home: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from cursor_goal import state as state_mod

    class Handle:
        def fileno(self) -> int:
            return 3

    def always_busy(*_a: object, **_k: object) -> None:
        raise BlockingIOError("busy")

    fake_fcntl = type(sys)("fcntl")
    fake_fcntl.flock = always_busy  # type: ignore[attr-defined]
    fake_fcntl.LOCK_EX = 2  # type: ignore[attr-defined]
    fake_fcntl.LOCK_NB = 4  # type: ignore[attr-defined]
    monkeypatch.setitem(sys.modules, "fcntl", fake_fcntl)
    monkeypatch.setattr(sys, "platform", "linux")
    monkeypatch.setattr(state_mod, "LOCK_TIMEOUT_SEC", 0.15)
    with pytest.raises(GoalLockTimeoutError, match="goal.lock"):
        state_mod._lock_acquire(Handle())


def test_data_dir_chmod_private(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    import os
    import stat as stat_mod

    if os.name == "nt":
        pytest.skip("Unix directory mode bits")
    from cursor_goal import state as state_mod

    data = tmp_path / "private-data"
    monkeypatch.setenv("CURSOR_GOAL_DATA", str(data))
    path = state_mod.data_dir()
    mode = path.stat().st_mode
    assert mode & stat_mod.S_IRWXU
    assert not (mode & (stat_mod.S_IRWXG | stat_mod.S_IRWXO))


def test_harden_windows_acl_cached(
    goal_home: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from cursor_goal import state as state_mod

    calls: list[list[str]] = []

    def fake_run(cmd: list[str], **_k: object) -> object:
        calls.append(list(cmd))

        class Result:
            returncode = 0
            stderr = ""
            stdout = ""

        return Result()

    monkeypatch.setattr(state_mod.os, "name", "nt")
    monkeypatch.setattr(win_acl.os, "name", "nt")
    monkeypatch.setattr(win_acl.subprocess, "run", fake_run)
    monkeypatch.setattr(
        win_acl, "_pinned_icacls", lambda: r"C:\Windows\System32\icacls.exe"
    )
    monkeypatch.setenv("USERNAME", "tester")
    monkeypatch.delenv("CURSOR_GOAL_SKIP_ACL", raising=False)
    state_mod._HARDENED_PATHS.clear()
    state_mod._ACL_HARDEN_FAILURES.clear()
    state_mod._harden_windows_acl(goal_home)
    state_mod._harden_windows_acl(goal_home)
    # First harden: inheritance strip + grant; second is cached (no more calls).
    assert len(calls) == 2
    assert any("/inheritance:r" in c for c in calls)
    assert any("/grant:r" in c for c in calls)
    assert state_mod.acl_harden_failure_message(goal_home) is None


def test_restore_windows_acl_inheritance_fail(
    goal_home: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from cursor_goal import win_acl

    def bad_run(*_a: object, **_k: object) -> object:
        class Result:
            returncode = 5
            stderr = "restore failed"
            stdout = ""

        return Result()

    monkeypatch.setattr(win_acl.subprocess, "run", bad_run)
    win_acl.restore_windows_acl_inheritance("icacls", goal_home)


def test_restore_windows_acl_inheritance_raises(
    goal_home: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from cursor_goal import win_acl

    def boom(*_a: object, **_k: object) -> object:
        raise OSError("icacls gone")

    monkeypatch.setattr(win_acl.subprocess, "run", boom)
    win_acl.restore_windows_acl_inheritance("icacls", goal_home)


def test_windows_username_rejects_metacharacters(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from cursor_goal import win_acl as win_acl_mod

    monkeypatch.setenv("USERNAME", "evil;user")
    monkeypatch.setenv("USER", "also*bad")
    monkeypatch.setattr(
        win_acl_mod.os, "getlogin", lambda: (_ for _ in ()).throw(OSError("no"))
    )
    monkeypatch.setattr(win_acl_mod, "_windows_logon_name", lambda: None)
    monkeypatch.setattr(win_acl_mod.sys, "platform", "linux")
    assert win_acl_mod.windows_username() is None


def test_parse_result_path_jail_rejects_outside(
    goal_home: Path, tmp_path: Path
) -> None:
    outside = tmp_path / "outside.txt"
    outside.write_text("YES: ok", encoding="utf-8")
    code, _out, err = run_cli("eval", "parse-result", f"@{outside}")
    assert code == 1
    assert "must be under" in err


def test_parse_result_allow_cwd(
    goal_home: Path, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    outside = tmp_path / "cwd_verdict.txt"
    outside.write_text("YES: ok via cwd\n", encoding="utf-8")
    monkeypatch.chdir(tmp_path)
    run_cli("manage", "create", "jail allow cwd")
    code, out, _err = run_cli("eval", "parse-result", f"@{outside}", "--allow-cwd")
    assert code == 0
    assert "VERDICT=YES" in out


def test_write_hooks_write_text_fail(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    path = tmp_path / "hooks.json"

    def boom_write(self: Path, *_a: object, **_k: object) -> None:
        raise OSError("disk full")

    def boom_open(*_a: object, **_k: object) -> int:
        raise OSError("disk full")

    monkeypatch.setattr(Path, "write_text", boom_write)
    monkeypatch.setattr(hooks_mod.os, "open", boom_open)
    with pytest.raises(OSError, match="disk full"):
        write_hooks_file(path, {"version": 1, "hooks": {"stop": []}})
    assert not path.exists()


def test_eval_parse_result_lock_timeout(
    goal_home: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from cursor_goal import evaluate as evaluate_mod

    run_cli("manage", "create", "g")

    def boom(*_a: object, **_k: object) -> None:
        raise GoalLockTimeoutError("locked")

    monkeypatch.setattr(evaluate_mod, "record_parse_result", boom)
    code, _out, err = run_cli("eval", "parse-result", "YES: x")
    assert code == 1
    assert "locked" in err


def test_harden_windows_acl_strip_then_grant(
    goal_home: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from cursor_goal import state as state_mod

    calls: list[list[str]] = []

    def fake_run(cmd: list[str], **_k: object) -> object:
        calls.append(list(cmd))

        class Result:
            returncode = 0
            stderr = ""
            stdout = ""

        return Result()

    monkeypatch.setattr(state_mod.os, "name", "nt")
    monkeypatch.setattr(win_acl.os, "name", "nt")
    monkeypatch.setattr(win_acl.subprocess, "run", fake_run)
    monkeypatch.setattr(
        win_acl, "_pinned_icacls", lambda: r"C:\Windows\System32\icacls.exe"
    )
    monkeypatch.setenv("USERNAME", "tester")
    monkeypatch.delenv("CURSOR_GOAL_SKIP_ACL", raising=False)
    state_mod._HARDENED_PATHS.clear()
    state_mod._ACL_HARDEN_FAILURES.clear()
    state_mod._harden_windows_acl(goal_home)
    assert len(calls) == 2
    assert calls[0][2] == "/inheritance:r"
    assert calls[1][2] == "/grant:r"
    assert str(goal_home) in state_mod._HARDENED_PATHS


def test_harden_windows_acl_skip_env(
    goal_home: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from cursor_goal import state as state_mod

    calls: list[object] = []

    def fake_run(*_a: object, **_k: object) -> object:
        calls.append(1)

        class Result:
            returncode = 0
            stderr = ""
            stdout = ""

        return Result()

    monkeypatch.setattr(state_mod.os, "name", "nt")
    monkeypatch.setattr(win_acl.os, "name", "nt")
    monkeypatch.setattr(win_acl.subprocess, "run", fake_run)
    monkeypatch.setenv("CURSOR_GOAL_SKIP_ACL", "1")
    state_mod._HARDENED_PATHS.clear()
    state_mod._ACL_HARDEN_FAILURES.clear()
    state_mod._harden_windows_acl(goal_home)
    assert calls == []


def test_harden_windows_acl_grant_fails_after_strip(
    goal_home: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from cursor_goal import state as state_mod

    n = {"i": 0}
    calls: list[list[str]] = []

    def fake_run(cmd: list[str], **_k: object) -> object:
        calls.append(list(cmd))
        n["i"] += 1

        class Result:
            # 1=strip ok, 2=grant fail, 3=inheritance restore
            returncode = 0 if n["i"] in {1, 3} else 5
            stderr = "grant failed" if n["i"] == 2 else ""
            stdout = ""

        return Result()

    monkeypatch.setattr(state_mod.os, "name", "nt")
    monkeypatch.setattr(win_acl.os, "name", "nt")
    monkeypatch.setattr(win_acl.subprocess, "run", fake_run)
    monkeypatch.setattr(
        win_acl, "_pinned_icacls", lambda: r"C:\Windows\System32\icacls.exe"
    )
    monkeypatch.setenv("USERNAME", "tester")
    monkeypatch.delenv("CURSOR_GOAL_SKIP_ACL", raising=False)
    state_mod._HARDENED_PATHS.clear()
    state_mod._ACL_HARDEN_FAILURES.clear()
    state_mod._harden_windows_acl(goal_home)
    assert str(goal_home) not in state_mod._HARDENED_PATHS
    assert state_mod.acl_harden_failure_message(goal_home) is not None
    assert any("/inheritance:e" in c for c in calls)


def test_harden_windows_acl_strip_fails_records_failure(
    goal_home: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from cursor_goal import state as state_mod

    n = {"i": 0}

    def fake_run(cmd: list[str], **_k: object) -> object:
        del cmd
        n["i"] += 1

        class Result:
            returncode = 5 if n["i"] == 1 else 0
            stderr = "strip failed"
            stdout = ""

        return Result()

    monkeypatch.setattr(state_mod.os, "name", "nt")
    monkeypatch.setattr(win_acl.os, "name", "nt")
    monkeypatch.setattr(win_acl.subprocess, "run", fake_run)
    monkeypatch.setattr(
        win_acl, "_pinned_icacls", lambda: r"C:\Windows\System32\icacls.exe"
    )
    monkeypatch.setenv("USERNAME", "tester")
    monkeypatch.delenv("CURSOR_GOAL_SKIP_ACL", raising=False)
    state_mod._HARDENED_PATHS.clear()
    state_mod._ACL_HARDEN_FAILURES.clear()
    state_mod._harden_windows_acl(goal_home)
    assert str(goal_home) not in state_mod._HARDENED_PATHS
    assert state_mod.acl_harden_failure_message(goal_home) is not None
    assert "inheritance strip failed" in state_mod.acl_harden_failure_message(goal_home)


def test_default_wake_budget_helpers() -> None:
    from cursor_goal.state import (
        GoalState,
        _apply_field,
        clamp_wake_budget,
        default_wake_budget,
    )

    assert default_wake_budget(20) == 200
    assert default_wake_budget(1) == 10
    assert clamp_wake_budget(999) == 500
    try:
        clamp_wake_budget(0)
        raise AssertionError("expected ValueError")
    except ValueError:
        pass

    state = GoalState()
    _apply_field(state, "shell_ok", 0)
    assert state.shell_ok is False
    _apply_field(state, "shell_ok", "off")
    assert state.shell_ok is False
    _apply_field(state, "shell_ok", "true")
    assert state.shell_ok is True
    _apply_field(state, "shell_ok", "on")
    assert state.shell_ok is True
    _apply_field(state, "shell_ok", 1)
    assert state.shell_ok is True
    _apply_field(state, "wake_ticks", 3)
    assert state.wake_ticks == 3
    try:
        _apply_field(state, "shell_ok", "maybe")
        raise AssertionError("expected ValueError")
    except ValueError:
        pass
    try:
        _apply_field(state, "wake_ticks", -1)
        raise AssertionError("expected ValueError")
    except ValueError:
        pass


def test_from_dict_invalid_shell_ok_and_wake_budget() -> None:
    from cursor_goal.state import GoalState

    with pytest.raises(ValueError, match="shell_ok"):
        GoalState.from_dict(
            {
                "condition": "x",
                "turn_budget": 5,
                "turns_used": 0,
                "schema_version": 1,
                "shell_ok": "maybe",
                "status": "pursuing",
            }
        )
    with pytest.raises(ValueError, match="wake_budget"):
        GoalState.from_dict(
            {
                "condition": "x",
                "turn_budget": 5,
                "turns_used": 0,
                "schema_version": 1,
                "wake_budget": "nope",
                "status": "pursuing",
            }
        )


def test_harden_acl_oserror_after_strip(
    goal_home: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from cursor_goal import state as state_mod

    n = {"i": 0}

    def fake_run(cmd: list[str], **_k: object) -> object:
        del cmd
        n["i"] += 1
        if n["i"] == 1:

            class Ok:
                returncode = 0
                stderr = ""
                stdout = ""

            return Ok()
        raise OSError("grant boom")

    monkeypatch.setattr(state_mod.os, "name", "nt")
    monkeypatch.setattr(win_acl.os, "name", "nt")
    monkeypatch.setattr(win_acl.subprocess, "run", fake_run)
    monkeypatch.setattr(
        win_acl, "_pinned_icacls", lambda: r"C:\Windows\System32\icacls.exe"
    )
    monkeypatch.setenv("USERNAME", "tester")
    monkeypatch.delenv("CURSOR_GOAL_SKIP_ACL", raising=False)
    state_mod._HARDENED_PATHS.clear()
    state_mod._ACL_HARDEN_FAILURES.clear()
    state_mod._harden_windows_acl(goal_home)
    assert str(goal_home) not in state_mod._HARDENED_PATHS


def test_harden_windows_acl_failures(
    goal_home: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from cursor_goal import state as state_mod

    monkeypatch.setattr(state_mod.os, "name", "nt")
    monkeypatch.setattr(win_acl.os, "name", "nt")
    monkeypatch.delenv("CURSOR_GOAL_SKIP_ACL", raising=False)
    monkeypatch.delenv("USERNAME", raising=False)
    monkeypatch.delenv("USER", raising=False)
    monkeypatch.setattr(win_acl, "_windows_logon_name", lambda: None)

    def boom_login() -> str:
        raise OSError("no login")

    monkeypatch.setattr(win_acl.os, "getlogin", boom_login)
    state_mod._HARDENED_PATHS.clear()
    state_mod._ACL_HARDEN_FAILURES.clear()
    state_mod._harden_windows_acl(goal_home)  # no username

    monkeypatch.setenv("USERNAME", "tester")
    monkeypatch.setattr(
        win_acl, "_pinned_icacls", lambda: r"C:\Windows\System32\icacls.exe"
    )

    def boom_run(*_a: object, **_k: object) -> object:
        raise OSError("no icacls")

    state_mod._HARDENED_PATHS.clear()
    state_mod._ACL_HARDEN_FAILURES.clear()
    monkeypatch.setattr(win_acl.subprocess, "run", boom_run)
    state_mod._harden_windows_acl(goal_home)

    def bad_exit(*_a: object, **_k: object) -> object:
        class Result:
            returncode = 5
            stderr = "access denied"
            stdout = ""

        return Result()

    state_mod._HARDENED_PATHS.clear()
    state_mod._ACL_HARDEN_FAILURES.clear()
    monkeypatch.setattr(win_acl.subprocess, "run", bad_exit)
    state_mod._harden_windows_acl(goal_home)
    assert str(goal_home) not in state_mod._HARDENED_PATHS


def test_harden_windows_acl_missing_icacls(
    goal_home: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from cursor_goal import state as state_mod

    monkeypatch.setattr(state_mod.os, "name", "nt")
    monkeypatch.setattr(win_acl.os, "name", "nt")
    monkeypatch.delenv("CURSOR_GOAL_SKIP_ACL", raising=False)
    monkeypatch.setenv("USERNAME", "tester")
    monkeypatch.setattr(win_acl, "_pinned_icacls", lambda: None)
    state_mod._HARDENED_PATHS.clear()
    state_mod._ACL_HARDEN_FAILURES.clear()
    state_mod._harden_windows_acl(goal_home)
    assert str(goal_home) not in state_mod._HARDENED_PATHS


def test_quarantine_collision(goal_home: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    from cursor_goal import state as state_mod

    (goal_home / "goal.json").write_text("{bad", encoding="utf-8")
    monkeypatch.setattr(state_mod, "now_iso", lambda: "20200101T000000Z")
    dest_name = "goal.json.corrupt.20200101T000000Z"
    (goal_home / dest_name).write_text("old", encoding="utf-8")
    assert load_goal() is None
    assert not (goal_home / "goal.json").exists()
    assert len(list(goal_home.glob("goal.json.corrupt.*"))) >= 2


def test_snapshot_goal(goal_home: Path) -> None:
    from cursor_goal.state import snapshot_goal

    save_goal(GoalState(condition="c", created_at="t", status="pursuing"))
    snapped = snapshot_goal()
    assert snapped is not None
    assert snapped.condition == "c"


def test_data_dir_is_insecure_stat_oserror(
    goal_home: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from cursor_goal import state as state_mod

    class Fake:
        def expanduser(self) -> Fake:
            return self

        def is_absolute(self) -> bool:
            return True

        @property
        def parent(self) -> Fake:
            return self

        def is_symlink(self) -> bool:
            return False

        def lstat(self) -> object:
            raise OSError("stat failed")

    monkeypatch.setattr(state_mod.os, "name", "posix")
    assert state_mod.data_dir_is_insecure(Fake()) is True  # type: ignore[arg-type]


def test_cursor_goal_data_requires_absolute(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from cursor_goal import state as state_mod

    monkeypatch.setenv("CURSOR_GOAL_DATA", "relative-data-dir")
    with pytest.raises(ValueError, match="absolute"):
        state_mod.configured_data_dir_path()


def test_normalize_workdir_jail(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from cursor_goal import state as state_mod

    inside = tmp_path / "proj"
    inside.mkdir()
    outside = tmp_path / "outside"
    outside.mkdir()
    monkeypatch.chdir(inside)
    monkeypatch.delenv("CURSOR_GOAL_ALLOW_ANY_WORKDIR", raising=False)
    assert Path(state_mod.normalize_workdir(".")) == inside.resolve()
    with pytest.raises(ValueError, match="must be under"):
        state_mod.normalize_workdir(str(outside))
    monkeypatch.setenv("CURSOR_GOAL_ALLOW_ANY_WORKDIR", "1")
    assert Path(state_mod.normalize_workdir(str(outside))) == outside.resolve()


def test_assert_workdir_usable_missing(tmp_path: Path) -> None:
    from cursor_goal import state as state_mod

    missing = tmp_path / "gone"
    with pytest.raises(ValueError, match="missing"):
        state_mod.assert_workdir_usable(str(missing))


def test_set_workdir_rejects_symlink_probe(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from cursor_goal import path_trust as path_trust_mod

    work = tmp_path / "wd"
    work.mkdir()
    monkeypatch.setattr(path_trust_mod, "path_has_symlink_or_reparse", lambda _p: True)
    state = GoalState(condition="c", created_at="t", status="pursuing")
    with pytest.raises(ValueError, match="symlink|junction|reparse"):
        _apply_field(state, "workdir", str(work))


def test_set_workdir_rejects_symlink(tmp_path: Path) -> None:
    target = tmp_path / "real-wd"
    target.mkdir()
    link = tmp_path / "link-wd"
    try:
        link.symlink_to(target, target_is_directory=True)
    except OSError as exc:
        pytest.skip(f"Cannot create symlinks without elevated privileges: {exc}")
    state = GoalState(condition="c", created_at="t", status="pursuing")
    with pytest.raises(ValueError, match="symlink|junction|reparse"):
        _apply_field(state, "workdir", str(link))


def test_set_workdir_accepts_real_dir(tmp_path: Path) -> None:
    work = tmp_path / "wd"
    work.mkdir()
    state = GoalState(condition="c", created_at="t", status="pursuing")
    _apply_field(state, "workdir", str(work))
    assert Path(state.workdir).resolve() == work.resolve()


def test_set_workdir_empty_clears() -> None:
    state = GoalState(
        condition="c", created_at="t", status="pursuing", workdir="/tmp/old"
    )
    _apply_field(state, "workdir", "")
    assert state.workdir == ""


def test_eval_validate_lock_timeout_on_snapshot(
    goal_home: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from cursor_goal import evaluate as evaluate_mod

    run_cli("manage", "create", "g", "--test", "echo")

    def boom(**_k: object) -> None:
        raise GoalLockTimeoutError("locked")

    monkeypatch.setattr(evaluate_mod, "snapshot_goal", boom)
    code, _out, err = run_cli("eval", "validate")
    assert code == 1
    assert "locked" in err


def test_refuse_if_acl_harden_failed_message(
    goal_home: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from cursor_goal import state as state_mod

    monkeypatch.setattr(state_mod.os, "name", "posix")
    assert state_mod.refuse_if_acl_harden_failed(goal_home) is None

    monkeypatch.setattr(state_mod.os, "name", "nt")
    monkeypatch.setattr(win_acl.os, "name", "nt")
    state_mod._ACL_HARDEN_FAILURES.clear()
    assert state_mod.refuse_if_acl_harden_failed(goal_home) is None
    state_mod._ACL_HARDEN_FAILURES[str(goal_home)] = "grant failed"
    msg = state_mod.refuse_if_acl_harden_failed(goal_home)
    assert msg is not None
    assert msg.startswith("[goal] Error:")
    assert "grant failed" in msg


def test_create_refuses_acl_harden_failure(
    goal_home: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from cursor_goal import manage as manage_mod

    monkeypatch.setattr(
        manage_mod,
        "refuse_if_acl_harden_failed",
        lambda: "[goal] Error: Windows ACL harden failed",
    )
    code, _out, err = run_cli("manage", "create", "acl fail")
    assert code == 1
    assert "ACL harden" in err


def test_doctor_wake_warning_includes_command(
    goal_home: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from cursor_goal import doctor as doctor_mod

    monkeypatch.setenv("CURSOR_GOAL_WAKE", "1")
    run_cli("manage", "create", "doctor wake")
    # Patch on doctor_mod (where cmd_doctor's globals actually resolve the
    # name from), not manage_mod's re-export — patching the re-export alias
    # has no effect on cmd_doctor's own module-level lookup.
    monkeypatch.setattr(doctor_mod, "_hooks_look_configured", lambda: True)
    # Armed but no loop pid → hard-fail with exact command
    code, out, err = run_cli("manage", "doctor")
    assert code == 1
    combined = out + err
    assert (
        "wake loop" in combined.lower()
        or "REQUIRED" in combined
        or "start" in combined.lower()
        or "continuation" in combined.lower()
    )


def test_wake_loop_shell_hint_unix(monkeypatch: pytest.MonkeyPatch) -> None:
    from cursor_goal import manage as manage_mod
    from cursor_goal import paths as paths_mod

    # Do not patch os.name globally (breaks pathlib Path() on Windows).
    monkeypatch.setattr(paths_mod, "python_invocation", lambda: ["python3", "-u"])
    monkeypatch.setattr(paths_mod, "quote_for_shell", lambda p: str(p))
    hint = manage_mod._wake_loop_shell_hint()
    assert "python3" in hint
    assert "wake loop" in hint


def test_eval_validate_refuses_acl_harden_failure(
    goal_home: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from cursor_goal import evaluate as evaluate_mod

    run_cli("manage", "create", "g", "--test", "echo")
    monkeypatch.setattr(
        evaluate_mod,
        "refuse_if_acl_harden_failed",
        lambda: "[goal] Error: Windows ACL harden failed",
    )
    code, _out, err = run_cli("eval", "validate")
    assert code == 1
    assert "ACL harden" in err


def test_windows_reparse_point_is_insecure(
    goal_home: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    import cursor_goal.path_trust as path_trust_mod
    import cursor_goal.state as state_mod

    monkeypatch.setattr(path_trust_mod.os, "name", "nt")
    monkeypatch.setattr(win_acl.os, "name", "nt")
    monkeypatch.setattr(
        path_trust_mod, "_windows_path_is_reparse_point", lambda _p: True
    )
    assert state_mod.data_dir_is_insecure(goal_home) is True
    monkeypatch.setattr(path_trust_mod, "data_dir_is_insecure", lambda path=None: True)
    monkeypatch.setattr(
        path_trust_mod, "data_dir", lambda *, check_writable=True: goal_home
    )
    msg = state_mod.refuse_if_data_dir_insecure()
    assert msg is not None
    assert "reparse" in msg.lower() or "junction" in msg.lower()


def test_windows_reparse_helper_symlink(
    goal_home: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    import cursor_goal.path_trust as path_trust_mod

    del goal_home
    del monkeypatch

    class Fake:
        def is_symlink(self) -> bool:
            return True

    assert path_trust_mod._windows_path_is_reparse_point(Fake()) is True  # type: ignore[arg-type]


def test_stop_and_wake_refuse_acl_harden(
    goal_home: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from cursor_goal import stop as stop_mod
    from cursor_goal import wake as wake_mod
    from cursor_goal.stop import handle_stop

    monkeypatch.setenv("CURSOR_GOAL_WAKE", "1")
    run_cli("manage", "create", "acl stop")
    monkeypatch.setattr(
        stop_mod,
        "refuse_if_acl_harden_failed",
        lambda: "[goal] Error: Windows ACL harden failed",
    )
    assert handle_stop({"status": "completed", "loop_count": 0}) == {}

    monkeypatch.setattr(
        wake_mod,
        "refuse_if_acl_harden_failed",
        lambda: "[goal] Error: Windows ACL harden failed",
    )
    with pytest.raises(OSError, match="ACL"):
        wake_mod.arm(interval=5)
    assert wake_mod.tick() == 1


def test_field_limits_on_set_and_load(goal_home: Path) -> None:
    import cursor_goal.state as state_mod
    from cursor_goal.state import MAX_FIELD_CHARS, GoalState, load_goal, save_goal

    huge = "x" * (MAX_FIELD_CHARS + 50)
    state = GoalState(condition="c", created_at="t", status="pursuing", active=True)
    with pytest.raises(ValueError, match="last_reason"):
        _apply_field(state, "last_reason", huge)
    with pytest.raises(ValueError, match="created_at"):
        _apply_field(state, "created_at", huge)

    state.last_reason = huge  # bypass setter for corrupt-on-disk simulation
    state.last_validation_output = huge
    state.last_eval_verdict = huge
    state.last_audit_verdict = huge
    state.created_at = huge
    save_goal(state)
    loaded = load_goal()
    assert loaded is not None
    assert len(loaded.last_reason) == MAX_FIELD_CHARS
    assert len(loaded.last_validation_output) == MAX_FIELD_CHARS
    assert len(loaded.last_eval_verdict) == MAX_FIELD_CHARS
    assert len(loaded.last_audit_verdict) == MAX_FIELD_CHARS
    assert len(loaded.created_at) == MAX_FIELD_CHARS
    assert state_mod._clamp_field_chars("n", "ok") == "ok"


def test_redact_jwt_and_basic_auth() -> None:
    from cursor_goal.validation import redact_secrets

    jwt = (
        "eyJhbGciOiJIUzI1NiIsInR5cCI6IkpXVCJ9."
        "eyJzdWIiOiIxMjM0NTY3ODkwIn0."
        "signaturepartgoeshere01"
    )
    out = redact_secrets(f"hdr {jwt} Authorization: Basic dXNlcjpwYXNz")
    assert "<redacted-jwt>" in out
    assert "dXNlcjpwYXNz" not in out
    assert "Basic <redacted>" in out


def test_manage_status_redacts_reason(goal_home: Path) -> None:
    run_cli("manage", "create", "secret status")
    from cursor_goal.state import update_goal_fields

    update_goal_fields(last_reason="password=supersecret value")
    code, out, _err = run_cli("manage", "status")
    assert code == 0
    assert "supersecret" not in out
    assert "<redacted>" in out


def test_doctor_redacts_goal_condition(goal_home: Path) -> None:
    run_cli("manage", "create", "ship with api_key=doctorsecret99")
    code, out, _err = run_cli("manage", "doctor")
    assert "doctorsecret99" not in out
    assert code in {0, 1}


def test_doctor_hard_fails_orphan_wake(goal_home: Path) -> None:
    from cursor_goal.wake import mark_orphan_wake

    mark_orphan_wake(999001, "test orphan marker")
    assert (goal_home / "wake.orphan").is_file()
    code, out, err = run_cli("manage", "doctor")
    combined = out + err
    assert code == 1
    assert "Orphan wake suspected" in combined
    assert "999001" in combined


def test_harden_windows_acl_returns_bool(
    goal_home: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from cursor_goal import win_acl as acl_mod

    monkeypatch.setattr(acl_mod.os, "name", "posix")
    assert acl_mod.harden_windows_acl(goal_home) is True

    monkeypatch.setattr(acl_mod.os, "name", "nt")
    monkeypatch.setattr(acl_mod, "acl_harden_disabled", lambda: True)
    assert acl_mod.harden_windows_acl(goal_home) is True


def test_harden_windows_acl_force_reharden(
    goal_home: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from cursor_goal import win_acl as acl_mod

    calls: list[list[str]] = []

    def fake_run(cmd: list[str], **_k: object) -> object:
        calls.append(list(cmd))

        class Result:
            returncode = 0
            stderr = ""
            stdout = ""

        return Result()

    monkeypatch.setattr(acl_mod.os, "name", "nt")
    monkeypatch.setattr(acl_mod, "acl_harden_disabled", lambda: False)
    monkeypatch.setattr(acl_mod, "windows_username", lambda: "testuser")
    monkeypatch.setattr(acl_mod, "_pinned_icacls", lambda: "icacls.exe")
    monkeypatch.setattr(acl_mod.subprocess, "run", fake_run)
    acl_mod.HARDENED_PATHS.clear()
    acl_mod.ACL_HARDEN_FAILURES.clear()
    assert acl_mod.harden_windows_acl(goal_home) is True
    first = len(calls)
    assert first >= 2
    assert acl_mod.harden_windows_acl(goal_home) is True
    assert len(calls) == first  # cached
    assert acl_mod.harden_windows_acl(goal_home, force=True) is True
    assert len(calls) > first


def test_is_absolute_interpreter_path() -> None:
    from cursor_goal.doctor import _is_absolute_interpreter_path

    assert _is_absolute_interpreter_path(r"C:\Python\python.exe") is True
    assert _is_absolute_interpreter_path("/usr/bin/python3") is True
    assert _is_absolute_interpreter_path(r"\\server\share\python.exe") is True
    assert _is_absolute_interpreter_path("//server/share/python.exe") is True
    assert _is_absolute_interpreter_path("python") is False
    assert _is_absolute_interpreter_path("") is False
    assert _is_absolute_interpreter_path('  "C:\\Python\\python.exe"  ') is True


def test_doctor_cursor_goal_python_absolute(
    goal_home: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr("cursor_goal.doctor.os.name", "nt")
    # Must be an existing absolute interpreter (doctor verifies the file exists).
    monkeypatch.setenv("CURSOR_GOAL_PYTHON", sys.executable)
    code, out, _err = run_cli("manage", "doctor")
    assert code == 0
    assert "CURSOR_GOAL_PYTHON" in out
    assert sys.executable in out


def test_doctor_cursor_goal_python_relative_fails(
    goal_home: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr("cursor_goal.doctor.os.name", "nt")
    monkeypatch.setenv("CURSOR_GOAL_PYTHON", "python")
    code, _out, err = run_cli("manage", "doctor")
    assert code == 1
    assert "absolute" in err.lower() or "absolute" in _out.lower()


def test_doctor_insecure_windows_message(
    goal_home: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    import cursor_goal.doctor as doctor_mod

    monkeypatch.setattr(doctor_mod.os, "name", "nt")
    monkeypatch.setattr(doctor_mod, "data_dir_is_insecure", lambda _p=None: True)
    code, out, err = run_cli("manage", "doctor")
    assert code == 1
    blob = out + err
    assert "reparse" in blob.lower() or "junction" in blob.lower()


def test_windows_reparse_helper_edge_cases(
    goal_home: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    import cursor_goal.path_trust as path_trust_mod

    del goal_home

    class BoomSymlink:
        def is_symlink(self) -> bool:
            raise OSError("nope")

    class NoSymlink:
        def is_symlink(self) -> bool:
            return False

    assert path_trust_mod._windows_path_is_reparse_point(BoomSymlink()) is True  # type: ignore[arg-type]

    monkeypatch.setattr(path_trust_mod.ctypes, "windll", None, raising=False)

    # When windll missing, getattr returns None → False
    class FakeCtypes:
        windll = None

    monkeypatch.setattr(path_trust_mod, "ctypes", FakeCtypes())
    assert path_trust_mod._windows_path_is_reparse_point(NoSymlink()) is False  # type: ignore[arg-type]

    class BoomAttrs:
        def GetFileAttributesW(self, _path: str) -> int:
            raise OSError("attrs")

    class FakeWindll:
        kernel32 = BoomAttrs()

    class FakeCtypes2:
        windll = FakeWindll()
        c_uint32 = type("c_uint32", (), {})

    monkeypatch.setattr(path_trust_mod, "ctypes", FakeCtypes2())
    assert path_trust_mod._windows_path_is_reparse_point(NoSymlink()) is True  # type: ignore[arg-type]

    class InvalidAttrs:
        def GetFileAttributesW(self, _path: str) -> int:
            return path_trust_mod._INVALID_FILE_ATTRIBUTES

        restype = None

    class FakeWindll2:
        kernel32 = type(
            "K",
            (),
            {
                "GetFileAttributesW": staticmethod(
                    lambda _p: path_trust_mod._INVALID_FILE_ATTRIBUTES
                )
            },
        )()

    class FakeCtypes3:
        windll = FakeWindll2()

        class c_uint32:
            pass

    # Rebuild with assignable restype
    class AttrFn:
        restype = None

        def __call__(self, _path: str) -> int:
            return path_trust_mod._INVALID_FILE_ATTRIBUTES

    class K32:
        GetFileAttributesW = AttrFn()

    class WDLL:
        kernel32 = K32()

    class CT:
        windll = WDLL()
        c_uint32 = int

    monkeypatch.setattr(path_trust_mod, "ctypes", CT())
    assert path_trust_mod._windows_path_is_reparse_point(NoSymlink()) is False  # type: ignore[arg-type]

    class ReparseFn:
        restype = None

        def __call__(self, _path: str) -> int:
            return path_trust_mod._FILE_ATTRIBUTE_REPARSE_POINT

    class K32b:
        GetFileAttributesW = ReparseFn()

    class WDLLb:
        kernel32 = K32b()

    class CTb:
        windll = WDLLb()
        c_uint32 = int

    monkeypatch.setattr(path_trust_mod, "ctypes", CTb())
    assert path_trust_mod._windows_path_is_reparse_point(NoSymlink()) is True  # type: ignore[arg-type]


def test_stop_skips_last_response_on_acl(
    goal_home: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from cursor_goal import stop as stop_mod

    monkeypatch.setattr(
        stop_mod,
        "refuse_if_acl_harden_failed",
        lambda: "[goal] Error: Windows ACL harden failed",
    )
    stop_mod._write_last_stop_response({"followup_message": "hi"})
    assert not (goal_home / "last-stop-response.json").is_file()


def test_wake_run_loop_refuses_acl(
    goal_home: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from cursor_goal import wake as wake_mod

    monkeypatch.setenv("CURSOR_GOAL_WAKE", "1")
    monkeypatch.setattr(
        wake_mod,
        "refuse_if_acl_harden_failed",
        lambda: "[goal] Error: Windows ACL harden failed",
    )
    assert wake_mod.run_loop(interval=5) == 1


def test_normalize_workdir_resolve_oserror(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from cursor_goal import state as state_mod

    target = tmp_path / "d"
    target.mkdir()
    monkeypatch.setenv("CURSOR_GOAL_ALLOW_ANY_WORKDIR", "1")

    def boom(self: Path) -> Path:
        del self
        raise OSError("resolve fail")

    monkeypatch.setattr(type(target), "resolve", boom)
    with pytest.raises(ValueError, match="could not be resolved"):
        state_mod.normalize_workdir(str(target))


def test_record_agent_nudge_noop_when_unarmed(goal_home: Path) -> None:
    from cursor_goal import wake as wake_mod

    del goal_home
    # No wake.json — should no-op without raising.
    wake_mod.record_agent_nudge()


def test_windows_username_prefers_os_logon_over_env(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(win_acl.sys, "platform", "win32")
    monkeypatch.setenv("USERNAME", "OtherUser")
    monkeypatch.setenv("USER", "OtherUser")
    monkeypatch.setattr(win_acl, "_windows_logon_name", lambda: "RealUser")
    assert win_acl.windows_username() == "RealUser"


def test_windows_logon_name_error_paths(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(win_acl.sys, "platform", "linux")
    assert win_acl._is_win32() is False
    assert win_acl._windows_logon_name() is None
    assert win_acl._windows_os_identity() is None

    monkeypatch.setattr(win_acl.sys, "platform", "win32")
    assert win_acl._is_win32() is True

    class NoWindll:
        pass

    monkeypatch.setattr(win_acl, "ctypes", NoWindll())
    assert win_acl._windows_logon_name() is None


def _patch_logon_ctypes(
    monkeypatch: pytest.MonkeyPatch,
    get_user_name: object,
) -> None:
    """Install a FakeCtypes whose helpers are not bound as instance methods."""

    class FakeWindll:
        class advapi32:
            GetUserNameW = get_user_name

    class FakeCtypes:
        windll = FakeWindll()
        c_ulong = win_acl.ctypes.c_ulong
        create_unicode_buffer = staticmethod(win_acl.ctypes.create_unicode_buffer)
        byref = staticmethod(lambda obj: obj)

    monkeypatch.setattr(win_acl.sys, "platform", "win32")
    monkeypatch.setattr(win_acl, "ctypes", FakeCtypes())


def test_windows_logon_name_getusername_raises(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    def boom(_buf: object, _size: object) -> int:
        raise OSError("access denied")

    _patch_logon_ctypes(monkeypatch, boom)
    assert win_acl._windows_logon_name() is None


def test_windows_logon_name_missing_api(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(win_acl.sys, "platform", "win32")

    class FakeCtypes:
        class windll:
            class advapi32:
                pass

    monkeypatch.setattr(win_acl, "ctypes", FakeCtypes())
    assert win_acl._windows_logon_name() is None


def test_windows_logon_name_empty_after_strip(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    def fill(buf: object, _size: object) -> int:
        buf.value = "   "  # type: ignore[attr-defined]
        return 1

    _patch_logon_ctypes(monkeypatch, fill)
    assert win_acl._windows_logon_name() is None


def test_windows_logon_name_buffer_too_small_then_ok(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    calls = {"n": 0}

    def get_user_name(buf: object, size: object) -> int:
        calls["n"] += 1
        if calls["n"] == 1:
            size.value = 32  # type: ignore[attr-defined]
            return 0
        buf.value = "RetryUser"  # type: ignore[attr-defined]
        return 1

    _patch_logon_ctypes(monkeypatch, get_user_name)
    assert win_acl._windows_logon_name() == "RetryUser"


def test_windows_logon_name_buffer_retry_fails(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    def get_user_name(_buf: object, size: object) -> int:
        size.value = 32  # type: ignore[attr-defined]
        return 0

    _patch_logon_ctypes(monkeypatch, get_user_name)
    assert win_acl._windows_logon_name() is None


def test_windows_logon_name_zero_size(monkeypatch: pytest.MonkeyPatch) -> None:
    def get_user_name(_buf: object, size: object) -> int:
        size.value = 0  # type: ignore[attr-defined]
        return 0

    _patch_logon_ctypes(monkeypatch, get_user_name)
    assert win_acl._windows_logon_name() is None


def test_windows_system_root_file_is_file_oserror(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from cursor_goal import native_path as np_mod

    root = tmp_path / "Win"
    (root / "System32").mkdir(parents=True)
    monkeypatch.setenv("SystemRoot", str(root))
    real_is_file = np_mod.Path.is_file

    def boom(self: Path) -> bool:
        if self.name == "icacls.exe":
            raise OSError("stat failed")
        return real_is_file(self)

    monkeypatch.setattr(np_mod.Path, "is_file", boom)
    assert np_mod.windows_system_root_file("System32", "icacls.exe") is None


def test_windows_system_root_file_requires_existing_file(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from cursor_goal.native_path import windows_system_root_file

    monkeypatch.delenv("SystemRoot", raising=False)
    monkeypatch.delenv("SYSTEMROOT", raising=False)
    assert windows_system_root_file("System32", "icacls.exe") is None

    root = tmp_path / "Win"
    system32 = root / "System32"
    system32.mkdir(parents=True)
    icacls = system32 / "icacls.exe"
    icacls.write_bytes(b"")
    monkeypatch.setenv("SystemRoot", str(root))
    found = windows_system_root_file("System32", "icacls.exe")
    assert found is not None
    assert found == icacls
    assert windows_system_root_file("System32", "missing.exe") is None
    assert windows_system_root_file() is None
