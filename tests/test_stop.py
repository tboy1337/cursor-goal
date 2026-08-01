"""Tests for cursor_goal.stop stop-hook contract."""

from __future__ import annotations

import io
import json
import sys
from contextlib import redirect_stdout
from pathlib import Path
from typing import Any

import pytest

import cursor_goal.stop as stop_mod
from cursor_goal.state import GoalState
from cursor_goal.stop import cmd_stop, handle_stop
from tests.conftest import load_goal_json, run_cli


def _run_stop(payload: dict[str, Any] | str | None) -> tuple[int, dict[str, Any]]:
    if payload is None:
        raw = ""
    elif isinstance(payload, str):
        raw = payload
    else:
        raw = json.dumps(payload)
    out = io.StringIO()
    old_stdin = sys.stdin
    try:
        sys.stdin = io.StringIO(raw)
        with redirect_stdout(out):
            code = cmd_stop([])
    finally:
        sys.stdin = old_stdin
    return code, json.loads(out.getvalue())


def test_stop_empty_stdin(goal_home: Path) -> None:
    code, payload = _run_stop(None)
    assert code == 0
    assert payload == {}


def test_stop_invalid_json(goal_home: Path) -> None:
    code, payload = _run_stop("{not-json")
    assert code == 0
    assert payload == {}


def test_stop_non_completed(goal_home: Path) -> None:
    run_cli("manage", "create", 'fix "quoted" goal')
    code, payload = _run_stop({"status": "aborted", "loop_count": 0})
    assert code == 0
    assert payload == {}


def test_stop_continues_when_pursuing(goal_home: Path) -> None:
    run_cli("manage", "create", 'fix "quoted" goal')
    code, payload = _run_stop({"status": "completed", "loop_count": 0})
    assert code == 0
    assert "followup_message" in payload
    assert "[GOAL]" in payload["followup_message"]
    assert 'fix "quoted" goal' in payload["followup_message"]
    assert load_goal_json(goal_home)["turns_used"] == 1


def test_stop_budget_limit(goal_home: Path) -> None:
    run_cli("manage", "create", "almost done", "--budget", "1")
    code, payload = _run_stop({"status": "completed", "loop_count": 0})
    assert code == 0
    assert "BUDGET" in payload["followup_message"]
    data = load_goal_json(goal_home)
    assert data["status"] == "budget-limited"
    assert data["active"] is False


def test_stop_with_validation_command_reminds_in_turn(goal_home: Path) -> None:
    """Stop hook must not run validation; only remind the agent."""
    run_cli("manage", "create", "ok", "--test", "echo hi")
    response = handle_stop({"status": "completed", "loop_count": 1})
    msg = response["followup_message"]
    assert "Run validation in-turn" in msg
    assert "echo hi" in msg
    assert "PASSED" not in msg
    assert "FAILED" not in msg


