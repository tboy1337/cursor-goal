"""Tests for cursor_goal.state and logging_config."""

from __future__ import annotations

import io
import json
import logging
import os
import sys
from pathlib import Path

import pytest

from cursor_goal.logging_config import get_logger
from cursor_goal.state import (
    GoalState,
    clear_eval_signal,
    data_dir,
    has_eval_signal,
    load_goal,
    save_goal,
    set_eval_signal,
    update_goal_fields,
)


def test_data_dir_default_home(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("CURSOR_GOAL_DATA", raising=False)
    monkeypatch.setenv("HOME", str(tmp_path / "home"))
    (tmp_path / "home").mkdir()
    # On Windows Path.home() uses USERPROFILE; set both.
    monkeypatch.setenv("USERPROFILE", str(tmp_path / "home"))
    path = data_dir()
    assert path.is_dir()
    assert path.name == "data"


def test_load_goal_corrupt_json(goal_home: Path) -> None:
    (goal_home / "goal.json").write_text("{not-json", encoding="utf-8")
    assert load_goal() is None


def test_load_goal_non_object(goal_home: Path) -> None:
    (goal_home / "goal.json").write_text("[1,2]\n", encoding="utf-8")
    assert load_goal() is None


def test_update_goal_fields_no_goal(goal_home: Path) -> None:
    assert update_goal_fields(last_reason="x") is None


def test_update_goal_fields_ignores_unknown(goal_home: Path) -> None:
    state = GoalState(condition="c", created_at="t", status="pursuing")
    save_goal(state)
    updated = update_goal_fields(last_reason="ok", not_a_field="nope")
    assert updated is not None
    assert updated.last_reason == "ok"
    raw = json.loads((goal_home / "goal.json").read_text(encoding="utf-8"))
    assert "not_a_field" not in raw


def test_logging_reset_closes_handlers(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    from cursor_goal import logging_config as log_mod

    monkeypatch.setenv("CURSOR_GOAL_LOG_FILE", str(tmp_path / "r.log"))
    log_mod._reset_for_tests()
    log_mod.get_logger("cursor_goal.reset_test")
    log_mod._reset_for_tests()
    log_mod._reset_for_tests()  # second reset is a no-op-ish cleanup


def test_logging_reset_handler_close_oserror(monkeypatch: pytest.MonkeyPatch) -> None:
    from cursor_goal import logging_config as log_mod

    class BadHandler(logging.Handler):
        def emit(self, record: logging.LogRecord) -> None:
            del record

        def close(self) -> None:
            raise OSError("close failed")

    log_mod._reset_for_tests()
    root = logging.getLogger("cursor_goal")
    root.addHandler(BadHandler())
    log_mod._CONFIGURED = True
    log_mod._LOG_FILE_HANDLE = None
    log_mod._reset_for_tests()


def test_fs_lock_unix_release(monkeypatch: pytest.MonkeyPatch) -> None:
    from cursor_goal import fs_lock as fs_lock_mod

    class Handle:
        def fileno(self) -> int:
            return 3

        def seek(self, *_a: object, **_k: object) -> None:
            return None

    calls: list[object] = []

    fake_fcntl = type(sys)("fcntl")
    fake_fcntl.flock = lambda *a, **k: calls.append(a)  # type: ignore[attr-defined]
    fake_fcntl.LOCK_UN = 8  # type: ignore[attr-defined]
    monkeypatch.setitem(sys.modules, "fcntl", fake_fcntl)
    monkeypatch.setattr(sys, "platform", "linux")
    fs_lock_mod.lock_release(Handle())  # type: ignore[arg-type]
    assert calls


def test_get_logger_reuses_handlers(monkeypatch: pytest.MonkeyPatch) -> None:
    from cursor_goal import logging_config as log_mod

    monkeypatch.setenv("CURSOR_GOAL_LOG", "DEBUG")
    log_mod._reset_for_tests()
    name = "cursor_goal.test_logger_unique"
    existing = logging.getLogger(name)
    existing.handlers.clear()
    first = get_logger(name)
    second = get_logger(name)
    assert first is second
    root = logging.getLogger("cursor_goal")
    assert len(root.handlers) >= 1
    assert len(first.handlers) == 0
    assert first.propagate is True


def test_load_goal_corrupt_numeric_fields(goal_home: Path) -> None:
    (goal_home / "goal.json").write_text(
        json.dumps({"condition": "x", "turn_budget": "nope", "turns_used": 0}),
        encoding="utf-8",
    )
    assert load_goal() is None


def test_save_goal_writes_schema_version(goal_home: Path) -> None:
    save_goal(GoalState(condition="c", created_at="t", status="pursuing"))
    raw = json.loads((goal_home / "goal.json").read_text(encoding="utf-8"))
    assert raw["schema_version"] == 1
    assert raw["wake_budget"] == 200
    assert raw["shell_ok"] is False
    assert raw.get("workdir", "") == ""


def test_from_dict_defaults_and_rejects_unsupported_schema(goal_home: Path) -> None:
    del goal_home  # fixture sets CURSOR_GOAL_DATA
    state = GoalState.from_dict(
        {
            "condition": "current",
            "turn_budget": 20,
            "turns_used": 0,
            "wake_ticks": 5,
            "schema_version": 1,
            "status": "pursuing",
            "active": True,
        }
    )
    assert state.wake_budget == 200
    # Missing shell_ok defaults to False.
    assert state.shell_ok is False
    assert state.wake_ticks == 5
    assert state.status == "pursuing"
    assert state.workdir == ""
    assert state.schema_version == 1

    with pytest.raises(ValueError, match="unsupported schema_version"):
        GoalState.from_dict(
            {
                "condition": "old",
                "turn_budget": 20,
                "turns_used": 0,
                "schema_version": 2,
                "status": "pursuing",
                "active": True,
            }
        )
    with pytest.raises(ValueError, match="unsupported schema_version"):
        GoalState.from_dict(
            {
                "condition": "old",
                "turn_budget": 20,
                "turns_used": 0,
                "schema_version": 4,
                "status": "pursuing",
                "active": True,
            }
        )


def test_from_dict_accepts_schema_version_1(goal_home: Path) -> None:
    del goal_home
    state = GoalState.from_dict(
        {
            "condition": "v1",
            "turn_budget": 10,
            "turns_used": 0,
            "wake_ticks": 0,
            "wake_budget": 100,
            "shell_ok": False,
            "workdir": "/tmp/work",
            "schema_version": 1,
            "status": "pursuing",
            "active": True,
        }
    )
    assert state.schema_version == 1
    assert state.shell_ok is False
    assert state.workdir == "/tmp/work"


def test_load_goal_rejects_unknown_schema(goal_home: Path) -> None:
    (goal_home / "goal.json").write_text(
        json.dumps(
            {
                "condition": "x",
                "turn_budget": 5,
                "turns_used": 0,
                "schema_version": 99,
            }
        ),
        encoding="utf-8",
    )
    assert load_goal() is None


def test_eval_signal_bound_to_goal_hash(goal_home: Path) -> None:
    save_goal(
        GoalState(
            condition="first",
            created_at="t1",
            status="pursuing",
            active=True,
        )
    )
    set_eval_signal()
    assert has_eval_signal() is True
    raw = json.loads((goal_home / "goal-eval-done").read_text(encoding="utf-8"))
    assert raw["verdict"] == "YES"
    # Overwrite goal identity without clearing signal
    save_goal(
        GoalState(
            condition="second",
            created_at="t2",
            status="pursuing",
            active=True,
        )
    )
    assert has_eval_signal() is False
    clear_eval_signal()
    assert has_eval_signal() is False


def test_eval_signal_rejects_missing_verdict(goal_home: Path) -> None:
    save_goal(GoalState(condition="c", created_at="t", status="pursuing", active=True))
    state = load_goal()
    assert state is not None
    (goal_home / "goal-eval-done").write_text(
        json.dumps({"condition_hash": state.content_hash(), "created_at": "t"}),
        encoding="utf-8",
    )
    assert has_eval_signal() is False


def test_legacy_empty_eval_signal_rejected(goal_home: Path) -> None:
    save_goal(GoalState(condition="c", created_at="t", status="pursuing", active=True))
    (goal_home / "goal-eval-done").write_text("", encoding="utf-8")
    assert has_eval_signal() is False


def test_set_eval_signal_without_goal(goal_home: Path) -> None:
    set_eval_signal()
    assert not (goal_home / "goal-eval-done").exists()


def test_has_eval_signal_corrupt_json(goal_home: Path) -> None:
    save_goal(GoalState(condition="c", created_at="t", status="pursuing", active=True))
    (goal_home / "goal-eval-done").write_text("{not-json", encoding="utf-8")
    assert has_eval_signal() is False


def test_has_eval_signal_non_object(goal_home: Path) -> None:
    save_goal(GoalState(condition="c", created_at="t", status="pursuing", active=True))
    (goal_home / "goal-eval-done").write_text("[1]\n", encoding="utf-8")
    assert has_eval_signal() is False


def test_chmod_private_best_effort(
    goal_home: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    import cursor_goal.state as state_mod

    calls: list[tuple] = []

    def fake_chmod(path: object, mode: int) -> None:
        calls.append((path, mode))
        raise OSError("denied")

    monkeypatch.setattr(state_mod.os, "chmod", fake_chmod)
    monkeypatch.setattr(state_mod.os, "name", "posix")
    state_mod._chmod_private(goal_home / "goal.json")
    assert calls


def test_chmod_private_noop_on_windows(
    goal_home: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    import cursor_goal.state as state_mod

    calls: list[object] = []

    def fake_chmod(*_a: object, **_k: object) -> None:
        calls.append(True)

    monkeypatch.setattr(state_mod.os, "name", "nt")
    monkeypatch.setattr(state_mod.os, "chmod", fake_chmod)
    state_mod._chmod_private(goal_home / "goal.json")
    assert not calls


def test_warn_if_world_writable(
    goal_home: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    import cursor_goal.state as state_mod

    class FakeStat:
        st_mode = 0o777

    monkeypatch.setattr(state_mod.os, "name", "posix")
    monkeypatch.setattr(Path, "stat", lambda self: FakeStat())
    state_mod._warn_if_world_writable(goal_home)


def test_warn_if_world_writable_noop_on_windows(
    goal_home: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    import cursor_goal.state as state_mod

    monkeypatch.setattr(state_mod.os, "name", "nt")
    state_mod._warn_if_world_writable(goal_home)


def test_warn_if_world_writable_stat_oserror(
    goal_home: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    import cursor_goal.state as state_mod

    def boom_stat(self: Path) -> object:
        raise OSError("stat failed")

    monkeypatch.setattr(state_mod.os, "name", "posix")
    monkeypatch.setattr(Path, "stat", boom_stat)
    state_mod._warn_if_world_writable(goal_home)


def test_lock_acquire_windows(monkeypatch: pytest.MonkeyPatch) -> None:
    import cursor_goal.state as state_mod

    calls: list[tuple[object, ...]] = []

    class Handle:
        def __init__(self) -> None:
            self._pos = 0
            self._size = 0

        def fileno(self) -> int:
            return 3

        def seek(self, offset: int, whence: int = 0) -> None:
            if whence == os.SEEK_END:
                self._pos = self._size
            else:
                self._pos = offset

        def tell(self) -> int:
            return self._pos

        def write(self, data: bytes) -> int:
            self._size += len(data)
            self._pos += len(data)
            return len(data)

        def flush(self) -> None:
            return None

    def fake_locking(fd: int, mode: int, nbytes: int) -> None:
        calls.append((fd, mode, nbytes))

    fake_msvcrt = type(sys)("msvcrt")
    fake_msvcrt.locking = fake_locking  # type: ignore[attr-defined]
    fake_msvcrt.LK_LOCK = 1  # type: ignore[attr-defined]
    fake_msvcrt.LK_NBLCK = 2  # type: ignore[attr-defined]
    monkeypatch.setitem(sys.modules, "msvcrt", fake_msvcrt)
    monkeypatch.setattr(sys, "platform", "win32")
    state_mod._lock_acquire(Handle())
    assert calls == [(3, 2, 1)]


def test_set_eval_signal_rejects_non_yes(goal_home: Path) -> None:
    save_goal(GoalState(condition="c", created_at="t", status="pursuing", active=True))
    set_eval_signal(verdict="NO", reason="nope")
    assert not (goal_home / "goal-eval-done").exists()


def test_has_eval_signal_no_goal(goal_home: Path) -> None:
    assert has_eval_signal() is False


def test_lock_release_oserror(goal_home: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    import cursor_goal.state as state_mod

    class Boom:
        def fileno(self) -> int:
            return 1

        def seek(self, _n: int) -> None:
            return None

    def boom_locking(*_a: object, **_k: object) -> None:
        raise OSError("unlock failed")

    fake_msvcrt = type(sys)("msvcrt")
    fake_msvcrt.locking = boom_locking  # type: ignore[attr-defined]
    fake_msvcrt.LK_UNLCK = 2  # type: ignore[attr-defined]
    monkeypatch.setitem(sys.modules, "msvcrt", fake_msvcrt)
    monkeypatch.setattr(sys, "platform", "win32")
    state_mod._lock_release(Boom())


def _lock_stress_worker(data_env: str, counter_path: str, iterations: int) -> None:
    """Module-level worker so Windows spawn can pickle it."""
    import time
    from pathlib import Path

    from cursor_goal.state import goal_lock

    os.environ["CURSOR_GOAL_DATA"] = data_env
    path = Path(counter_path)
    for _ in range(iterations):
        with goal_lock():
            value = int(path.read_text(encoding="utf-8"))
            time.sleep(0.002)
            path.write_text(str(value + 1), encoding="utf-8")


@pytest.mark.timeout(90)
def test_goal_lock_concurrent_multiprocess(goal_home: Path) -> None:
    """Cross-process exclusive lock: workers serialize writes to a shared counter."""
    import multiprocessing

    counter_path = goal_home / "lock_counter.txt"
    counter_path.write_text("0", encoding="utf-8")
    data_env = str(goal_home)
    workers = 4
    iterations = 5

    ctx = multiprocessing.get_context("spawn")
    procs = [
        ctx.Process(
            target=_lock_stress_worker,
            args=(data_env, str(counter_path), iterations),
        )
        for _ in range(workers)
    ]
    for proc in procs:
        proc.start()
    for proc in procs:
        proc.join(timeout=60)
        assert proc.exitcode == 0
    assert counter_path.read_text(encoding="utf-8") == str(workers * iterations)


def test_active_string_false_is_corrupt(goal_home: Path) -> None:
    (goal_home / "goal.json").write_text(
        json.dumps(
            {
                "active": "false",
                "condition": "c",
                "turn_budget": 5,
                "turns_used": 0,
                "status": "pursuing",
                "schema_version": 1,
            }
        ),
        encoding="utf-8",
    )
    from cursor_goal.state import CorruptGoalError

    with pytest.raises(CorruptGoalError):
        load_goal(raise_corrupt=True)
    assert load_goal() is None
    assert list(goal_home.glob("goal.json.corrupt.*"))


def test_update_goal_fields_rejects_bad_types(goal_home: Path) -> None:
    save_goal(GoalState(condition="c", created_at="t", status="pursuing"))
    with pytest.raises(ValueError, match="boolean"):
        update_goal_fields(active="false")


def test_record_parse_result_atomic_vs_clear(goal_home: Path) -> None:
    """YES signal must bind to the goal present under the same lock as write."""
    from cursor_goal.state import create_goal_atomic, record_parse_result

    state = GoalState(
        condition="first",
        created_at="t1",
        status="pursuing",
        active=True,
    )
    created, status = create_goal_atomic(state)
    assert status == "ok"
    assert created is not None
    updated = record_parse_result("YES", "ok")
    assert updated is not None
    assert has_eval_signal() is True
    # Clear + create new goal under lock clears prior signal semantics
    second = GoalState(
        condition="second",
        created_at="t2",
        status="pursuing",
        active=True,
    )
    create_goal_atomic(second, force=True)
    assert has_eval_signal() is False


def test_create_goal_atomic_exists(goal_home: Path) -> None:
    from cursor_goal.state import create_goal_atomic

    first = GoalState(condition="a", created_at="t", status="pursuing", active=True)
    assert create_goal_atomic(first)[1] == "ok"
    second = GoalState(condition="b", created_at="t2", status="pursuing", active=True)
    existing, status = create_goal_atomic(second)
    assert status == "exists"
    assert existing is not None
    assert existing.condition == "a"


def test_clamp_turn_budget_bounds() -> None:
    from cursor_goal.state import clamp_turn_budget

    assert clamp_turn_budget(20) == 20
    assert clamp_turn_budget(999) == 500
    with pytest.raises(ValueError):
        clamp_turn_budget(0)


def test_refuse_if_data_dir_insecure_message(
    goal_home: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from cursor_goal import path_trust as path_trust_mod
    from cursor_goal import state as state_mod

    monkeypatch.setattr(path_trust_mod, "data_dir_is_insecure", lambda path=None: True)
    monkeypatch.setattr(
        path_trust_mod, "data_dir", lambda check_writable=False: goal_home
    )
    msg = state_mod.refuse_if_data_dir_insecure()
    assert msg is not None
    assert "insecure" in msg

    monkeypatch.setattr(path_trust_mod, "data_dir_is_insecure", lambda path=None: False)
    assert state_mod.refuse_if_data_dir_insecure() is None


def test_data_dir_is_insecure_symlink_path(
    monkeypatch: pytest.MonkeyPatch,
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
            return True

        def lstat(self) -> object:
            raise AssertionError("lstat should not run for symlink")

    monkeypatch.setattr(state_mod.os, "name", "posix")
    assert state_mod.data_dir_is_insecure(Fake()) is True  # type: ignore[arg-type]


def test_real_symlink_data_dir_is_insecure(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """CURSOR_GOAL_DATA pointing at a symlink must be treated as insecure."""
    from cursor_goal import state as state_mod

    target = tmp_path / "real-data"
    target.mkdir()
    link = tmp_path / "link-data"
    try:
        link.symlink_to(target, target_is_directory=True)
    except OSError as exc:
        pytest.skip(f"Cannot create symlinks without elevated privileges: {exc}")

    assert state_mod.path_has_symlink_or_reparse(link) is True
    monkeypatch.setenv("CURSOR_GOAL_DATA", str(link))
    assert state_mod.data_dir_is_insecure() is True
    assert (
        state_mod.path_has_symlink_or_reparse(state_mod.configured_data_dir_path())
        is True
    )


def test_path_has_symlink_or_reparse_mocked(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    from cursor_goal import path_trust as path_trust_mod
    from cursor_goal import state as state_mod

    normal = tmp_path / "normal-data"
    normal.mkdir()
    assert state_mod.path_has_symlink_or_reparse(normal) is False

    monkeypatch.setattr(
        path_trust_mod, "_windows_path_is_reparse_point", lambda _p: True
    )
    monkeypatch.setattr(path_trust_mod.os, "name", "nt")
    assert state_mod.path_has_symlink_or_reparse(normal) is True


def test_data_dir_refuses_mkdir_through_link(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from cursor_goal import path_trust as path_trust_mod
    from cursor_goal import state as state_mod

    linkish = tmp_path / "linkish"
    monkeypatch.setenv("CURSOR_GOAL_DATA", str(linkish))
    monkeypatch.setattr(path_trust_mod, "path_has_symlink_or_reparse", lambda _p: True)
    result = state_mod.data_dir(check_writable=False)
    assert not linkish.exists()
    assert "linkish" in str(result)


def test_from_dict_clamps_oversized_condition() -> None:
    from cursor_goal.state import MAX_FIELD_CHARS, GoalState

    state = GoalState.from_dict(
        {
            "active": True,
            "condition": "c" * (MAX_FIELD_CHARS + 50),
            "validation_command": "v" * (MAX_FIELD_CHARS + 10),
            "turn_budget": 5,
            "turns_used": 0,
            "status": "pursuing",
            "schema_version": 1,
            "workdir": "w" * (MAX_FIELD_CHARS + 5),
        }
    )
    assert len(state.condition) == MAX_FIELD_CHARS
    assert len(state.validation_command) == MAX_FIELD_CHARS
    assert len(state.workdir) == MAX_FIELD_CHARS


def test_workdir_field_setter() -> None:
    from cursor_goal.state import GoalState, _apply_field

    state = GoalState()
    _apply_field(state, "workdir", "/tmp/project")
    assert state.workdir == "/tmp/project"
    _apply_field(state, "workdir", None)
    assert state.workdir == ""


def test_data_dir_is_insecure_none_with_link(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from cursor_goal import path_trust as path_trust_mod
    from cursor_goal import state as state_mod

    monkeypatch.setenv("CURSOR_GOAL_DATA", str(tmp_path / "cfg"))
    monkeypatch.setattr(path_trust_mod, "path_has_symlink_or_reparse", lambda _p: True)
    assert state_mod.data_dir_is_insecure() is True
    msg = state_mod.refuse_if_data_dir_insecure()
    assert msg is not None
    assert "insecure" in msg


def test_data_dir_is_insecure_race_recheck(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from cursor_goal import path_trust as path_trust_mod
    from cursor_goal import state as state_mod

    data = tmp_path / "data"
    data.mkdir()
    monkeypatch.setenv("CURSOR_GOAL_DATA", str(data))
    calls = {"n": 0}

    def flaky(_p: Path) -> bool:
        calls["n"] += 1
        return calls["n"] >= 2

    monkeypatch.setattr(path_trust_mod, "path_has_symlink_or_reparse", flaky)
    assert state_mod.data_dir_is_insecure() is True


def test_data_dir_is_insecure_windows_reparse_target(
    goal_home: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from cursor_goal import path_trust as path_trust_mod
    from cursor_goal import state as state_mod

    monkeypatch.setattr(path_trust_mod.os, "name", "nt")
    monkeypatch.setattr(path_trust_mod, "path_has_symlink_or_reparse", lambda _p: False)
    monkeypatch.setattr(
        path_trust_mod, "_windows_path_is_reparse_point", lambda _p: True
    )
    assert state_mod.data_dir_is_insecure(goal_home) is True


def test_refuse_if_data_dir_insecure_nt_message(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from cursor_goal import path_trust as path_trust_mod
    from cursor_goal import state as state_mod

    monkeypatch.setattr(path_trust_mod, "data_dir_is_insecure", lambda: True)
    monkeypatch.setattr(path_trust_mod.os, "name", "nt")
    monkeypatch.setenv("CURSOR_GOAL_DATA", str(tmp_path / "d"))
    msg = state_mod.refuse_if_data_dir_insecure()
    assert msg is not None
    assert "junction" in msg or "reparse" in msg


def test_refuse_if_data_dir_insecure_posix_message(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from cursor_goal import path_trust as path_trust_mod
    from cursor_goal import state as state_mod

    monkeypatch.setattr(path_trust_mod, "data_dir_is_insecure", lambda: True)
    monkeypatch.setattr(path_trust_mod.os, "name", "posix")
    monkeypatch.setattr(
        path_trust_mod, "configured_data_dir_path", lambda: tmp_path / "d"
    )
    monkeypatch.setattr(
        path_trust_mod, "_absolute_without_resolve", lambda p: tmp_path / "d"
    )
    msg = state_mod.refuse_if_data_dir_insecure()
    assert msg is not None
    assert "chmod" in msg or "world-writable" in msg


def test_data_dir_is_insecure_mode_bits(
    goal_home: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from cursor_goal import path_trust as path_trust_mod
    from cursor_goal import state as state_mod

    class FakeStat:
        st_mode = 0o777
        st_uid = 0

    monkeypatch.setattr(path_trust_mod.os, "name", "posix")
    monkeypatch.setattr(path_trust_mod.os, "getuid", lambda: 1, raising=False)
    monkeypatch.setattr(type(goal_home), "is_symlink", lambda self: False)
    monkeypatch.setattr(type(goal_home), "lstat", lambda self: FakeStat())
    # Not owned by uid 1 → insecure
    assert state_mod.data_dir_is_insecure(goal_home) is True

    FakeStat.st_uid = 1
    assert state_mod.data_dir_is_insecure(goal_home) is True  # still world-writable

    FakeStat.st_mode = 0o700
    assert state_mod.data_dir_is_insecure(goal_home) is False

    monkeypatch.setattr(type(goal_home), "is_symlink", lambda self: True)
    assert state_mod.data_dir_is_insecure(goal_home) is True

    monkeypatch.setattr(type(goal_home), "is_symlink", lambda self: False)
    monkeypatch.setattr(
        path_trust_mod, "_windows_path_is_reparse_point", lambda _p: False
    )
    monkeypatch.setattr(path_trust_mod.os, "name", "nt")
    assert state_mod.data_dir_is_insecure(goal_home) is False


def test_get_logger_invalid_level_and_log_file(
    goal_home: Path, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    import logging

    from cursor_goal import logging_config as log_mod

    monkeypatch.setenv("CURSOR_GOAL_LOG", "NOTALEVEL")
    monkeypatch.setenv("CURSOR_GOAL_LOG_FILE", str(tmp_path / "cg.log"))
    log_mod._reset_for_tests()
    name = f"cursor_goal.test_log_{os.getpid()}"
    existing = logging.getLogger(name)
    existing.handlers.clear()
    logger = log_mod.get_logger(name)
    root = logging.getLogger("cursor_goal")
    assert root.level == logging.WARNING
    logger.warning("hello durable")
    log_path = tmp_path / "cg.log"
    assert log_path.is_file()
    assert "hello durable" in log_path.read_text(encoding="utf-8")

    # Default data-dir log path via CURSOR_GOAL_LOG_FILE=1
    log_mod._reset_for_tests()
    existing2 = logging.getLogger(name + "_b")
    existing2.handlers.clear()
    monkeypatch.setenv("CURSOR_GOAL_LOG_FILE", "1")
    monkeypatch.delenv("CURSOR_GOAL_LOG", raising=False)
    monkeypatch.setenv("CURSOR_GOAL_DATA", str(goal_home))
    logger_b = log_mod.get_logger(name + "_b")
    root_b = logging.getLogger("cursor_goal")
    assert root_b.level == logging.INFO
    logger_b.error("via data dir")
    assert (goal_home / "cursor-goal.log").is_file()


def test_get_logger_log_file_oserror(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    import logging

    from cursor_goal import logging_config as log_mod

    bad = tmp_path / "missing" / "nested" / "x.log"
    # Parent cannot be created if we point at a file-as-directory.
    blocker = tmp_path / "missing"
    blocker.write_text("not a dir", encoding="utf-8")
    monkeypatch.setenv("CURSOR_GOAL_LOG_FILE", str(bad))
    log_mod._reset_for_tests()
    name = f"cursor_goal.test_log_fail_{os.getpid()}"
    logging.getLogger(name).handlers.clear()
    log_mod.get_logger(name)
    root = logging.getLogger("cursor_goal")
    assert root.handlers  # stderr still attached


def test_default_log_path_home(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    from cursor_goal import logging_config as log_mod

    monkeypatch.delenv("CURSOR_GOAL_DATA", raising=False)
    home = tmp_path / "home"
    home.mkdir()
    monkeypatch.setenv("HOME", str(home))
    monkeypatch.setenv("USERPROFILE", str(home))
    path = log_mod._default_log_path()
    assert path.name == "cursor-goal.log"
    assert str(home) in str(path) or "cursor-goal" in str(path)


def test_maybe_chmod_log_file_paths(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    from cursor_goal import logging_config as log_mod

    path = tmp_path / "x.log"
    path.write_text("x", encoding="utf-8")
    monkeypatch.setattr(log_mod.os, "name", "nt")
    log_mod._maybe_chmod_log_file(path)  # no-op on Windows name

    monkeypatch.setattr(log_mod.os, "name", "posix")

    def boom(_p: object, _m: int) -> None:
        raise OSError("denied")

    monkeypatch.setattr(log_mod.os, "chmod", boom)
    log_mod._maybe_chmod_log_file(path)  # swallows OSError

    called: list[int] = []

    def ok(_p: object, mode: int) -> None:
        called.append(mode)

    monkeypatch.setattr(log_mod.os, "chmod", ok)
    log_mod._maybe_chmod_log_file(path)
    assert called == [0o600]


def test_atomic_write_text_roundtrip(goal_home: Path) -> None:
    from cursor_goal.state import atomic_write_text

    path = goal_home / "atomic.txt"
    atomic_write_text(path, "payload\n")
    assert path.read_text(encoding="utf-8") == "payload\n"


def test_atomic_write_cleans_tmp_on_failure(
    goal_home: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from cursor_goal import state as state_mod

    path = goal_home / "atomic-fail.txt"
    real_replace = Path.replace

    def boom_replace(self: Path, target: Path) -> Path:
        if self.suffix == ".tmp" or str(self).endswith(".tmp"):
            raise OSError("replace failed")
        return real_replace(self, target)

    monkeypatch.setattr(Path, "replace", boom_replace)
    with pytest.raises(OSError):
        state_mod.atomic_write_text(path, "x")
    assert list(goal_home.glob("atomic-fail.txt.*.tmp")) == []


def test_atomic_write_posix_branch_and_unlink_oserror(
    goal_home: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from cursor_goal import state as state_mod

    path = goal_home / "atomic-posix.txt"
    monkeypatch.setattr(state_mod.os, "name", "posix")
    state_mod.atomic_write_text(path, "posix-mode\n")
    assert path.read_text(encoding="utf-8") == "posix-mode\n"

    def boom_replace(self: Path, target: Path) -> Path:
        raise OSError("replace failed")

    def boom_unlink(self: Path, missing_ok: bool = False) -> None:
        raise OSError("busy")

    monkeypatch.setattr(Path, "replace", boom_replace)
    monkeypatch.setattr(Path, "unlink", boom_unlink)
    with pytest.raises(OSError):
        state_mod.atomic_write_text(goal_home / "atomic-posix2.txt", "y")


def test_stop_skips_last_response_when_insecure(
    goal_home: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    import cursor_goal.stop as stop_mod

    monkeypatch.setenv("CURSOR_GOAL_STOP_DRAIN_MS", "0")
    monkeypatch.setattr(
        stop_mod, "refuse_if_data_dir_insecure", lambda: "[goal] Error: insecure"
    )
    out = io.StringIO()
    from contextlib import redirect_stdout

    with redirect_stdout(out):
        stop_mod.emit({"followup_message": "x"})
    assert not (goal_home / "last-stop-response.json").is_file()


def test_scrubbed_env_adds_comspec_on_windows(monkeypatch: pytest.MonkeyPatch) -> None:
    from cursor_goal import validation as val_mod

    monkeypatch.setattr(val_mod.os, "name", "nt")
    scrubbed = val_mod.scrubbed_validation_env(
        {"PATH": "C:\\Windows", "ComSpec": "C:\\Windows\\system32\\cmd.exe"}
    )
    assert scrubbed.get("COMSPEC") == "C:\\Windows\\system32\\cmd.exe" or scrubbed.get(
        "ComSpec"
    )


def test_stop_redact_without_condition_marker() -> None:
    from cursor_goal.stop import _redact_followup_for_disk

    assert _redact_followup_for_disk("[GOAL] keep me") == "[GOAL] keep me"


def test_absolute_without_resolve_relative(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    from cursor_goal import state as state_mod

    monkeypatch.chdir(tmp_path)
    out = state_mod._absolute_without_resolve(Path("rel-data"))
    assert out.is_absolute()
    assert out.name == "rel-data"


def test_chmod_dir_private_oserror(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    from cursor_goal import state as state_mod

    monkeypatch.setattr(state_mod.os, "name", "posix")

    def boom(_p: object, _m: int) -> None:
        raise OSError("denied")

    monkeypatch.setattr(state_mod.os, "chmod", boom)
    state_mod._chmod_dir_private(tmp_path)


def test_get_logger_existing_stream_and_child_handlers(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    import logging

    from cursor_goal import logging_config as log_mod

    log_mod._reset_for_tests()
    root = logging.getLogger("cursor_goal")
    existing = logging.StreamHandler()
    root.addHandler(existing)
    monkeypatch.delenv("CURSOR_GOAL_LOG_FILE", raising=False)
    # Reconfigure with StreamHandler already present (skip add branch).
    log_mod._CONFIGURED = False
    log_mod.get_logger("cursor_goal")
    child_name = f"cursor_goal.child_{os.getpid()}"
    child = logging.getLogger(child_name)
    child.addHandler(logging.StreamHandler())
    assert child.handlers
    log_mod.get_logger(child_name)
    assert child.handlers == []
    log_mod._reset_for_tests()


def test_windows_username_rejects_unsafe(monkeypatch: pytest.MonkeyPatch) -> None:
    from cursor_goal import win_acl

    monkeypatch.setenv("USERNAME", "bad;user")
    monkeypatch.delenv("USER", raising=False)
    monkeypatch.setattr(win_acl.os, "getlogin", lambda: "")
    assert win_acl.windows_username() is None
