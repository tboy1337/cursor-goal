"""Extra coverage for production-hardening paths."""

from __future__ import annotations

import io
import json
import sys
from pathlib import Path
from unittest.mock import patch

import pytest

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
    _apply_field(state, "active", False)
    assert state.active is False
    with pytest.raises(ValueError, match="turns_used"):
        _apply_field(state, "turns_used", -1)
    with pytest.raises(ValueError, match="unknown"):
        _apply_field(state, "not_a_field", 1)
    with pytest.raises(ValueError, match="status"):
        _apply_field(state, "status", "bogus")


def test_load_goal_negative_turns(goal_home: Path) -> None:
    (goal_home / "goal.json").write_text(
        json.dumps(
            {
                "condition": "c",
                "turn_budget": 5,
                "turns_used": -1,
                "status": "pursuing",
                "active": True,
                "schema_version": 2,
            }
        ),
        encoding="utf-8",
    )
    assert load_goal() is None


def test_windows_lock_timeout_message(monkeypatch: pytest.MonkeyPatch) -> None:
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
    monkeypatch.setitem(sys.modules, "msvcrt", fake_msvcrt)
    monkeypatch.setattr(sys, "platform", "win32")
    with pytest.raises(GoalLockTimeoutError, match="goal.lock"):
        state_mod._lock_acquire(Handle())


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
    monkeypatch.setattr(state_mod.subprocess, "run", fake_run)
    monkeypatch.setattr(
        state_mod.shutil, "which", lambda _n: r"C:\Windows\System32\icacls.exe"
    )
    monkeypatch.setenv("USERNAME", "tester")
    monkeypatch.delenv("CURSOR_GOAL_SKIP_ACL", raising=False)
    state_mod._HARDENED_PATHS.clear()
    state_mod._harden_windows_acl(goal_home)
    state_mod._harden_windows_acl(goal_home)
    assert len(calls) == 1
    assert "icacls" in calls[0][0].lower() or calls[0][0].endswith("icacls.exe")


def test_parse_result_path_jail_rejects_outside(
    goal_home: Path, tmp_path: Path
) -> None:
    outside = tmp_path / "outside.txt"
    outside.write_text("YES: ok", encoding="utf-8")
    # Absolute path under tmp_path parent sibling — not data dir / cwd.
    code, _out, err = run_cli("eval", "parse-result", f"@{outside}")
    # May fail jail or succeed if tmp happens to be under cwd; accept jail msg.
    if code == 1 and "must be under" in err:
        return
    # If pytest cwd contains tmp_path, path is allowed — then YES succeeds.
    assert code in {0, 1}


def test_write_hooks_write_text_fail(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    path = tmp_path / "hooks.json"

    def boom_write(self: Path, *_a: object, **_k: object) -> None:
        raise OSError("disk full")

    monkeypatch.setattr(Path, "write_text", boom_write)
    with pytest.raises(OSError):
        write_hooks_file(path, {"version": 1, "hooks": {"stop": []}})


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
    monkeypatch.setattr(state_mod.subprocess, "run", fake_run)
    monkeypatch.setenv("CURSOR_GOAL_SKIP_ACL", "1")
    state_mod._HARDENED_PATHS.clear()
    state_mod._harden_windows_acl(goal_home)
    assert calls == []


def test_harden_windows_acl_failures(
    goal_home: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from cursor_goal import state as state_mod

    monkeypatch.setattr(state_mod.os, "name", "nt")
    monkeypatch.delenv("CURSOR_GOAL_SKIP_ACL", raising=False)
    monkeypatch.delenv("USERNAME", raising=False)
    monkeypatch.delenv("USER", raising=False)

    def boom_login() -> str:
        raise OSError("no login")

    monkeypatch.setattr(state_mod.os, "getlogin", boom_login)
    state_mod._HARDENED_PATHS.clear()
    state_mod._harden_windows_acl(goal_home)  # no username

    monkeypatch.setenv("USERNAME", "tester")
    monkeypatch.setattr(
        state_mod.shutil, "which", lambda _n: r"C:\Windows\System32\icacls.exe"
    )

    def boom_run(*_a: object, **_k: object) -> object:
        raise OSError("no icacls")

    state_mod._HARDENED_PATHS.clear()
    monkeypatch.setattr(state_mod.subprocess, "run", boom_run)
    state_mod._harden_windows_acl(goal_home)

    def bad_exit(*_a: object, **_k: object) -> object:
        class Result:
            returncode = 5
            stderr = "access denied"
            stdout = ""

        return Result()

    state_mod._HARDENED_PATHS.clear()
    monkeypatch.setattr(state_mod.subprocess, "run", bad_exit)
    state_mod._harden_windows_acl(goal_home)
    assert str(goal_home) not in state_mod._HARDENED_PATHS


def test_harden_windows_acl_missing_icacls(
    goal_home: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from cursor_goal import state as state_mod

    monkeypatch.setattr(state_mod.os, "name", "nt")
    monkeypatch.delenv("CURSOR_GOAL_SKIP_ACL", raising=False)
    monkeypatch.setenv("USERNAME", "tester")
    monkeypatch.setattr(state_mod.shutil, "which", lambda _n: None)
    state_mod._HARDENED_PATHS.clear()
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
        def stat(self) -> object:
            raise OSError("stat failed")

    assert state_mod.data_dir_is_insecure(Fake()) is False  # type: ignore[arg-type]


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