def test_stop_oversized_stdin(goal_home: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    run_cli("manage", "create", "ok")
    huge = "{" + ("x" * (stop_mod.MAX_STDIN_BYTES + 10))

    class HugeStdin:
        def read(self, _n: int = -1) -> str:
            return huge

    monkeypatch.setattr(sys, "stdin", HugeStdin())
    out = io.StringIO()
    with redirect_stdout(out):
        code = cmd_stop([])
    assert code == 0
    assert json.loads(out.getvalue()) == {}


def test_handle_stop_rejects_non_dict(goal_home: Path) -> None:
    assert handle_stop(None) == {}
    assert handle_stop("x") == {}  # type: ignore[arg-type]


def test_handle_stop_inactive_or_paused(goal_home: Path) -> None:
    run_cli("manage", "create", "g")
    run_cli("manage", "pause")
    assert handle_stop({"status": "completed"}) == {}
    run_cli("manage", "clear")
    assert handle_stop({"status": "completed"}) == {}


def test_handle_stop_mutate_oserror(
    goal_home: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    run_cli("manage", "create", "g", "--test", "echo ok")

    def boom(_mutator: object) -> GoalState | None:
        raise OSError("disk full")

    monkeypatch.setattr(stop_mod, "mutate_goal", boom)
    response = handle_stop({"status": "completed", "loop_count": 0})
    assert "followup_message" in response
    assert "[GOAL]" in response["followup_message"]


def test_cmd_stop_stdin_oserror(
    goal_home: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    class BadStdin:
        def read(self, _n: int = -1) -> str:
            raise OSError("broken pipe")

    monkeypatch.setattr(sys, "stdin", BadStdin())
    out = io.StringIO()
    with redirect_stdout(out):
        code = cmd_stop([])
    assert code == 0
    assert json.loads(out.getvalue()) == {}


def test_cmd_stop_non_object_json(goal_home: Path) -> None:
    code, payload = _run_stop("[1,2,3]")
    assert code == 0
    assert payload == {}


def test_cmd_stop_unhandled_error(
    goal_home: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    def boom(_payload: object) -> dict[str, Any]:
        raise RuntimeError("unexpected")

    monkeypatch.setattr(stop_mod, "handle_stop", boom)
    code, payload = _run_stop({"status": "completed"})
    assert code == 0
    assert payload == {}


def test_emit_drain_sleep_respects_env(
    goal_home: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    calls: list[float] = []

    def fake_sleep(seconds: float) -> None:
        calls.append(seconds)

    monkeypatch.setenv("CURSOR_GOAL_STOP_DRAIN_MS", "50")
    monkeypatch.setattr(stop_mod.time, "sleep", fake_sleep)
    out = io.StringIO()
    with redirect_stdout(out):
        stop_mod.emit({"followup_message": "hi"})
    assert calls == [0.05]
    assert '"followup_message"' in out.getvalue()


def test_emit_drain_zero_skips_sleep(
    goal_home: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    calls: list[float] = []
    monkeypatch.setenv("CURSOR_GOAL_STOP_DRAIN_MS", "0")
    monkeypatch.setattr(stop_mod.time, "sleep", lambda s: calls.append(s))
    out = io.StringIO()
    with redirect_stdout(out):
        stop_mod.emit({})
    assert calls == []


def test_drain_ms_invalid_falls_back(
    goal_home: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("CURSOR_GOAL_STOP_DRAIN_MS", "nope")
    assert stop_mod._drain_ms() == stop_mod._default_drain_ms()


def test_drain_ms_clamps_huge_values(
    goal_home: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("CURSOR_GOAL_STOP_DRAIN_MS", "99999")
    assert stop_mod._drain_ms() == stop_mod.MAX_DRAIN_MS


def test_default_drain_ms_windows(
    goal_home: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.delenv("CURSOR_GOAL_STOP_DRAIN_MS", raising=False)
    monkeypatch.setattr(stop_mod.os, "name", "nt")
    assert stop_mod._default_drain_ms() == stop_mod.DEFAULT_DRAIN_MS_WINDOWS
    assert stop_mod._drain_ms() == stop_mod.DEFAULT_DRAIN_MS_WINDOWS


def test_default_drain_ms_posix(
    goal_home: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.delenv("CURSOR_GOAL_STOP_DRAIN_MS", raising=False)
    monkeypatch.setattr(stop_mod.os, "name", "posix")
    assert stop_mod._default_drain_ms() == stop_mod.DEFAULT_DRAIN_MS
    assert stop_mod._drain_ms() == stop_mod.DEFAULT_DRAIN_MS


def test_emit_always_writes_last_stop_response(
    goal_home: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("CURSOR_GOAL_STOP_DRAIN_MS", "0")
    out = io.StringIO()
    with redirect_stdout(out):
        stop_mod.emit({"followup_message": "[GOAL] test"})
    path = goal_home / "last-stop-response.json"
    assert path.is_file()
    data = json.loads(path.read_text(encoding="utf-8"))
    assert data["payload"]["followup_message"] == "[GOAL] test"
    assert "ts" in data
    assert "pid" in data


def test_last_stop_redacts_toward_and_goal_conditions(
    goal_home: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("CURSOR_GOAL_STOP_DRAIN_MS", "0")
    cases = (
        (
            "[GOAL] Continue working toward: secret-customer-name",
            "toward: <redacted>",
        ),
        (
            "[GOAL] Run validation. Goal: secret-customer-name",
            "Goal: <redacted>",
        ),
        (
            "[GOAL BUDGET] summarize progress toward: secret-customer-name",
            "progress toward: <redacted>",
        ),
    )
    for message, expected_tail in cases:
        out = io.StringIO()
        with redirect_stdout(out):
            stop_mod.emit({"followup_message": message})
        data = json.loads(
            (goal_home / "last-stop-response.json").read_text(encoding="utf-8")
        )
        stored = data["payload"]["followup_message"]
        assert "secret-customer-name" not in stored
        assert expected_tail in stored
        assert "secret-customer-name" in out.getvalue()


def test_fsync_stdout_swallows_oserror(
    goal_home: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("CURSOR_GOAL_STOP_DRAIN_MS", "0")

    class _Out:
        def write(self, _s: str) -> int:
            return 0

        def flush(self) -> None:
            return None

        def fileno(self) -> int:
            return 1

    monkeypatch.setattr(sys, "stdout", _Out())
    monkeypatch.setattr(
        stop_mod.os, "fsync", lambda _fd: (_ for _ in ()).throw(OSError("no fsync"))
    )
    stop_mod.emit({})


def test_last_stop_write_oserror_is_swallowed(
    goal_home: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("CURSOR_GOAL_STOP_DRAIN_MS", "0")

    def boom(_path: object, _text: str) -> None:
        raise OSError("readonly")

    monkeypatch.setattr(stop_mod, "atomic_write_text", boom)
    out = io.StringIO()
    with redirect_stdout(out):
        stop_mod.emit({"followup_message": "x"})
    assert '"followup_message"' in out.getvalue()


def test_handle_stop_mutate_oserror_inactive(
    goal_home: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    def boom(_mutator: object) -> GoalState | None:
        raise OSError("disk full")

    monkeypatch.setattr(stop_mod, "mutate_goal", boom)
    monkeypatch.setattr(stop_mod, "snapshot_goal", lambda: None)
    assert handle_stop({"status": "completed", "loop_count": 0}) == {}


def test_stop_singleflight_second_emits_empty(
    goal_home: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    run_cli("manage", "create", "singleflight")
    monkeypatch.setenv("CURSOR_GOAL_STOP_DRAIN_MS", "0")
    held = stop_mod._try_acquire_singleflight()
    assert held is not None
    try:
        code, payload = _run_stop({"status": "completed", "loop_count": 0})
        assert code == 0
        assert payload == {}
    finally:
        stop_mod._release_singleflight(held)


def test_stop_budget_disarms_wake(goal_home: Path) -> None:
    run_cli("manage", "create", "budget wake", "--budget", "1")
    from cursor_goal.wake import arm

    arm(interval=5)
    code, payload = _run_stop({"status": "completed", "loop_count": 0})
    assert code == 0
    assert "BUDGET" in payload["followup_message"]
    assert not (goal_home / "wake.json").is_file()


def test_fail_open_continue_cap(
    goal_home: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    run_cli("manage", "create", "fail open")

    def boom(_mutator: object) -> GoalState | None:
        raise OSError("disk full")

    monkeypatch.setattr(stop_mod, "mutate_goal", boom)
    for _ in range(stop_mod.MAX_FAIL_OPEN_CONTINUES):
        resp = handle_stop({"status": "completed", "loop_count": 0})
        assert "followup_message" in resp
    assert handle_stop({"status": "completed", "loop_count": 0}) == {}


def test_release_singleflight_none(goal_home: Path) -> None:
    stop_mod._release_singleflight(None)


def test_fail_open_counter_corrupt(goal_home: Path) -> None:
    path = stop_mod._fail_open_continue_count_path()
    path.write_text("not-int\n", encoding="utf-8")
    assert stop_mod._read_fail_open_continues() == 0


def test_fail_open_write_oserror(
    goal_home: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    def boom(_path: object, _text: str) -> None:
        raise OSError("readonly")

    monkeypatch.setattr(stop_mod, "atomic_write_text", boom)
    stop_mod._write_fail_open_continues(1)


def test_budget_disarm_oserror(
    goal_home: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    run_cli("manage", "create", "disarm boom", "--budget", "1")

    def boom(**_k: object) -> bool:
        raise OSError("cannot kill")

    monkeypatch.setattr(stop_mod, "wake_disarm", boom)
    resp = handle_stop({"status": "completed", "loop_count": 0})
    assert "BUDGET" in resp["followup_message"]


def test_release_singleflight_close_oserror(
    goal_home: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    class BoomHandle:
        def seek(self, *_a: object, **_k: object) -> None:
            return None

        def fileno(self) -> int:
            return 1

        def close(self) -> None:
            raise OSError("already closed")

    if sys.platform == "win32":
        import msvcrt

        monkeypatch.setattr(msvcrt, "locking", lambda *_a, **_k: None)
    stop_mod._release_singleflight(BoomHandle())  # type: ignore[arg-type]


def test_clear_fail_open_oserror(
    goal_home: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    class Boom:
        def unlink(self, *, missing_ok: bool = False) -> None:
            raise OSError("busy")

    monkeypatch.setattr(stop_mod, "_fail_open_continue_count_path", lambda: Boom())
    stop_mod._clear_fail_open_continues()


def test_handle_stop_refuses_insecure_data_dir(
    goal_home: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    run_cli("manage", "create", "insecure stop")
    monkeypatch.setattr(
        stop_mod,
        "refuse_if_data_dir_insecure",
        lambda: "[goal] Error: data directory is insecure",
    )
    assert handle_stop({"status": "completed", "loop_count": 0}) == {}
