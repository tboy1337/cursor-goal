"""Tests for cursor_goal.state and logging_config."""

from __future__ import annotations

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


def test_get_logger_reuses_handlers(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("CURSOR_GOAL_LOG", "DEBUG")
    name = "cursor_goal.test_logger_unique"
    # Ensure clean slate
    existing = logging.getLogger(name)
    existing.handlers.clear()
    first = get_logger(name)
    second = get_logger(name)
    assert first is second
    assert len(first.handlers) == 1


def test_load_goal_corrupt_numeric_fields(goal_home: Path) -> None:
    (goal_home / "goal.json").write_text(
        json.dumps({"condition": "x", "turn_budget": "nope", "turns_used": 0}),
        encoding="utf-8",
    )
    assert load_goal() is None


def test_save_goal_writes_schema_version(goal_home: Path) -> None:
    save_goal(GoalState(condition="c", created_at="t", status="pursuing"))
    raw = json.loads((goal_home / "goal.json").read_text(encoding="utf-8"))
    assert raw["schema_version"] == 3
    assert raw["wake_budget"] == 200
    assert raw["shell_ok"] is True


def test_from_dict_migrates_v2_wake_budget(goal_home: Path) -> None:
    del goal_home  # fixture sets CURSOR_GOAL_DATA
    state = GoalState.from_dict(
        {
            "condition": "legacy",
            "turn_budget": 20,
            "turns_used": 0,
            "wake_ticks": 5,
            "schema_version": 2,
            "status": "pursuing",
            "active": True,
        }
    )
    assert state.wake_budget == 200
    assert state.shell_ok is True
    assert state.wake_ticks == 5
    assert state.status == "pursuing"

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
    monkeypatch.setitem(sys.modules, "msvcrt", fake_msvcrt)
    monkeypatch.setattr(sys, "platform", "win32")
    state_mod._lock_acquire(Handle())
    assert calls == [(3, 1, 1)]


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
                "schema_version": 2,
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
    from cursor_goal import state as state_mod

    monkeypatch.setattr(state_mod, "data_dir_is_insecure", lambda path=None: True)
    monkeypatch.setattr(state_mod, "data_dir", lambda check_writable=False: goal_home)
    msg = state_mod.refuse_if_data_dir_insecure()
    assert msg is not None
    assert "writable" in msg

    monkeypatch.setattr(state_mod, "data_dir_is_insecure", lambda path=None: False)
    assert state_mod.refuse_if_data_dir_insecure() is None


def test_data_dir_is_insecure_mode_bits(
    goal_home: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from cursor_goal import state as state_mod

    class FakeStat:
        st_mode = 0o777

    monkeypatch.setattr(state_mod.os, "name", "posix")
    monkeypatch.setattr(
        type(goal_home),
        "stat",
        lambda self: FakeStat(),
    )
    assert state_mod.data_dir_is_insecure(goal_home) is True

    monkeypatch.setattr(state_mod.os, "name", "nt")
    assert state_mod.data_dir_is_insecure(goal_home) is False
