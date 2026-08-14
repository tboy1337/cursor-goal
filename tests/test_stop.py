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
from cursor_goal.stop import cmd_stop, handle_stop, handle_subagent_stop
from cursor_goal.wake import arm
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
    # Live followup keeps a usable condition; disk store redacts separately.
    assert 'fix "quoted" goal' in payload["followup_message"]
    assert "toward:" in payload["followup_message"]
    assert load_goal_json(goal_home)["turns_used"] == 1
    disk = json.loads(
        (goal_home / "last-stop-response.json").read_text(encoding="utf-8")
    )
    stored = disk["payload"]["followup_message"]
    assert "toward: <redacted>" in stored
    assert 'fix "quoted" goal' not in stored


def test_stop_budget_limit(goal_home: Path) -> None:
    run_cli("manage", "create", "almost done", "--budget", "1")
    code, payload = _run_stop({"status": "completed", "loop_count": 0})
    assert code == 0
    assert "BUDGET" in payload["followup_message"]
    assert "almost done" in payload["followup_message"]
    data = load_goal_json(goal_home)
    assert data["status"] == "budget-limited"
    assert data["active"] is False
    disk = json.loads(
        (goal_home / "last-stop-response.json").read_text(encoding="utf-8")
    )
    assert "progress toward: <redacted>" in disk["payload"]["followup_message"]
    assert "almost done" not in disk["payload"]["followup_message"]


def test_stop_with_validation_command_reminds_in_turn(goal_home: Path) -> None:
    """Stop hook must not run validation; only remind the agent."""
    run_cli("manage", "create", "secret-condition", "--test", "echo hi")
    response = handle_stop({"status": "completed", "loop_count": 1})
    msg = response["followup_message"]
    assert "Run validation in-turn" in msg
    assert "echo hi" in msg
    assert "Goal: secret-condition" in msg
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


def test_stop_singleflight_second_is_silent(
    goal_home: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Loser must not write stdout or last-stop-response (avoids clobbering)."""
    run_cli("manage", "create", "singleflight")
    monkeypatch.setenv("CURSOR_GOAL_STOP_DRAIN_MS", "0")
    held = stop_mod._try_acquire_singleflight()
    assert held is not None
    try:
        out = io.StringIO()
        old_stdin = sys.stdin
        try:
            sys.stdin = io.StringIO(
                json.dumps({"status": "completed", "loop_count": 0})
            )
            with redirect_stdout(out):
                code = cmd_stop([])
        finally:
            sys.stdin = old_stdin
        assert code == 0
        assert out.getvalue() == ""
        last = goal_home / "last-stop-response.json"
        # Loser must not create/overwrite diagnostics.
        assert not last.is_file() or "singleflight" not in last.read_text(
            encoding="utf-8"
        )
    finally:
        stop_mod._release_singleflight(held)


def test_stop_singleflight_refuses_insecure(
    goal_home: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(
        stop_mod,
        "refuse_if_data_dir_insecure",
        lambda: "[goal] Error: insecure",
    )
    code, payload = _run_stop({"status": "completed", "loop_count": 0})
    assert code == 0
    assert payload == {}


def test_stop_singleflight_refuses_acl(
    goal_home: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(
        stop_mod,
        "refuse_if_acl_harden_failed",
        lambda: "[goal] Error: ACL harden failed",
    )
    code, payload = _run_stop({"status": "completed", "loop_count": 0})
    assert code == 0
    assert payload == {}


def test_cmd_stop_emit_oserror_fail_open(
    goal_home: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    run_cli("manage", "create", "emit fail")
    monkeypatch.setenv("CURSOR_GOAL_STOP_DRAIN_MS", "0")

    def boom(_payload: dict[str, Any]) -> None:
        raise OSError("stdout broken")

    monkeypatch.setattr(stop_mod, "emit", boom)
    code, payload = _run_stop({"status": "completed", "loop_count": 0})
    assert code == 0
    assert payload == {}


def test_cmd_stop_emit_and_failopen_write_fail(
    goal_home: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    run_cli("manage", "create", "emit fail2")
    monkeypatch.setenv("CURSOR_GOAL_STOP_DRAIN_MS", "0")

    def boom(_payload: dict[str, Any]) -> None:
        raise OSError("stdout broken")

    class BoomStdout:
        def write(self, _s: str) -> int:
            raise OSError("write failed")

        def flush(self) -> None:
            return None

    monkeypatch.setattr(stop_mod, "emit", boom)
    monkeypatch.setattr(sys, "stdout", BoomStdout())
    old = sys.stdin
    try:
        sys.stdin = io.StringIO(json.dumps({"status": "completed", "loop_count": 0}))
        code = cmd_stop([])
    finally:
        sys.stdin = old
    assert code == 0


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


def test_fail_open_accounts_against_budget(
    goal_home: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    run_cli("manage", "create", "fail open budget", "--budget", "2")

    def boom(_mutator: object) -> GoalState | None:
        raise OSError("disk full")

    monkeypatch.setattr(stop_mod, "mutate_goal", boom)
    # turns_used=0; first fail-open count=1 → remaining ok; count=2 → exhausted
    assert "followup_message" in handle_stop({"status": "completed", "loop_count": 0})
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


def test_budget_limited_response_wake_branch(goal_home: Path) -> None:
    """When wake_ticks (not turns_used) trips the budget, say so explicitly.

    ``_budget_limited_response`` is a pure formatter over a ``GoalState``
    snapshot; the wake-tripped combination (turns_used below its budget,
    wake_ticks at/above its budget) is exercised directly here since the
    ``mutate_goal``/``from_dict`` round trip only ever reaches
    ``handle_stop``'s own mutator with turns_used freshly at the turn
    budget (see ``test_stop_budget_limit``).
    """
    state = GoalState(
        condition="wake budget trip",
        turn_budget=20,
        turns_used=3,
        wake_budget=5,
        wake_ticks=5,
    )
    response = stop_mod._budget_limited_response(state)
    assert "Wake tick limit (5)" in response["followup_message"]
    assert "Turn limit" not in response["followup_message"]


def test_budget_limited_response_turn_branch(goal_home: Path) -> None:
    state = GoalState(
        condition="turn budget trip",
        turn_budget=5,
        turns_used=5,
        wake_budget=200,
        wake_ticks=0,
    )
    response = stop_mod._budget_limited_response(state)
    assert "Turn limit (5)" in response["followup_message"]
    assert "Wake tick limit" not in response["followup_message"]


def test_stop_generation_id_extraction() -> None:
    assert stop_mod._stop_generation_id(None) == ""
    assert stop_mod._stop_generation_id({}) == ""
    assert stop_mod._stop_generation_id({"generation_id": 123}) == ""
    assert stop_mod._stop_generation_id({"generation_id": "  gen-1  "}) == "gen-1"


def test_stop_dedupe_roundtrip(goal_home: Path) -> None:
    assert stop_mod._read_stop_dedupe() is None
    assert stop_mod._cached_stop_response_for("gen-1") is None
    stop_mod._remember_stop_response("gen-1", {"followup_message": "hi"})
    cached = stop_mod._cached_stop_response_for("gen-1")
    assert cached == {"followup_message": "hi"}
    # A different generation_id is a miss.
    assert stop_mod._cached_stop_response_for("gen-2") is None
    # Empty generation_id never hits the cache.
    assert stop_mod._cached_stop_response_for("") is None
    stop_mod._remember_stop_response("", {"ignored": True})


def test_stop_dedupe_corrupt_file_returns_none(goal_home: Path) -> None:
    stop_mod._stop_dedupe_path().write_text("not-json", encoding="utf-8")
    assert stop_mod._read_stop_dedupe() is None
    stop_mod._stop_dedupe_path().write_text("[1, 2]", encoding="utf-8")
    assert stop_mod._read_stop_dedupe() is None


def test_stop_dedupe_write_oserror_swallowed(
    goal_home: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    def boom(_path: object, _text: str) -> None:
        raise OSError("readonly")

    monkeypatch.setattr(stop_mod, "atomic_write_text", boom)
    stop_mod._write_stop_dedupe("gen-1", {})


def test_cmd_stop_sequential_dual_hooks_dedupe_by_generation_id(
    goal_home: Path,
) -> None:
    """Two sequential hook invocations for the same turn charge turns_used once."""
    run_cli("manage", "create", "dual hook dedupe")
    payload = {
        "status": "completed",
        "loop_count": 0,
        "generation_id": "turn-abc",
    }
    code1, first = _run_stop(payload)
    code2, second = _run_stop(payload)
    assert code1 == 0
    assert code2 == 0
    assert first == second
    assert load_goal_json(goal_home)["turns_used"] == 1


def test_cmd_stop_sequential_dual_hooks_dedupe_without_generation_id(
    goal_home: Path,
) -> None:
    """Payload-hash fallback still charges turns_used once for sequential dual hooks."""
    run_cli("manage", "create", "dual hook fallback")
    payload = {
        "status": "completed",
        "loop_count": 0,
        "hook_event_name": "stop",
        "conversation_id": "conv-1",
    }
    code1, first = _run_stop(payload)
    code2, second = _run_stop(payload)
    assert code1 == 0
    assert code2 == 0
    assert first == second
    assert load_goal_json(goal_home)["turns_used"] == 1


def test_stop_dedupe_key_prefers_generation_id() -> None:
    assert (
        stop_mod._stop_dedupe_key({"generation_id": "  gen-9  ", "status": "completed"})
        == "gen-9"
    )
    empty = stop_mod._stop_dedupe_key(None)
    assert empty == ""
    hashed = stop_mod._stop_dedupe_key({"status": "completed", "loop_count": 0})
    assert hashed.startswith("payload:")
    again = stop_mod._stop_dedupe_key({"loop_count": 0, "status": "completed"})
    assert hashed == again


def test_cmd_stop_dispatches_subagent_type_payload(goal_home: Path) -> None:
    run_cli("manage", "create", "subagent dispatch")
    code, payload = _run_stop(
        {"subagent_type": "goal-evaluator", "status": "completed"}
    )
    assert code == 0
    assert "evaluator subagent finished" in payload["followup_message"]


def test_handle_subagent_stop_rejects_non_dict() -> None:
    assert handle_subagent_stop(None) == {}
    assert handle_subagent_stop("x") == {}  # type: ignore[arg-type]


def test_handle_subagent_stop_wrong_subagent_type(goal_home: Path) -> None:
    run_cli("manage", "create", "wrong subagent")
    assert (
        handle_subagent_stop({"subagent_type": "other-agent", "status": "completed"})
        == {}
    )


def test_handle_subagent_stop_non_completed_status(goal_home: Path) -> None:
    run_cli("manage", "create", "not completed")
    assert (
        handle_subagent_stop({"subagent_type": "goal-evaluator", "status": "aborted"})
        == {}
    )


def test_handle_subagent_stop_refuses_insecure_data_dir(
    goal_home: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    run_cli("manage", "create", "insecure subagent stop")
    monkeypatch.setattr(
        stop_mod,
        "refuse_if_data_dir_insecure",
        lambda: "[goal] Error: data directory is insecure",
    )
    assert (
        handle_subagent_stop({"subagent_type": "goal-evaluator", "status": "completed"})
        == {}
    )


def test_handle_subagent_stop_refuses_acl_failure(
    goal_home: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    run_cli("manage", "create", "acl subagent stop")
    monkeypatch.setattr(
        stop_mod,
        "refuse_if_acl_harden_failed",
        lambda: "[goal] Error: ACL harden failed",
    )
    assert (
        handle_subagent_stop({"subagent_type": "goal-evaluator", "status": "completed"})
        == {}
    )


def test_handle_subagent_stop_no_active_goal(goal_home: Path) -> None:
    assert (
        handle_subagent_stop({"subagent_type": "goal-evaluator", "status": "completed"})
        == {}
    )


def test_handle_subagent_stop_paused_goal(goal_home: Path) -> None:
    run_cli("manage", "create", "paused subagent stop")
    run_cli("manage", "pause")
    assert (
        handle_subagent_stop({"subagent_type": "goal-evaluator", "status": "completed"})
        == {}
    )


def test_handle_subagent_stop_success(goal_home: Path) -> None:
    run_cli("manage", "create", 'evaluator "done" check')
    response = handle_subagent_stop(
        {"subagent_type": "goal-evaluator", "status": "completed"}
    )
    assert "eval parse-result" in response["followup_message"]
    assert 'evaluator "done" check' in response["followup_message"]


def test_subagent_stop_singleflight_second_is_silent(
    goal_home: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    run_cli("manage", "create", "subagent singleflight")
    monkeypatch.setenv("CURSOR_GOAL_STOP_DRAIN_MS", "0")
    held = stop_mod._try_acquire_singleflight(stop_mod.SUBAGENT_STOP_SINGLEFLIGHT_NAME)
    assert held is not None
    try:
        out = io.StringIO()
        old_stdin = sys.stdin
        try:
            sys.stdin = io.StringIO(
                json.dumps({"subagent_type": "goal-evaluator", "status": "completed"})
            )
            with redirect_stdout(out):
                code = cmd_stop([])
        finally:
            sys.stdin = old_stdin
        assert code == 0
        assert out.getvalue() == ""
    finally:
        stop_mod._release_singleflight(held)


def test_subagent_stop_unhandled_error_fail_open(
    goal_home: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    run_cli("manage", "create", "subagent unhandled")

    def boom(_payload: object) -> dict[str, Any]:
        raise RuntimeError("unexpected")

    monkeypatch.setattr(stop_mod, "handle_subagent_stop", boom)
    code, payload = _run_stop(
        {"subagent_type": "goal-evaluator", "status": "completed"}
    )
    assert code == 0
    assert payload == {}


def test_subagent_stop_emit_oserror_fail_open(
    goal_home: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    run_cli("manage", "create", "subagent emit fail")
    monkeypatch.setenv("CURSOR_GOAL_STOP_DRAIN_MS", "0")

    def boom(_payload: dict[str, Any]) -> None:
        raise OSError("stdout broken")

    monkeypatch.setattr(stop_mod, "emit_subagent_stop", boom)
    code, payload = _run_stop(
        {"subagent_type": "goal-evaluator", "status": "completed"}
    )
    assert code == 0
    assert payload == {}


def test_subagent_stop_emit_and_failopen_write_fail(
    goal_home: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    run_cli("manage", "create", "subagent emit fail2")
    monkeypatch.setenv("CURSOR_GOAL_STOP_DRAIN_MS", "0")

    def boom(_payload: dict[str, Any]) -> None:
        raise OSError("stdout broken")

    class BoomStdout:
        def write(self, _s: str) -> int:
            raise OSError("write failed")

        def flush(self) -> None:
            return None

    monkeypatch.setattr(stop_mod, "emit_subagent_stop", boom)
    monkeypatch.setattr(sys, "stdout", BoomStdout())
    old = sys.stdin
    try:
        sys.stdin = io.StringIO(
            json.dumps({"subagent_type": "goal-evaluator", "status": "completed"})
        )
        code = cmd_stop([])
    finally:
        sys.stdin = old
    assert code == 0


def test_emit_subagent_stop_writes_and_records_nudge(
    goal_home: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("CURSOR_GOAL_STOP_DRAIN_MS", "0")
    run_cli("manage", "create", "subagent nudge")
    arm(interval=5)
    out = io.StringIO()
    with redirect_stdout(out):
        stop_mod.emit_subagent_stop({"followup_message": "[GOAL] parse it"})
    assert '"followup_message"' in out.getvalue()
    path = goal_home / stop_mod.LAST_SUBAGENT_STOP_RESPONSE_NAME
    assert path.is_file()
    data = json.loads(path.read_text(encoding="utf-8"))
    assert data["payload"]["followup_message"] == "[GOAL] parse it"


def test_write_last_subagent_stop_response_refuses_insecure(
    goal_home: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(
        stop_mod,
        "refuse_if_data_dir_insecure",
        lambda: "[goal] Error: insecure",
    )
    stop_mod._write_last_subagent_stop_response({"x": 1})
    assert not (goal_home / stop_mod.LAST_SUBAGENT_STOP_RESPONSE_NAME).is_file()


def test_write_last_subagent_stop_response_refuses_acl(
    goal_home: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(
        stop_mod,
        "refuse_if_acl_harden_failed",
        lambda: "[goal] Error: ACL harden failed",
    )
    stop_mod._write_last_subagent_stop_response({"x": 1})
    assert not (goal_home / stop_mod.LAST_SUBAGENT_STOP_RESPONSE_NAME).is_file()


def test_write_last_subagent_stop_response_oserror_swallowed(
    goal_home: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    def boom(_path: object, _text: str) -> None:
        raise OSError("readonly")

    monkeypatch.setattr(stop_mod, "atomic_write_text", boom)
    stop_mod._write_last_subagent_stop_response({"x": 1})


def test_fail_open_counter_lock_failure(
    goal_home: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    run_cli("manage", "create", "lock failure")

    def boom_mutate(_mutator: object) -> GoalState | None:
        raise OSError("disk full")

    def boom_lock() -> Any:
        raise OSError("lock unavailable")

    monkeypatch.setattr(stop_mod, "mutate_goal", boom_mutate)
    monkeypatch.setattr(stop_mod, "goal_lock", boom_lock)
    assert handle_stop({"status": "completed", "loop_count": 0}) == {}


def test_release_singleflight_swallow_lock_release_error(
    goal_home: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    class BoomHandle:
        def seek(self, *_a: object, **_k: object) -> None:
            return None

        def fileno(self) -> int:
            return 1

        def close(self) -> None:
            return None

    if sys.platform == "win32":
        import msvcrt

        monkeypatch.setattr(
            msvcrt,
            "locking",
            lambda *_a, **_k: (_ for _ in ()).throw(OSError("cannot unlock")),
        )
    else:
        import fcntl

        monkeypatch.setattr(
            fcntl,
            "flock",
            lambda *_a, **_k: (_ for _ in ()).throw(OSError("cannot unlock")),
        )
    stop_mod._release_singleflight(BoomHandle())  # type: ignore[arg-type]
