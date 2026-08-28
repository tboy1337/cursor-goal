"""Tests for cursor_goal.wake watchdog."""

from __future__ import annotations

import io
import json
import os
import subprocess
import threading
import time
from contextlib import redirect_stdout
from pathlib import Path

import pytest

import cursor_goal.wake as wake_mod
import cursor_goal.wake_process as wake_process_mod
from cursor_goal.state import goal_lock
from tests.conftest import run_cli


@pytest.fixture()
def wake_on(goal_home: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    monkeypatch.setenv("CURSOR_GOAL_WAKE", "1")
    monkeypatch.setenv("CURSOR_GOAL_WAKE_INTERVAL_S", "5")
    return goal_home


def _pin_windows_bins(monkeypatch: pytest.MonkeyPatch) -> None:
    """Stub System32 powershell/taskkill so ownership probes are not PATH-based."""
    monkeypatch.setattr(
        wake_process_mod,
        "_pinned_powershell",
        lambda: r"C:\Windows\System32\WindowsPowerShell\v1.0\powershell.exe",
    )
    monkeypatch.setattr(
        wake_process_mod,
        "_pinned_taskkill",
        lambda: r"C:\Windows\System32\taskkill.exe",
    )


def test_wake_disabled_skips_arm(
    goal_home: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("CURSOR_GOAL_WAKE", "0")
    assert wake_mod.arm() == {}
    code, out, _err = run_cli("wake", "arm")
    assert code == 0
    assert "disabled" in out.lower()


def test_wake_arm_writes_config(wake_on: Path) -> None:
    code, out, _err = run_cli("wake", "arm", "--interval", "10")
    assert code == 0
    assert "Wake armed" in out
    path = wake_on / "wake.json"
    assert path.is_file()
    data = json.loads(path.read_text(encoding="utf-8"))
    assert data["interval_s"] == 10
    assert data["sentinel"] == "AGENT_GOAL_WAKE"
    assert data["notify_pattern"] == wake_mod.NOTIFY_PATTERN


def test_wake_interval_clamps(wake_on: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("CURSOR_GOAL_WAKE_INTERVAL_S", "9999")
    config = wake_mod.arm()
    assert config["interval_s"] == wake_mod.MAX_INTERVAL_S
    monkeypatch.setenv("CURSOR_GOAL_WAKE_INTERVAL_S", "1")
    config = wake_mod.arm()
    assert config["interval_s"] == wake_mod.MIN_INTERVAL_S


def test_wake_interval_invalid_env(
    wake_on: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("CURSOR_GOAL_WAKE_INTERVAL_S", "nope")
    config = wake_mod.arm()
    assert config["interval_s"] == wake_mod.DEFAULT_INTERVAL_S


def test_wake_tick_emits_when_pursuing(wake_on: Path) -> None:
    assert run_cli("manage", "create", "wake goal")[0] == 0
    # manage create arms wake when CURSOR_GOAL_WAKE=1
    assert (wake_on / "wake.json").is_file()
    code, out, _err = run_cli("wake", "tick")
    assert code == 0
    assert out.startswith("AGENT_GOAL_WAKE ")
    assert wake_mod.WAKE_FOLLOWUP_MARK in out
    idx = out.find("{")
    assert idx >= 0
    payload = json.loads(out[idx:])
    assert "[GOAL]" in payload["prompt"]
    assert "FOLLOW-UP REQUIRED" in payload["prompt"]
    assert "wake goal" in payload["prompt"]
    assert "<untrusted_condition>" in payload["prompt"]
    assert "Do not manage pause" in payload["prompt"]


def test_wake_tick_silent_when_no_goal(wake_on: Path) -> None:
    wake_mod.arm(interval=5)
    code, out, _err = run_cli("wake", "tick")
    assert code == 0
    assert out == ""
    assert not (wake_on / "wake.json").is_file()


def test_wake_tick_disarms_when_paused(wake_on: Path) -> None:
    assert run_cli("manage", "create", "pause wake")[0] == 0
    assert run_cli("manage", "pause")[0] == 0
    assert not (wake_on / "wake.json").is_file()
    code, out, _err = run_cli("wake", "tick")
    assert code == 0
    assert out == ""


def test_wake_tick_silent_when_blocked(wake_on: Path) -> None:
    from cursor_goal.state import update_goal_fields

    assert run_cli("manage", "create", "blocked wake")[0] == 0
    update_goal_fields(status="blocked", active=False)
    code, out, _err = run_cli("wake", "tick")
    assert code == 0
    assert out == ""


def test_manage_create_arms_wake(wake_on: Path) -> None:
    code, out, _err = run_cli("manage", "create", "armed")
    assert code == 0
    assert "GOAL_WAKE_REQUIRED " in out
    assert "Wake armed" in out
    assert "REQUIRED next step" in out
    assert "wake loop" in out
    assert (wake_on / "wake.json").is_file()


def test_manage_clear_disarms_wake(wake_on: Path) -> None:
    assert run_cli("manage", "create", "to clear")[0] == 0
    assert (wake_on / "wake.json").is_file()
    assert run_cli("manage", "clear")[0] == 0
    assert not (wake_on / "wake.json").is_file()


def test_manage_done_disarms_wake(wake_on: Path) -> None:
    assert run_cli("manage", "create", "to done")[0] == 0
    assert run_cli("manage", "done", "--force")[0] == 0
    assert not (wake_on / "wake.json").is_file()


def test_wake_disarm_when_not_armed(wake_on: Path) -> None:
    code, out, _err = run_cli("wake", "disarm")
    assert code == 0
    assert "not armed" in out.lower()


def test_wake_status_json(wake_on: Path) -> None:
    assert run_cli("manage", "create", "status goal")[0] == 0
    code, out, _err = run_cli("wake", "status")
    assert code == 0
    data = json.loads(out)
    assert data["enabled"] is True
    assert data["armed"] is True
    assert data["goal_pursuing"] is True
    assert data["sentinel"] == "AGENT_GOAL_WAKE"
    assert data["notify_pattern"] == wake_mod.NOTIFY_PATTERN
    assert "command" in data and "wake" in data["command"]
    assert data["continuation_ready"] is False
    assert data["continuation_reason"] == "pid_dead"
    assert data["heartbeat_stale"] is False


def test_wake_unknown_and_help(goal_home: Path) -> None:
    assert run_cli("wake")[0] == 1
    assert run_cli("wake", "help")[0] == 0
    code, _out, err = run_cli("wake", "wat")
    assert code == 1
    assert "unknown" in err.lower()


def test_wake_arm_bad_interval(wake_on: Path) -> None:
    code, _out, err = run_cli("wake", "arm", "--interval", "x")
    assert code == 1
    assert "integer" in err.lower() or "Error" in err


def test_wake_arm_unexpected_args(wake_on: Path) -> None:
    code, _out, err = run_cli("wake", "arm", "extra")
    assert code == 1
    assert "unexpected" in err.lower()


def test_emit_wake_line_format(wake_on: Path) -> None:
    out = io.StringIO()
    with redirect_stdout(out):
        wake_mod.emit_wake_line("hello")
    line = out.getvalue()
    assert line.startswith("AGENT_GOAL_WAKE ")
    assert wake_mod.WAKE_FOLLOWUP_MARK in line
    idx = line.find("{")
    assert idx >= 0
    assert json.loads(line[idx:]) == {"prompt": "hello"}


def test_wake_loop_exits_when_disarmed(
    wake_on: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Short loop: disarm config mid-sleep via tiny interval + clear config."""
    monkeypatch.setenv("CURSOR_GOAL_WAKE_INTERVAL_S", "5")
    assert run_cli("manage", "create", "loop goal")[0] == 0

    sleeps: list[float] = []

    def fake_sleep(seconds: float) -> None:
        sleeps.append(seconds)
        # After first sleep slice, clear wake.json so loop exits.
        if len(sleeps) == 1:
            wake_mod.disarm(kill_loop=False)

    monkeypatch.setattr(wake_mod.time, "sleep", fake_sleep)
    code = wake_mod.run_loop(interval=5)
    assert code == 0
    assert sleeps  # interruptible sleep uses slices
    assert not (wake_on / "wake.pid").is_file()


def test_wake_loop_exits_when_not_pursuing(
    wake_on: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from cursor_goal.state import GoalState, mutate_goal

    assert run_cli("manage", "create", "loop pause")[0] == 0

    def fake_sleep(_seconds: float) -> None:
        def mutator(state: GoalState) -> None:
            state.status = "paused"
            state.active = False

        mutate_goal(mutator)

    monkeypatch.setattr(wake_mod.time, "sleep", fake_sleep)
    code = wake_mod.run_loop(interval=5)
    assert code == 0


def test_kill_pid_skips_self(wake_on: Path) -> None:
    import os

    wake_mod._kill_pid(os.getpid())  # must not terminate the test process
    wake_mod._kill_pid(-1)
    wake_mod._kill_pid(0)


def test_wake_tick_without_arm_when_pursuing(wake_on: Path) -> None:
    """Unarmed pursuing goals must not emit (budget / fail-closed)."""
    assert run_cli("manage", "create", "no arm file")[0] == 0
    wake_mod.disarm(kill_loop=False)
    code, out, _err = run_cli("wake", "tick")
    assert code == 1
    assert "AGENT_GOAL_WAKE" not in out


def test_pid_helpers(wake_on: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    wake_mod._write_pid_record(1, "test-token")
    assert wake_process_mod._read_pid() == 1
    # pid 1 may or may not be killable; _pid_alive should not raise
    assert isinstance(wake_mod._pid_alive(1), bool)
    assert wake_mod._pid_alive(-1) is False
    wake_mod._clear_pid()
    assert wake_process_mod._read_pid() is None

    # Corrupt pid file
    (wake_on / "wake.pid").write_text("not-a-pid\n", encoding="utf-8")
    assert wake_process_mod._read_pid() is None


def test_resume_rearms_wake(wake_on: Path) -> None:
    assert run_cli("manage", "create", "resume wake")[0] == 0
    assert run_cli("manage", "pause")[0] == 0
    assert not (wake_on / "wake.json").is_file()
    code, out, _err = run_cli("manage", "resume")
    assert code == 0
    assert "Wake armed" in out
    assert (wake_on / "wake.json").is_file()


def test_interval_empty_env_uses_default(
    wake_on: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("CURSOR_GOAL_WAKE_INTERVAL_S", "")
    assert wake_mod._interval_from_env_or(wake_mod.DEFAULT_INTERVAL_S) == (
        wake_mod.DEFAULT_INTERVAL_S
    )


def test_read_wake_config_corrupt_and_non_dict(wake_on: Path) -> None:
    path = wake_on / "wake.json"
    path.write_text("{not-json", encoding="utf-8")
    assert wake_mod._read_wake_config() is None
    path.write_text("[1,2,3]\n", encoding="utf-8")
    assert wake_mod._read_wake_config() is None


def test_pid_alive_exception_paths(
    wake_on: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Exercise the POSIX os.kill(pid, 0) probe regardless of host OS.

    _pid_alive() dispatches on os.name, so force the posix branch here and
    cover the Windows branch separately (see test_windows_pid_alive_*).
    """
    monkeypatch.setattr(wake_process_mod.os, "name", "posix")

    def raise_lookup(_pid: int, _sig: int) -> None:
        raise ProcessLookupError()

    monkeypatch.setattr(wake_process_mod.os, "kill", raise_lookup)
    assert wake_mod._pid_alive(12345) is False

    def raise_perm(_pid: int, _sig: int) -> None:
        raise PermissionError()

    monkeypatch.setattr(wake_process_mod.os, "kill", raise_perm)
    assert wake_mod._pid_alive(12345) is True

    def raise_os(_pid: int, _sig: int) -> None:
        raise OSError("nope")

    monkeypatch.setattr(wake_process_mod.os, "kill", raise_os)
    assert wake_mod._pid_alive(12345) is False


class _FakeKernel32:
    """Stand-in for ctypes.windll.kernel32, portable to non-Windows CI."""

    def __init__(
        self,
        *,
        handle: int = 77,
        exit_code: int = wake_process_mod._STILL_ACTIVE,
        get_exit_code_ok: bool = True,
        raise_on: str | None = None,
    ) -> None:
        self._handle = handle
        self._exit_code = exit_code
        self._get_exit_code_ok = get_exit_code_ok
        self._raise_on = raise_on
        self.closed_handles: list[int] = []

    # Method names mirror the real WinAPI (kernel32.dll) entry points.
    def OpenProcess(self, _access: int, _inherit: bool, _pid: int) -> int:
        if self._raise_on == "OpenProcess":
            raise OSError("access denied")
        return self._handle

    def GetExitCodeProcess(self, _handle: int, exit_code_ref: Any) -> int:
        if self._raise_on == "GetExitCodeProcess":
            raise OSError("bad handle")
        if self._get_exit_code_ok:
            exit_code_ref._obj.value = (
                self._exit_code
            )  # pylint: disable=protected-access
        return 1 if self._get_exit_code_ok else 0

    def CloseHandle(self, handle: int) -> int:
        self.closed_handles.append(handle)
        return 1


class _FakeWindll:
    def __init__(self, kernel32: _FakeKernel32) -> None:
        self.kernel32 = kernel32


def test_windows_pid_alive_no_win32_apis(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(wake_process_mod.ctypes, "windll", None, raising=False)
    assert wake_process_mod._windows_pid_alive(123) is False


def test_windows_pid_alive_open_process_fails(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    fake = _FakeKernel32(handle=0)
    monkeypatch.setattr(
        wake_process_mod.ctypes, "windll", _FakeWindll(fake), raising=False
    )
    assert wake_process_mod._windows_pid_alive(123) is False
    assert fake.closed_handles == []


def test_windows_pid_alive_get_exit_code_fails(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    fake = _FakeKernel32(get_exit_code_ok=False)
    monkeypatch.setattr(
        wake_process_mod.ctypes, "windll", _FakeWindll(fake), raising=False
    )
    assert wake_process_mod._windows_pid_alive(123) is False
    assert fake.closed_handles == [77]


def test_windows_pid_alive_active_process(monkeypatch: pytest.MonkeyPatch) -> None:
    fake = _FakeKernel32(exit_code=wake_process_mod._STILL_ACTIVE)
    monkeypatch.setattr(
        wake_process_mod.ctypes, "windll", _FakeWindll(fake), raising=False
    )
    assert wake_process_mod._windows_pid_alive(123) is True
    assert fake.closed_handles == [77]


def test_windows_pid_alive_exited_process(monkeypatch: pytest.MonkeyPatch) -> None:
    fake = _FakeKernel32(exit_code=0)
    monkeypatch.setattr(
        wake_process_mod.ctypes, "windll", _FakeWindll(fake), raising=False
    )
    assert wake_process_mod._windows_pid_alive(123) is False


def test_windows_pid_alive_open_process_raises(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    fake = _FakeKernel32(raise_on="OpenProcess")
    monkeypatch.setattr(
        wake_process_mod.ctypes, "windll", _FakeWindll(fake), raising=False
    )
    assert wake_process_mod._windows_pid_alive(123) is False


def test_windows_pid_alive_get_exit_code_raises(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    fake = _FakeKernel32(raise_on="GetExitCodeProcess")
    monkeypatch.setattr(
        wake_process_mod.ctypes, "windll", _FakeWindll(fake), raising=False
    )
    assert wake_process_mod._windows_pid_alive(123) is False
    # CloseHandle still runs via the finally block even after the raise.
    assert fake.closed_handles == [77]


def test_pid_alive_dispatches_to_windows_probe(
    wake_on: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    del wake_on
    monkeypatch.setattr(wake_process_mod.os, "name", "nt")
    monkeypatch.setattr(wake_process_mod, "_windows_pid_alive", lambda _pid: True)
    assert wake_mod._pid_alive(123) is True


def test_clear_pid_oserror(wake_on: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    class BoomPath:
        def unlink(self, *, missing_ok: bool = False) -> None:
            raise OSError("busy")

    monkeypatch.setattr(wake_mod, "wake_pid_path", lambda: BoomPath())
    wake_mod._clear_pid()


def test_kill_pid_dead_and_oserror(
    wake_on: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(wake_process_mod, "_pid_alive", lambda _pid: False)
    wake_mod._kill_pid(999999)

    monkeypatch.setattr(wake_process_mod, "_pid_alive", lambda _pid: True)
    monkeypatch.setattr(wake_process_mod, "_windows_pid_looks_owned", lambda _pid: True)

    if wake_process_mod.os.name == "nt":
        calls: list[list[str]] = []

        def fake_run(cmd: list[str], **_kwargs: object) -> object:
            calls.append(list(cmd))

            class Result:
                returncode = 0
                stdout = ""
                stderr = ""

            return Result()

        monkeypatch.setattr(wake_process_mod.subprocess, "run", fake_run)
        monkeypatch.setattr(
            wake_process_mod,
            "_pinned_taskkill",
            lambda: r"C:\Windows\System32\taskkill.exe",
        )
        wake_mod._kill_pid(4242, token="owned")
        assert calls and calls[0][0].lower().endswith("taskkill.exe")
    else:

        def boom(_pid: int, _sig: int) -> None:
            raise OSError("denied")

        monkeypatch.setattr(wake_process_mod.os, "kill", boom)
        wake_mod._kill_pid(4242, token="owned")


def test_followup_prompt_without_goal(wake_on: Path) -> None:
    msg = wake_mod._followup_prompt()
    assert "[GOAL]" in msg
    assert "FOLLOW-UP REQUIRED" in msg
    assert "goal-auditor" in msg


def test_tick_when_wake_disabled(
    wake_on: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("CURSOR_GOAL_WAKE", "0")
    assert wake_mod.tick() == 0


def test_run_loop_when_disabled(wake_on: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("CURSOR_GOAL_WAKE", "0")
    assert wake_mod.run_loop() == 0


def test_run_loop_keyboard_interrupt(
    wake_on: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    assert run_cli("manage", "create", "kb interrupt")[0] == 0

    def boom(_seconds: float) -> None:
        raise KeyboardInterrupt

    monkeypatch.setattr(wake_mod.time, "sleep", boom)
    assert wake_mod.run_loop(interval=5) == 0


def test_wake_loop_cli_errors(wake_on: Path) -> None:
    code, _out, err = run_cli("wake", "loop", "extra")
    assert code == 1
    assert "unexpected" in err.lower()
    code, _out, err = run_cli("wake", "arm", "--interval")
    assert code == 1
    assert "requires" in err.lower()


def test_disarm_unlink_oserror(wake_on: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    wake_mod.arm(interval=5)

    class BoomJson:
        def is_file(self) -> bool:
            return True

        def unlink(self, *, missing_ok: bool = False) -> None:
            raise OSError("locked")

    monkeypatch.setattr(wake_mod, "wake_json_path", lambda: BoomJson())
    monkeypatch.setattr(wake_mod, "wake_pid_path", lambda: BoomJson())
    assert wake_mod.disarm(kill_loop=False) is True


def test_cmd_wake_oserror(wake_on: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    def boom(**_kwargs: object) -> dict:
        raise OSError("disk full")

    monkeypatch.setattr(wake_mod, "arm", boom)
    code, _out, err = run_cli("wake", "arm")
    assert code == 1
    assert "Error" in err


def test_manage_arm_wake_oserror(
    wake_on: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    import cursor_goal.manage as manage_mod

    def boom() -> dict:
        raise OSError("cannot arm")

    monkeypatch.setattr(manage_mod, "wake_arm", boom)
    code, out, err = run_cli("manage", "create", "arm fail unique", "--force")
    assert code == 1
    assert "Wake armed" not in out
    assert "GOAL_WAKE_REQUIRED" not in out
    assert "paused" in err.lower() or "arm failed" in err.lower()
    data = json.loads((wake_on / "goal.json").read_text(encoding="utf-8"))
    assert data["status"] == "paused"


def test_disarm_kills_foreign_pid(
    wake_on: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    wake_mod.arm(interval=5)
    wake_mod._write_pid_record(999001, "test-token-999001")
    killed: list[int] = []

    def fake_kill(pid: int, *, token: str | None = None) -> None:
        del token
        killed.append(pid)

    monkeypatch.setattr(wake_mod, "_kill_pid", fake_kill)
    assert wake_mod.disarm(kill_loop=True) is True
    assert killed == [999001]


def test_run_loop_emits_while_pursuing(
    wake_on: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    assert run_cli("manage", "create", "emit wake")[0] == 0
    ticks = {"n": 0}

    def fake_sleep(_seconds: float) -> None:
        ticks["n"] += 1
        if ticks["n"] >= 2:
            wake_mod.disarm(kill_loop=False)

    monkeypatch.setattr(wake_mod.time, "sleep", fake_sleep)
    out = io.StringIO()
    with redirect_stdout(out):
        assert wake_mod.run_loop(interval=5) == 0
    assert "AGENT_GOAL_WAKE" in out.getvalue()


def test_wake_disarm_when_armed_prints(wake_on: Path) -> None:
    wake_mod.arm(interval=5)
    code, out, _err = run_cli("wake", "disarm")
    assert code == 0
    assert "disarmed" in out.lower()


def test_run_loop_empty_arm(wake_on: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(wake_mod, "arm", lambda **_k: {})
    assert wake_mod.run_loop(interval=5) == 0


def test_wake_loop_missing_interval_value(wake_on: Path) -> None:
    code, _out, err = run_cli("wake", "loop", "--interval")
    assert code == 1
    assert "requires" in err.lower()


def test_wake_arm_includes_token(wake_on: Path) -> None:
    config = wake_mod.arm(interval=5)
    assert "token" in config and len(config["token"]) >= 8
    data = json.loads((wake_on / "wake.json").read_text(encoding="utf-8"))
    assert data["token"] == config["token"]


def test_clear_pid_skips_other_owner(wake_on: Path) -> None:
    wake_mod._write_pid_record(111, "token-a")
    wake_mod._clear_pid(only_if_pid=222, only_if_token="token-a")
    assert wake_process_mod._read_pid() == 111
    wake_mod._clear_pid(only_if_pid=111, only_if_token="token-a")
    assert wake_process_mod._read_pid() is None


def test_wake_tick_increments_wake_ticks(wake_on: Path) -> None:
    assert run_cli("manage", "create", "tick budget", "--budget", "3")[0] == 0
    assert run_cli("wake", "tick")[0] == 0
    from tests.conftest import load_goal_json

    data = load_goal_json(wake_on)
    assert data["wake_ticks"] == 1
    assert data["status"] == "pursuing"


def test_wake_ticks_hit_budget(wake_on: Path) -> None:
    assert (
        run_cli(
            "manage",
            "create",
            "wake budget",
            "--budget",
            "5",
            "--wake-budget",
            "1",
        )[0]
        == 0
    )
    code, out, _err = run_cli("wake", "tick")
    assert code == 0
    assert "BUDGET" in out
    assert "Do not spawn auditor" in out
    from tests.conftest import load_goal_json

    data = load_goal_json(wake_on)
    assert data["status"] == "budget-limited"
    assert data["wake_budget"] == 1
    assert not (wake_on / "wake.json").is_file()


def test_wake_ticks_do_not_consume_turn_budget(wake_on: Path) -> None:
    assert (
        run_cli(
            "manage",
            "create",
            "independent budgets",
            "--budget",
            "2",
            "--wake-budget",
            "10",
        )[0]
        == 0
    )
    assert run_cli("wake", "tick")[0] == 0
    # Expire wake→wake coalesce so a second tick can increment wake_ticks.
    config = json.loads((wake_on / "wake.json").read_text(encoding="utf-8"))
    config.pop("last_nudge_at", None)
    config.pop("last_nudge_source", None)
    (wake_on / "wake.json").write_text(json.dumps(config), encoding="utf-8")
    assert run_cli("wake", "tick")[0] == 0
    from tests.conftest import load_goal_json

    data = load_goal_json(wake_on)
    assert data["wake_ticks"] == 2
    assert data["turns_used"] == 0
    assert data["status"] == "pursuing"
    assert data["wake_budget"] == 10
    assert data["turn_budget"] == 2
    assert data["schema_version"] == 1


def test_force_create_disarms_prior_wake(
    wake_on: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    assert run_cli("manage", "create", "first")[0] == 0
    wake_mod._write_pid_record(999002, "test-token-999002")
    killed: list[int] = []

    def fake_kill(pid: int, *, token: str | None = None) -> None:
        del token
        killed.append(pid)

    monkeypatch.setattr(wake_mod, "_kill_pid", fake_kill)
    assert run_cli("manage", "create", "second", "--force")[0] == 0
    assert 999002 in killed


def test_windows_kill_refuses_unowned(
    wake_on: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    del wake_on
    # Avoid data_dir()/Path.resolve() after os.name monkeypatch: on macOS,
    # pathlib may call a flavour realpath that is aliased to abspath and
    # rejects strict= (TypeError). Ownership refusal does not need pid files.
    monkeypatch.setattr(wake_process_mod, "_read_pid_record", lambda: None)
    monkeypatch.setattr(wake_process_mod, "_pid_alive", lambda _pid: True)
    monkeypatch.setattr(wake_process_mod.os, "name", "nt")
    monkeypatch.setattr(
        wake_process_mod, "_windows_pid_looks_owned", lambda _pid: False
    )
    calls: list[object] = []
    monkeypatch.setattr(
        wake_process_mod.subprocess,
        "run",
        lambda *_a, **_k: calls.append(1),
    )
    wake_mod._kill_pid(4242, token="owned")
    assert calls == []


def test_kill_pid_refuses_missing_token(
    wake_on: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    del wake_on
    monkeypatch.setattr(wake_process_mod, "_pid_alive", lambda _pid: True)
    calls: list[object] = []
    monkeypatch.setattr(
        wake_process_mod.subprocess,
        "run",
        lambda *_a, **_k: calls.append(1),
    )
    wake_mod._kill_pid(4242, token=None)
    wake_mod._kill_pid(4242, token="")
    assert calls == []


def test_kill_pid_tokenless_never_kills(
    wake_on: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Clean break: missing token never kills even if cmdline looks owned."""
    del wake_on
    monkeypatch.setattr(wake_process_mod, "_pid_alive", lambda _pid: True)
    monkeypatch.setattr(wake_process_mod, "_pid_looks_owned", lambda _pid: True)
    monkeypatch.setattr(wake_process_mod, "_read_pid_record", lambda: None)
    monkeypatch.setattr(wake_process_mod.os, "name", "nt")
    monkeypatch.setattr(wake_process_mod, "_windows_pid_looks_owned", lambda _pid: True)
    calls: list[object] = []
    monkeypatch.setattr(
        wake_process_mod.subprocess,
        "run",
        lambda *_a, **_k: calls.append(1),
    )
    wake_mod._kill_pid(4242, token="")
    assert calls == []


def test_kill_existing_loop_clears_tokenless_without_kill(
    wake_on: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    (wake_on / "wake.pid").write_text("424242\n", encoding="utf-8")
    killed: list[int] = []

    def fake(pid: int, *, token: str | None = None) -> None:
        del token
        killed.append(pid)

    monkeypatch.setattr(wake_mod, "_kill_pid", fake)
    monkeypatch.setattr(wake_process_mod, "_pid_alive", lambda _pid: False)
    wake_mod._kill_existing_loop()
    assert killed == []
    assert wake_process_mod._read_pid() is None
    assert wake_process_mod.read_orphan_wake() is None


def test_kill_existing_loop_marks_orphan_when_tokenless_alive(
    wake_on: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    (wake_on / "wake.pid").write_text("424242\n", encoding="utf-8")
    killed: list[int] = []

    def fake(pid: int, *, token: str | None = None) -> None:
        del token
        killed.append(pid)

    monkeypatch.setattr(wake_mod, "_kill_pid", fake)
    monkeypatch.setattr(wake_process_mod, "_pid_alive", lambda _pid: True)
    wake_mod._kill_existing_loop()
    assert killed == []
    assert wake_process_mod._read_pid() is None
    orphan = wake_process_mod.read_orphan_wake()
    assert orphan is not None
    assert orphan.get("pid") == 424242


def test_kill_existing_loop_never_kills_tokenless_even_if_owned(
    wake_on: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    (wake_on / "wake.pid").write_text("424242\n", encoding="utf-8")
    killed: list[int] = []

    def fake(pid: int, *, token: str | None = None) -> None:
        del token
        killed.append(pid)

    monkeypatch.setattr(wake_mod, "_kill_pid", fake)
    monkeypatch.setattr(wake_process_mod, "_pid_alive", lambda _pid: True)
    monkeypatch.setattr(wake_process_mod, "_pid_looks_owned", lambda _pid: True)
    wake_mod._kill_existing_loop()
    assert killed == []
    assert wake_process_mod._read_pid() is None
    assert wake_process_mod.read_orphan_wake() is not None


def test_touch_last_emit_oserror_is_swallowed(
    wake_on: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    wake_mod.arm(interval=5)

    def boom(_config: dict[str, object]) -> None:
        raise OSError("disk full")

    monkeypatch.setattr(wake_mod, "_write_wake_config", boom)
    # Must not raise: emit_wake_line()'s nudge stamp is best-effort.
    wake_mod.emit_wake_line("hello")


def test_touch_last_emit_missing_config_is_noop(wake_on: Path) -> None:
    del wake_on
    # No wake.json present: _touch_last_emit() returns early without writing.
    wake_mod._touch_last_emit()


def test_emit_budget_limited_tick_missing_state(
    wake_on: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    del wake_on
    disarmed: list[bool] = []
    monkeypatch.setattr(wake_mod, "disarm", lambda **kwargs: disarmed.append(True))
    result = wake_mod.WakeTickResult(status="budget_limited", state=None)
    assert wake_mod._emit_budget_limited_tick(result) == 1
    assert disarmed == []


def test_emit_budget_limited_loop_step_missing_state(
    wake_on: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    del wake_on
    disarmed: list[bool] = []
    monkeypatch.setattr(wake_mod, "disarm", lambda **kwargs: disarmed.append(True))
    result = wake_mod.WakeTickResult(status="budget_limited", state=None)
    assert wake_mod._emit_budget_limited_loop_step(result) == 1
    assert disarmed == []
    assert "budget_limited result missing state" in capsys.readouterr().err


def test_wake_loop_tick_outcome_config_cleared(
    wake_on: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    del wake_on
    monkeypatch.setattr(wake_mod, "_read_wake_config", lambda: None)
    assert wake_mod._wake_loop_tick_outcome("tok") == 0


def test_wake_loop_tick_outcome_token_mismatch(
    wake_on: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    del wake_on
    monkeypatch.setattr(wake_mod, "_read_wake_config", lambda: {"token": "different"})
    assert wake_mod._wake_loop_tick_outcome("original") == 0


def test_run_loop_acl_reharden_oserror_is_swallowed(
    wake_on: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    assert run_cli("manage", "create", "acl reharden fail")[0] == 0
    monkeypatch.setattr(wake_mod.time, "sleep", lambda _s: None)

    def boom(*_a: object, **_k: object) -> None:
        raise OSError("acl failed")

    monkeypatch.setattr(wake_mod, "harden_windows_acl", boom)
    monkeypatch.setattr(
        wake_mod,
        "_record_wake_tick",
        lambda: wake_mod.WakeTickResult(status="inactive"),
    )
    # Must not raise: run_loop re-hardens ACLs best-effort at startup.
    assert wake_mod.run_loop(interval=5) == 0


def test_kill_existing_loop_defense_in_depth_tokenless_dead_pid(
    wake_on: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Belt-and-suspenders: _read_pid_record never returns an empty token in
    practice (it clears those itself), but _kill_existing_loop still guards
    against it. A dead pid must clear quietly without marking orphan."""
    del wake_on
    marked: list[int] = []
    monkeypatch.setattr(
        wake_mod, "mark_orphan_wake", lambda pid, _reason: marked.append(pid)
    )
    monkeypatch.setattr(
        wake_mod,
        "_read_pid_record",
        lambda: {"pid": 424242, "token": "", "started_at": ""},
    )
    monkeypatch.setattr(wake_process_mod, "_pid_alive", lambda _pid: False)
    cleared: list[int] = []
    monkeypatch.setattr(
        wake_mod, "_clear_pid", lambda **kwargs: cleared.append(kwargs["only_if_pid"])
    )
    wake_mod._kill_existing_loop()
    assert marked == []
    assert cleared == [424242]


def test_windows_ownership_rejects_bare_wake_marker(
    wake_on: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    del wake_on

    class Result:
        returncode = 0
        stdout = "C:\\tools\\other-wake-daemon.exe --mode wake"
        stderr = ""

    monkeypatch.setattr(
        wake_process_mod.subprocess,
        "run",
        lambda *_a, **_k: Result(),
    )
    _pin_windows_bins(monkeypatch)
    assert wake_process_mod._windows_pid_looks_owned(12345) is False

    class Owned:
        returncode = 0
        stdout = "py -3 -u C:\\Users\\x\\.cursor\\skills\\goal\\scripts\\run_goal.py wake loop"
        stderr = ""

    monkeypatch.setattr(
        wake_process_mod.subprocess,
        "run",
        lambda *_a, **_k: Owned(),
    )
    assert wake_process_mod._windows_pid_looks_owned(12345) is True

    class OwnedPackage:
        returncode = 0
        stdout = "python -m cursor_goal wake loop"
        stderr = ""

    monkeypatch.setattr(
        wake_process_mod.subprocess,
        "run",
        lambda *_a, **_k: OwnedPackage(),
    )
    assert wake_process_mod._windows_pid_looks_owned(12345) is True

    class OwnedHyphen:
        returncode = 0
        stdout = "C:\\tools\\cursor-goal\\wake_loop.cmd"
        stderr = ""

    monkeypatch.setattr(
        wake_process_mod.subprocess,
        "run",
        lambda *_a, **_k: OwnedHyphen(),
    )
    assert wake_process_mod._windows_pid_looks_owned(99) is True


def test_atomic_write_cleans_tmp_on_replace_fail(
    wake_on: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    path = wake_on / "atomic.txt"
    tmp_holder: dict[str, Path] = {}

    real_with_name = Path.with_name

    def tracking_with_name(self: Path, name: str) -> Path:
        result = real_with_name(self, name)
        if name.endswith(".tmp"):
            tmp_holder["tmp"] = result
        return result

    monkeypatch.setattr(Path, "with_name", tracking_with_name)

    def boom_replace(self: Path, _target: Path) -> None:
        raise OSError("replace failed")

    monkeypatch.setattr(Path, "replace", boom_replace)
    with pytest.raises(OSError):
        wake_mod._atomic_write_text(path, "hello")
    assert "tmp" in tmp_holder
    assert not tmp_holder["tmp"].exists()


def test_read_pid_record_variants(
    wake_on: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    (wake_on / "wake.pid").write_text("", encoding="utf-8")
    assert wake_mod._read_pid_record() is None
    # Plain-int is cleared (no longer accepted); dead pid → no orphan.
    monkeypatch.setattr(wake_process_mod, "_pid_alive", lambda _pid: False)
    (wake_on / "wake.pid").write_text("42\n", encoding="utf-8")
    assert wake_mod._read_pid_record() is None
    assert not (wake_on / "wake.pid").is_file()
    (wake_on / "wake.pid").write_text('{"pid": "x"}\n', encoding="utf-8")
    assert wake_mod._read_pid_record() is None
    (wake_on / "wake.pid").write_text("[1]\n", encoding="utf-8")
    assert wake_mod._read_pid_record() is None


def test_read_pid_record_empty_token_marks_orphan(
    wake_on: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(wake_process_mod, "_pid_alive", lambda _pid: True)
    (wake_on / "wake.pid").write_text(
        '{"pid": 999001, "token": "", "started_at": ""}\n',
        encoding="utf-8",
    )
    assert wake_mod._read_pid_record() is None
    assert not (wake_on / "wake.pid").is_file()
    orphan = wake_process_mod.read_orphan_wake()
    assert orphan is not None
    assert orphan.get("pid") == 999001


def test_read_pid_record_plain_int_alive_marks_orphan(
    wake_on: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(wake_process_mod, "_pid_alive", lambda _pid: True)
    (wake_on / "wake.pid").write_text("777001\n", encoding="utf-8")
    assert wake_mod._read_pid_record() is None
    orphan = wake_process_mod.read_orphan_wake()
    assert orphan is not None
    assert orphan.get("pid") == 777001


def test_read_pid_record_empty_token_dead_pid(
    wake_on: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(wake_process_mod, "_pid_alive", lambda _pid: False)
    (wake_on / "wake.pid").write_text(
        '{"pid": 999002, "token": ""}\n',
        encoding="utf-8",
    )
    assert wake_mod._read_pid_record() is None
    assert not (wake_on / "wake.pid").is_file()
    assert wake_process_mod.read_orphan_wake() is None


def test_read_pid_record_empty_token_unlink_oserror(
    wake_on: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(wake_process_mod, "_pid_alive", lambda _pid: False)
    pid_path = wake_on / "wake.pid"
    pid_path.write_text('{"pid": 999003, "token": ""}\n', encoding="utf-8")
    real_unlink = Path.unlink

    def selective_unlink(self: Path, missing_ok: bool = False) -> None:
        if self.name == "wake.pid":
            raise OSError("busy")
        return real_unlink(self, missing_ok=missing_ok)

    monkeypatch.setattr(Path, "unlink", selective_unlink)
    assert wake_mod._read_pid_record() is None


def test_write_pid_record_requires_token(wake_on: Path) -> None:
    del wake_on
    with pytest.raises(ValueError, match="token"):
        wake_mod._write_pid_record(1, "")


def test_kill_existing_loop_defense_empty_token(
    wake_on: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    del wake_on
    monkeypatch.setattr(
        wake_mod,
        "_read_pid_record",
        lambda: {"pid": 424243, "token": "", "started_at": ""},
    )
    monkeypatch.setattr(wake_mod, "_pid_alive", lambda _pid: True)
    cleared: list[bool] = []
    monkeypatch.setattr(wake_mod, "_clear_pid", lambda **_kwargs: cleared.append(True))
    orphaned: list[int] = []

    def mark(pid: int, reason: str) -> None:
        del reason
        orphaned.append(pid)

    monkeypatch.setattr(wake_mod, "mark_orphan_wake", mark)
    killed: list[int] = []
    monkeypatch.setattr(
        wake_mod, "_kill_pid", lambda pid, *, token=None: killed.append(pid)
    )
    wake_mod._kill_existing_loop()
    assert killed == []
    assert cleared
    assert orphaned == [424243]


def test_cmdline_looks_owned_helpers() -> None:
    assert (
        wake_process_mod._cmdline_looks_owned("python -m cursor_goal wake loop") is True
    )
    assert wake_process_mod._cmdline_looks_owned("run_goal.py wake loop") is True
    assert wake_process_mod._cmdline_looks_owned("C:\\x\\wake_loop.cmd") is True
    assert wake_process_mod._cmdline_looks_owned("other-wake-daemon") is False
    assert wake_process_mod._cmdline_looks_owned("") is False
    # Reject bare repo/package paths (PID-reuse false positives).
    assert (
        wake_process_mod._cmdline_looks_owned(
            r"pytest C:\Users\x\Documents\Git\cursor-goal\tests\test_wake.py"
        )
        is False
    )
    assert (
        wake_process_mod._cmdline_looks_owned(
            r"Code.exe --folder-uri file:///C:/Users/x/cursor-goal"
        )
        is False
    )
    assert (
        wake_process_mod._cmdline_looks_owned("python run_goal.py manage status")
        is False
    )
    assert (
        wake_process_mod._cmdline_looks_owned("python -m cursor_goal manage status")
        is False
    )


def test_clear_pid_token_mismatch(wake_on: Path) -> None:
    wake_mod._write_pid_record(55, "tok-a")
    wake_mod._clear_pid(only_if_pid=55, only_if_token="tok-b")
    assert wake_process_mod._read_pid() == 55


def test_windows_ownership_probe(
    wake_on: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    class Res:
        stdout = "python run_goal.py wake loop"
        stderr = ""
        returncode = 0

    monkeypatch.setattr(wake_process_mod.subprocess, "run", lambda *_a, **_k: Res())
    _pin_windows_bins(monkeypatch)
    assert wake_process_mod._windows_pid_looks_owned(123) is True

    class Empty:
        stdout = ""
        stderr = ""
        returncode = 1

    monkeypatch.setattr(wake_process_mod.subprocess, "run", lambda *_a, **_k: Empty())
    assert wake_process_mod._windows_pid_looks_owned(123) is False

    def boom(*_a: object, **_k: object) -> None:
        raise OSError("no ps")

    monkeypatch.setattr(wake_process_mod.subprocess, "run", boom)
    assert wake_process_mod._windows_pid_looks_owned(123) is False


def test_kill_pid_token_guards(wake_on: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(wake_process_mod, "_pid_alive", lambda _p: True)
    wake_mod._write_pid_record(777, "right")
    wake_mod._kill_pid(777, token="wrong")
    wake_mod._write_pid_record(888, "same")
    wake_mod._kill_pid(777, token="same")
    # Matching token+pid falls through to platform kill paths.
    wake_mod._write_pid_record(777, "same")
    monkeypatch.setattr(wake_process_mod, "_windows_pid_looks_owned", lambda _p: True)
    monkeypatch.setattr(wake_process_mod.subprocess, "run", lambda *_a, **_k: None)
    monkeypatch.setattr(wake_process_mod.os, "kill", lambda *_a, **_k: None)
    _pin_windows_bins(monkeypatch)
    wake_mod._kill_pid(777, token="same")


def test_unix_pid_owned_none_subprocess(
    wake_on: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    del wake_on

    class NoProc:
        def __init__(self, *_args: object, **_kwargs: object) -> None:
            pass

        def is_file(self) -> bool:
            return False

    monkeypatch.setattr(wake_process_mod, "Path", NoProc)
    monkeypatch.setattr(wake_process_mod.subprocess, "run", lambda *_a, **_k: None)
    assert wake_process_mod._unix_pid_looks_owned(42) is False


def test_windows_pid_owned_none_subprocess(
    wake_on: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    del wake_on
    monkeypatch.setattr(wake_process_mod.subprocess, "run", lambda *_a, **_k: None)
    _pin_windows_bins(monkeypatch)
    assert wake_process_mod._windows_pid_looks_owned(42) is False


def test_record_wake_tick_inactive(wake_on: Path) -> None:
    assert run_cli("manage", "create", "inactive tick")[0] == 0
    assert run_cli("manage", "done", "--force")[0] == 0
    result = wake_mod._record_wake_tick()
    assert result.status == "inactive"
    assert result.state is not None
    assert result.state.active is False or result.state.status != "pursuing"


def test_owning_loop_pid_if_alive_no_record(wake_on: Path) -> None:
    del wake_on
    assert wake_mod._owning_loop_pid_if_alive() is None


def test_owning_loop_pid_if_alive_dead_pid(
    wake_on: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    wake_mod._write_pid_record(999321, "tok")
    monkeypatch.setattr(wake_mod, "_pid_alive", lambda _pid: False)
    assert wake_mod._owning_loop_pid_if_alive() is None


def test_owning_loop_pid_if_alive_unowned_pid(
    wake_on: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    wake_mod._write_pid_record(999321, "tok")
    monkeypatch.setattr(wake_mod, "_pid_alive", lambda _pid: True)
    monkeypatch.setattr(wake_mod, "_pid_looks_owned", lambda _pid: False)
    assert wake_mod._owning_loop_pid_if_alive() is None


def test_owning_loop_pid_if_alive_verified(
    wake_on: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    wake_mod._write_pid_record(999321, "tok")
    monkeypatch.setattr(wake_mod, "_pid_alive", lambda _pid: True)
    monkeypatch.setattr(wake_mod, "_pid_looks_owned", lambda _pid: True)
    assert wake_mod._owning_loop_pid_if_alive() == 999321


def test_wake_tick_skips_charge_when_owning_loop_alive(
    wake_on: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A live, verified-owned loop already ticks itself; a concurrent manual
    tick must not double-charge wake_ticks."""
    assert run_cli("manage", "create", "owned-loop tick")[0] == 0
    wake_mod.arm(interval=5)
    other_pid = os.getpid() + 1
    monkeypatch.setattr(wake_mod, "_owning_loop_pid_if_alive", lambda: other_pid)
    charged: list[bool] = []
    monkeypatch.setattr(
        wake_mod,
        "_record_wake_tick",
        lambda: charged.append(True) or wake_mod.WakeTickResult(status="ok"),
    )
    out = io.StringIO()
    with redirect_stdout(out):
        assert wake_mod.tick() == 0
    assert charged == []
    assert "AGENT_GOAL_WAKE" not in out.getvalue()


def test_record_wake_tick_no_goal_at_all(wake_on: Path) -> None:
    """mutate_goal() returns None (not a raise) when goal.json is entirely absent."""
    del wake_on
    result = wake_mod._record_wake_tick()
    assert result.status == "inactive"
    assert result.state is None


def test_kill_existing_loop(wake_on: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    wake_mod._write_pid_record(999003, "t")
    killed: list[int] = []

    def fake(pid: int, *, token: str | None = None) -> None:
        del token
        killed.append(pid)

    monkeypatch.setattr(wake_mod, "_kill_pid", fake)
    wake_mod._kill_existing_loop()
    assert killed == [999003]
    assert wake_process_mod._read_pid() is None

    wake_mod._write_pid_record(wake_mod.os.getpid(), "self")
    wake_mod._kill_existing_loop()  # skips self


def test_record_wake_tick_errors(
    wake_on: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    def boom(_m: object) -> None:
        raise ValueError("inactive")

    monkeypatch.setattr(wake_mod, "mutate_goal", boom)
    monkeypatch.setattr(wake_mod, "snapshot_goal", lambda: None)
    result = wake_mod._record_wake_tick()
    assert result.status == "inactive"
    assert result.state is None

    def boom_os(_m: object) -> None:
        raise OSError("disk")

    monkeypatch.setattr(wake_mod, "mutate_goal", boom_os)
    result = wake_mod._record_wake_tick()
    assert result.status == "persist_failed"
    assert result.state is None


def test_wake_tick_persist_failure_refuses_emit(
    wake_on: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    assert run_cli("manage", "create", "persist fail tick")[0] == 0
    wake_mod.arm(interval=5)
    monkeypatch.setattr(
        wake_mod,
        "_record_wake_tick",
        lambda: wake_mod.WakeTickResult(status="persist_failed"),
    )
    out = io.StringIO()
    with redirect_stdout(out):
        assert wake_mod.tick() == 1
    assert "AGENT_GOAL_WAKE" not in out.getvalue()


def test_wake_tick_unarmed_pursuing_refuses_emit(wake_on: Path) -> None:
    assert run_cli("manage", "create", "unarmed tick")[0] == 0
    wake_mod.disarm(kill_loop=True)
    out = io.StringIO()
    with redirect_stdout(out):
        assert wake_mod.tick() == 1
    assert "AGENT_GOAL_WAKE" not in out.getvalue()


def test_continuation_readiness_pid_unverified(
    wake_on: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    assert run_cli("manage", "create", "unowned pid")[0] == 0
    wake_mod.arm(interval=5)
    wake_mod._write_pid_record(999777, "tok")
    monkeypatch.setattr(wake_mod, "_pid_alive", lambda _pid: True)
    monkeypatch.setattr(wake_mod, "_pid_looks_owned", lambda _pid: False)
    ready = wake_mod.continuation_readiness(goal_pursuing=True)
    assert ready["continuation_ready"] is False
    assert ready["reason"] == "pid_unverified"


def test_run_loop_persist_failed_exits(
    wake_on: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    assert run_cli("manage", "create", "loop persist fail")[0] == 0
    monkeypatch.setattr(wake_mod.time, "sleep", lambda _s: None)
    monkeypatch.setattr(
        wake_mod,
        "_record_wake_tick",
        lambda: wake_mod.WakeTickResult(status="persist_failed"),
    )
    out = io.StringIO()
    with redirect_stdout(out):
        assert wake_mod.run_loop(interval=5) == 1
    assert "AGENT_GOAL_WAKE" not in out.getvalue()


def test_run_loop_inactive_after_tick(
    wake_on: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    assert run_cli("manage", "create", "loop inactive")[0] == 0
    monkeypatch.setattr(wake_mod.time, "sleep", lambda _s: None)
    monkeypatch.setattr(
        wake_mod,
        "_record_wake_tick",
        lambda: wake_mod.WakeTickResult(status="inactive"),
    )
    assert wake_mod.run_loop(interval=5) == 0


def test_tick_inactive_status_disarms(
    wake_on: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    assert run_cli("manage", "create", "tick inactive")[0] == 0
    wake_mod.arm(interval=5)
    monkeypatch.setattr(
        wake_mod,
        "_record_wake_tick",
        lambda: wake_mod.WakeTickResult(status="inactive"),
    )
    assert wake_mod.tick() == 0


def test_interruptible_sleep_token_mismatch(
    wake_on: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    wake_mod.arm(interval=5)
    cfg = wake_mod._read_wake_config()
    assert cfg is not None
    monkeypatch.setattr(wake_mod.time, "sleep", lambda _s: None)
    # Change token in file
    cfg["token"] = "other"
    wake_mod._write_wake_config(cfg)
    assert wake_mod._interruptible_sleep(5.0, "original") is False


def test_run_loop_budget_via_wake_ticks(
    wake_on: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    assert (
        run_cli(
            "manage",
            "create",
            "loop budget",
            "--budget",
            "5",
            "--wake-budget",
            "1",
        )[0]
        == 0
    )
    monkeypatch.setattr(wake_mod.time, "sleep", lambda _s: None)
    out = io.StringIO()
    with redirect_stdout(out):
        assert wake_mod.run_loop(interval=5) == 0
    assert "BUDGET" in out.getvalue()


def test_run_loop_token_replaced(
    wake_on: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    assert run_cli("manage", "create", "token race")[0] == 0
    n = {"i": 0}

    def sleep(_s: float) -> None:
        n["i"] += 1
        if n["i"] == 1:
            # Re-arm replaces token while loop runs
            wake_mod.arm(interval=5)

    monkeypatch.setattr(wake_mod.time, "sleep", sleep)
    assert wake_mod.run_loop(interval=5) == 0


def test_taskkill_oserror(wake_on: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    del wake_on
    # See test_windows_kill_refuses_unowned: skip pid-path resolve under os.name=nt.
    monkeypatch.setattr(wake_process_mod, "_read_pid_record", lambda: None)
    monkeypatch.setattr(wake_process_mod, "_pid_alive", lambda _p: True)
    monkeypatch.setattr(wake_process_mod.os, "name", "nt")
    monkeypatch.setattr(wake_process_mod, "_windows_pid_looks_owned", lambda _p: True)
    monkeypatch.setattr(
        wake_process_mod,
        "_pinned_taskkill",
        lambda: r"C:\Windows\System32\taskkill.exe",
    )

    def boom(*_a: object, **_k: object) -> None:
        raise OSError("taskkill gone")

    monkeypatch.setattr(wake_process_mod.subprocess, "run", boom)
    wake_mod._kill_pid(4242, token="owned")


def test_atomic_write_unlink_oserror(
    wake_on: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    path = wake_on / "atomic2.txt"

    def boom_replace(self: Path, _target: Path) -> None:
        raise OSError("replace failed")

    monkeypatch.setattr(Path, "replace", boom_replace)

    real_unlink = Path.unlink

    def boom_unlink(self: Path, *, missing_ok: bool = False) -> None:
        if str(self).endswith(".tmp"):
            raise OSError("busy tmp")
        return real_unlink(self, missing_ok=missing_ok)

    monkeypatch.setattr(Path, "unlink", boom_unlink)
    with pytest.raises(OSError):
        wake_mod._atomic_write_text(path, "x")


def test_unix_kill_refuses_unowned(
    wake_on: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    del wake_on
    monkeypatch.setattr(wake_process_mod, "_read_pid_record", lambda: None)
    monkeypatch.setattr(wake_process_mod, "_pid_alive", lambda _pid: True)
    monkeypatch.setattr(wake_process_mod.os, "name", "posix")
    monkeypatch.setattr(wake_process_mod, "_unix_pid_looks_owned", lambda _pid: False)
    killed: list[int] = []

    def fake_kill(pid: int, _sig: int) -> None:
        killed.append(pid)

    monkeypatch.setattr(wake_process_mod.os, "kill", fake_kill)
    wake_mod._kill_pid(4242, token="owned")
    assert killed == []


def test_unix_ownership_via_ps(wake_on: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    del wake_on

    real_is_file = Path.is_file

    def fake_is_file(self: Path) -> bool:
        if str(self).endswith("/cmdline"):
            return False
        return real_is_file(self)

    monkeypatch.setattr(Path, "is_file", fake_is_file)

    class Owned:
        returncode = 0
        stdout = "python3 -u /home/x/.cursor/skills/cursor-goal/scripts/run_goal.py wake loop"
        stderr = ""

    monkeypatch.setattr(wake_process_mod.subprocess, "run", lambda *_a, **_k: Owned())
    assert wake_process_mod._unix_pid_looks_owned(88) is True

    class Bare:
        returncode = 0
        stdout = "/usr/bin/other-wake-daemon --mode wake"
        stderr = ""

    monkeypatch.setattr(wake_process_mod.subprocess, "run", lambda *_a, **_k: Bare())
    assert wake_process_mod._unix_pid_looks_owned(88) is False

    class Failed:
        returncode = 1
        stdout = ""
        stderr = "not found"

    monkeypatch.setattr(wake_process_mod.subprocess, "run", lambda *_a, **_k: Failed())
    assert wake_process_mod._unix_pid_looks_owned(88) is False


def test_wake_arm_refuses_insecure(
    wake_on: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(
        wake_mod,
        "refuse_if_data_dir_insecure",
        lambda: "[goal] Error: data directory is insecure",
    )
    with pytest.raises(OSError, match="insecure"):
        wake_mod.arm()
    code, _out, err = run_cli("wake", "loop")
    assert code == 1
    assert "insecure" in err


def test_wake_tick_refuses_insecure(
    wake_on: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    assert run_cli("manage", "create", "tick insecure")[0] == 0
    monkeypatch.setattr(
        wake_mod,
        "refuse_if_data_dir_insecure",
        lambda: "[goal] Error: data directory is insecure",
    )
    assert wake_mod.tick() == 1


def test_unix_ownership_via_proc_file(
    wake_on: Path, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    del wake_on
    cmdline = tmp_path / "cmdline"
    cmdline.write_bytes(b"python\x00-m\x00cursor_goal\x00wake\x00loop\x00")

    real_path = Path

    class ProcPath:
        def __init__(self, arg: object) -> None:
            self._arg = str(arg)

        def is_file(self) -> bool:
            return self._arg.endswith("/cmdline")

        def read_bytes(self) -> bytes:
            return cmdline.read_bytes()

    def path_factory(arg: object) -> object:
        text = str(arg)
        if text.startswith("/proc/") and text.endswith("/cmdline"):
            return ProcPath(text)
        return real_path(arg)

    monkeypatch.setattr(wake_process_mod, "Path", path_factory)
    assert wake_process_mod._unix_pid_looks_owned(42) is True

    bad = tmp_path / "badcmd"
    bad.write_bytes(b"other-wake-daemon\x00")

    class BadProc(ProcPath):
        def read_bytes(self) -> bytes:
            return bad.read_bytes()

    monkeypatch.setattr(
        wake_process_mod,
        "Path",
        lambda arg: BadProc(arg) if str(arg).endswith("/cmdline") else real_path(arg),
    )
    assert wake_process_mod._unix_pid_looks_owned(42) is False


def test_unix_ownership_proc_oserror(
    wake_on: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    del wake_on

    class BoomPath:
        def is_file(self) -> bool:
            return True

        def read_bytes(self) -> bytes:
            raise OSError("denied")

    monkeypatch.setattr(wake_process_mod, "Path", lambda _arg: BoomPath())
    assert wake_process_mod._unix_pid_looks_owned(7) is False


def test_wake_coalesce_does_not_skip_after_stop_nudge(wake_on: Path) -> None:
    """Stop stamps must not delay the race-immune wake path."""
    assert run_cli("manage", "create", "coalesce stop")[0] == 0
    assert (wake_on / "wake.json").is_file()
    # Clear wake-sourced stamp from any prior emit so only the stop stamp remains.
    config = json.loads((wake_on / "wake.json").read_text(encoding="utf-8"))
    config.pop("last_nudge_at", None)
    config.pop("last_nudge_source", None)
    (wake_on / "wake.json").write_text(json.dumps(config), encoding="utf-8")
    wake_mod.record_agent_nudge(source="stop")
    stamped = json.loads((wake_on / "wake.json").read_text(encoding="utf-8"))
    assert stamped.get("last_nudge_source") == "stop"
    code, out, _err = run_cli("wake", "tick")
    assert code == 0
    assert out.startswith("AGENT_GOAL_WAKE ")
    data = json.loads((wake_on / "goal.json").read_text(encoding="utf-8"))
    assert data["wake_ticks"] == 1


def test_wake_coalesce_skips_after_wake_nudge(wake_on: Path) -> None:
    assert run_cli("manage", "create", "coalesce wake")[0] == 0
    assert (wake_on / "wake.json").is_file()
    code, out, _err = run_cli("wake", "tick")
    assert code == 0
    assert out.startswith("AGENT_GOAL_WAKE ")
    data = json.loads((wake_on / "goal.json").read_text(encoding="utf-8"))
    assert data["wake_ticks"] == 1
    stamped = json.loads((wake_on / "wake.json").read_text(encoding="utf-8"))
    assert stamped.get("last_nudge_source") == "wake"
    code, out, _err = run_cli("wake", "tick")
    assert code == 0
    assert out == ""
    data = json.loads((wake_on / "goal.json").read_text(encoding="utf-8"))
    assert data["wake_ticks"] == 1


def test_wake_coalesce_missing_source_treated_as_wake(wake_on: Path) -> None:
    """Older wake.json without last_nudge_source still coalesces (wake back-compat)."""
    assert run_cli("manage", "create", "legacy source")[0] == 0
    wake_mod.arm(interval=30)
    config = json.loads((wake_on / "wake.json").read_text(encoding="utf-8"))
    config["last_nudge_at"] = wake_mod._now_iso()
    config.pop("last_nudge_source", None)
    (wake_on / "wake.json").write_text(json.dumps(config), encoding="utf-8")
    assert wake_mod._nudge_within_coalesce_window(config) is True


def test_refuse_if_wake_dead_while_pursuing(
    wake_on: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Default (non-strict): refuse_if_wake_dead is a no-op; warning fires instead."""
    monkeypatch.delenv("CURSOR_GOAL_ALLOW_DEAD_WAKE", raising=False)
    monkeypatch.delenv("CURSOR_GOAL_REQUIRE_WAKE", raising=False)
    assert run_cli("manage", "create", "need wake", "--test", "echo ok")[0] == 0
    assert wake_mod.refuse_if_wake_dead() is None
    warning = wake_mod.wake_dead_warning()
    assert warning is not None
    assert "wake" in warning.lower()
    assert "warning" in warning.lower()

    monkeypatch.setenv("CURSOR_GOAL_REQUIRE_WAKE", "1")
    msg = wake_mod.refuse_if_wake_dead()
    assert msg is not None
    assert "wake" in msg.lower()
    monkeypatch.setenv("CURSOR_GOAL_ALLOW_DEAD_WAKE", "1")
    assert wake_mod.refuse_if_wake_dead() is None
    assert wake_mod.wake_dead_warning() is None


def test_require_wake_strict_env_values(
    wake_on: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    del wake_on
    monkeypatch.delenv("CURSOR_GOAL_REQUIRE_WAKE", raising=False)
    assert wake_mod.require_wake_strict() is False
    for value in ("1", "true", "YES", "on"):
        monkeypatch.setenv("CURSOR_GOAL_REQUIRE_WAKE", value)
        assert wake_mod.require_wake_strict() is True
    monkeypatch.setenv("CURSOR_GOAL_REQUIRE_WAKE", "0")
    assert wake_mod.require_wake_strict() is False


def test_eval_validate_refuses_dead_wake(
    wake_on: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """CURSOR_GOAL_REQUIRE_WAKE=1 restores the strict hard-refusal behavior."""
    monkeypatch.delenv("CURSOR_GOAL_ALLOW_DEAD_WAKE", raising=False)
    monkeypatch.setenv("CURSOR_GOAL_REQUIRE_WAKE", "1")
    cmd = f'{__import__("sys").executable} -c "raise SystemExit(0)"'
    assert run_cli("manage", "create", "v", "--test", cmd)[0] == 0
    code, _out, err = run_cli("eval", "validate")
    assert code == 1
    assert "wake" in err.lower()


def test_eval_validate_warns_but_continues_by_default(
    wake_on: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Default (non-strict): eval validate warns about dead wake but still runs."""
    monkeypatch.delenv("CURSOR_GOAL_ALLOW_DEAD_WAKE", raising=False)
    monkeypatch.delenv("CURSOR_GOAL_REQUIRE_WAKE", raising=False)
    cmd = f'{__import__("sys").executable} -c "raise SystemExit(0)"'
    assert run_cli("manage", "create", "v2", "--test", cmd)[0] == 0
    code, _out, err = run_cli("eval", "validate")
    assert code == 0
    assert "warning" in err.lower()
    assert "wake" in err.lower()


def test_nudge_coalesce_invalid_timestamp(wake_on: Path) -> None:
    wake_mod.arm(interval=5)
    config = json.loads((wake_on / "wake.json").read_text(encoding="utf-8"))
    config["last_nudge_at"] = "not-a-timestamp"
    (wake_on / "wake.json").write_text(json.dumps(config), encoding="utf-8")
    assert wake_mod._nudge_within_coalesce_window(config) is False


def test_nudge_coalesce_invalid_source_ignored(wake_on: Path) -> None:
    wake_mod.arm(interval=30)
    config = json.loads((wake_on / "wake.json").read_text(encoding="utf-8"))
    config["last_nudge_at"] = wake_mod._now_iso()
    config["last_nudge_source"] = "other"
    (wake_on / "wake.json").write_text(json.dumps(config), encoding="utf-8")
    assert wake_mod._nudge_within_coalesce_window(config) is False


def test_record_agent_nudge_unknown_source_defaults_to_wake(wake_on: Path) -> None:
    wake_mod.arm(interval=5)
    wake_mod.record_agent_nudge(source="nope")
    stamped = json.loads((wake_on / "wake.json").read_text(encoding="utf-8"))
    assert stamped.get("last_nudge_source") == "wake"


def test_refuse_if_wake_dead_when_armed(
    wake_on: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.delenv("CURSOR_GOAL_ALLOW_DEAD_WAKE", raising=False)
    monkeypatch.setenv("CURSOR_GOAL_REQUIRE_WAKE", "1")
    assert run_cli("manage", "create", "armed dead")[0] == 0
    assert (wake_on / "wake.json").is_file()
    monkeypatch.setattr(
        wake_mod,
        "status_info",
        lambda: {
            "armed": True,
            "pid_alive": False,
            "interval_s": 5,
            "token_prefix": "x",
            "last_emit_at": None,
        },
    )
    msg = wake_mod.refuse_if_wake_dead()
    assert msg is not None
    assert "not alive" in msg.lower()
    monkeypatch.delenv("CURSOR_GOAL_REQUIRE_WAKE", raising=False)
    warning = wake_mod.wake_dead_warning()
    assert warning is not None
    assert "not alive" in warning.lower()


def test_assert_workdir_usable_empty() -> None:
    from cursor_goal.state import assert_workdir_usable

    assert assert_workdir_usable("  ") == ""


def test_refuse_if_wake_dead_not_pursuing(
    wake_on: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    del wake_on
    monkeypatch.delenv("CURSOR_GOAL_ALLOW_DEAD_WAKE", raising=False)
    assert wake_mod.refuse_if_wake_dead() is None


def test_refuse_if_wake_dead_when_pid_alive(
    wake_on: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.delenv("CURSOR_GOAL_ALLOW_DEAD_WAKE", raising=False)
    assert run_cli("manage", "create", "alive ok")[0] == 0
    assert (wake_on / "wake.json").is_file()
    monkeypatch.setattr(
        wake_mod,
        "continuation_readiness",
        lambda **_kwargs: {
            "continuation_ready": True,
            "reason": "ready",
            "heartbeat_stale": False,
            "command": "wake loop",
            "pattern": "^AGENT_GOAL_WAKE",
        },
    )
    assert wake_mod.refuse_if_wake_dead() is None


def test_refuse_if_wake_dead_when_unarmed(
    wake_on: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.delenv("CURSOR_GOAL_ALLOW_DEAD_WAKE", raising=False)
    monkeypatch.setenv("CURSOR_GOAL_REQUIRE_WAKE", "1")
    assert run_cli("manage", "create", "unarmed")[0] == 0
    monkeypatch.setattr(
        wake_mod,
        "continuation_readiness",
        lambda **_kwargs: {
            "continuation_ready": False,
            "reason": "not_armed",
            "heartbeat_stale": False,
            "command": "wake loop",
            "pattern": "^AGENT_GOAL_WAKE",
        },
    )
    msg = wake_mod.refuse_if_wake_dead()
    assert msg is not None
    assert "not armed" in msg.lower()
    assert "continuation_ready=false" in msg
    monkeypatch.delenv("CURSOR_GOAL_REQUIRE_WAKE", raising=False)
    warning = wake_mod.wake_dead_warning()
    assert warning is not None
    assert "not armed" in warning.lower()


def test_continuation_readiness_disabled(
    wake_on: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    del wake_on
    monkeypatch.setenv("CURSOR_GOAL_WAKE", "0")
    ready = wake_mod.continuation_readiness()
    assert ready["continuation_ready"] is True
    assert ready["reason"] == "disabled"


def test_continuation_readiness_not_pursuing(
    wake_on: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    del wake_on
    monkeypatch.setenv("CURSOR_GOAL_WAKE", "1")
    ready = wake_mod.continuation_readiness(goal_pursuing=False)
    assert ready["continuation_ready"] is True
    assert ready["reason"] == "not_pursuing"


def test_continuation_readiness_uses_config_emit_and_interval_defaults(
    wake_on: Path,
) -> None:
    """When last_emit_at/interval_s are not overridden, they come from wake.json."""
    assert run_cli("manage", "create", "config defaults")[0] == 0
    wake_mod.arm(interval=7)
    ready = wake_mod.continuation_readiness(
        armed=None,
        pid_alive=True,
        goal_pursuing=True,
    )
    assert ready["continuation_ready"] is True


def test_continuation_readiness_not_armed(wake_on: Path) -> None:
    assert run_cli("manage", "create", "unarmed readiness")[0] == 0
    ready = wake_mod.continuation_readiness(armed=False, goal_pursuing=True)
    assert ready["continuation_ready"] is False
    assert ready["reason"] == "not_armed"


def test_continuation_readiness_heartbeat_stale(
    wake_on: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    assert run_cli("manage", "create", "stale")[0] == 0
    ready = wake_mod.continuation_readiness(
        enabled=True,
        armed=True,
        pid_alive=True,
        goal_pursuing=True,
        last_emit_at="2000-01-01T00:00:00+00:00",
        interval_s=5,
    )
    assert ready["continuation_ready"] is True
    assert ready["heartbeat_stale"] is True
    assert ready["reason"] == "heartbeat_stale"


def test_format_wake_required_line(wake_on: Path) -> None:
    del wake_on
    line = wake_mod.format_wake_required_line(
        {"notify_pattern": "^AGENT_GOAL_WAKE", "interval_s": 15}
    )
    assert line.startswith("GOAL_WAKE_REQUIRED ")
    payload = json.loads(line[len("GOAL_WAKE_REQUIRED ") :])
    assert payload["pattern"] == "^AGENT_GOAL_WAKE"
    assert payload["notify_pattern"] == "^AGENT_GOAL_WAKE"
    assert payload["interval_s"] == 15


def test_notify_pattern_includes_followup_required() -> None:
    assert "FOLLOWUP_REQUIRED" in wake_mod.NOTIFY_PATTERN
    assert wake_mod.NOTIFY_PATTERN.startswith("^AGENT_GOAL_WAKE ")
    assert wake_mod.WAKE_FOLLOWUP_MARK in wake_mod.NOTIFY_PATTERN


def test_record_agent_nudge_no_config(wake_on: Path) -> None:
    del wake_on
    wake_mod.record_agent_nudge()  # no wake.json yet


def test_record_agent_nudge_write_oserror(
    wake_on: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    wake_mod.arm(interval=5)
    assert (wake_on / "wake.json").is_file()

    def boom(_cfg: object) -> None:
        raise OSError("disk full")

    monkeypatch.setattr(wake_mod, "_write_wake_config", boom)
    wake_mod.record_agent_nudge()  # swallows OSError


def test_nudge_within_coalesce_none() -> None:
    assert wake_mod._nudge_within_coalesce_window(None) is False


def test_heartbeat_stale_typing_edges() -> None:
    """Non-string / invalid ISO / string interval must not raise."""
    assert (
        wake_mod._heartbeat_stale(
            armed=True,
            pid_alive=True,
            last_emit_at=12345,
            interval_s=15,
        )
        is False
    )
    assert (
        wake_mod._heartbeat_stale(
            armed=True,
            pid_alive=True,
            last_emit_at="not-an-iso",
            interval_s=15,
        )
        is False
    )
    assert (
        wake_mod._heartbeat_stale(
            armed=True,
            pid_alive=True,
            last_emit_at="2000-01-01T00:00:00+00:00",
            interval_s="5",
        )
        is True
    )
    assert (
        wake_mod._heartbeat_stale(
            armed=True,
            pid_alive=True,
            last_emit_at="2000-01-01T00:00:00+00:00",
            interval_s="nope",
        )
        is True
    )
    assert (
        wake_mod._heartbeat_stale(
            armed=True,
            pid_alive=True,
            last_emit_at="2000-01-01T00:00:00+00:00",
            interval_s=True,  # bool must not be treated as int
        )
        is True
    )
    assert (
        wake_mod._heartbeat_stale(
            armed=False,
            pid_alive=True,
            last_emit_at="2000-01-01T00:00:00+00:00",
            interval_s=5,
        )
        is False
    )


def test_wake_arm_cli_falls_back_when_loop_path_unresolved(
    wake_on: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(
        wake_mod,
        "wake_loop_invocation",
        lambda: (_ for _ in ()).throw(ValueError("no skill")),
    )
    code, out, _err = run_cli("wake", "arm")
    assert code == 0
    assert "resolve with: manage harness-cmd" in out


def test_wake_loop_command_unresolved(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(
        wake_mod,
        "wake_loop_invocation",
        lambda: (_ for _ in ()).throw(ValueError("no skill")),
    )
    hint = wake_mod._wake_loop_command()
    assert "unresolved-skill" in hint


def test_pid_alive_true_for_current_process(wake_on: Path) -> None:
    """os.kill(pid, 0) on a real, currently-running process raises nothing."""
    del wake_on
    assert wake_process_mod._pid_alive(os.getpid()) is True


def test_mark_orphan_wake_oserror_is_swallowed(
    wake_on: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    del wake_on

    def boom(*_a: object, **_k: object) -> None:
        raise OSError("disk full")

    monkeypatch.setattr(wake_process_mod, "atomic_write_text", boom)
    # Must not raise: mark_orphan_wake() is a best-effort diagnostic write.
    wake_process_mod.mark_orphan_wake(4242, "test reason")
    assert wake_process_mod.read_orphan_wake() is None


def test_clear_orphan_wake_removes_existing_marker(wake_on: Path) -> None:
    del wake_on
    wake_process_mod.mark_orphan_wake(4242, "test reason")
    assert wake_process_mod.read_orphan_wake() is not None
    wake_process_mod.clear_orphan_wake()
    assert wake_process_mod.read_orphan_wake() is None


def test_clear_orphan_wake_oserror_is_swallowed(
    wake_on: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    wake_process_mod.mark_orphan_wake(4242, "test reason")

    real_unlink = Path.unlink

    def boom_unlink(self: Path, *a: object, **k: object) -> None:
        if self == wake_process_mod.wake_orphan_path():
            raise OSError("locked")
        return real_unlink(self, *a, **k)

    monkeypatch.setattr(Path, "unlink", boom_unlink)
    # Must not raise: clear_orphan_wake() only logs and continues on failure.
    wake_process_mod.clear_orphan_wake()


def test_read_orphan_wake_bad_json_returns_none(
    wake_on: Path,
) -> None:
    wake_process_mod.wake_orphan_path().write_text("not json{{{", encoding="utf-8")
    assert wake_process_mod.read_orphan_wake() is None


def test_read_orphan_wake_non_dict_returns_none(wake_on: Path) -> None:
    wake_process_mod.wake_orphan_path().write_text("[1, 2, 3]", encoding="utf-8")
    assert wake_process_mod.read_orphan_wake() is None


def test_clear_pid_unconditional_oserror_is_swallowed(
    wake_on: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    wake_process_mod._write_pid_record(123, "tok")
    real_unlink = Path.unlink

    def boom_unlink(self: Path, *a: object, **k: object) -> None:
        if self == wake_process_mod.wake_pid_path():
            raise OSError("locked")
        return real_unlink(self, *a, **k)

    monkeypatch.setattr(Path, "unlink", boom_unlink)
    # Unconditional clear (no only_if_pid/only_if_token): must not raise.
    wake_process_mod._clear_pid()


def test_unix_pid_looks_owned_ps_oserror(
    wake_on: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    # On Windows /proc never exists, so this always exercises the ps fallback.
    del wake_on

    def boom(*_a: object, **_k: object) -> None:
        raise OSError("ps: command not found")

    monkeypatch.setattr(wake_process_mod.subprocess, "run", boom)
    assert wake_process_mod._unix_pid_looks_owned(88) is False


def test_unix_pid_looks_owned_ps_timeout(
    wake_on: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    del wake_on

    def boom(*_a: object, **_k: object) -> None:
        raise subprocess.TimeoutExpired(cmd="ps", timeout=5)

    monkeypatch.setattr(wake_process_mod.subprocess, "run", boom)
    assert wake_process_mod._unix_pid_looks_owned(88) is False


def test_pid_looks_owned_rejects_non_positive_pid(wake_on: Path) -> None:
    del wake_on
    assert wake_process_mod._pid_looks_owned(0) is False
    assert wake_process_mod._pid_looks_owned(-1) is False


def test_pid_looks_owned_delegates_on_windows(
    wake_on: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    del wake_on
    monkeypatch.setattr(wake_process_mod.os, "name", "nt")
    monkeypatch.setattr(wake_process_mod, "_windows_pid_looks_owned", lambda _pid: True)
    assert wake_process_mod._pid_looks_owned(123) is True


def test_clear_pid_skips_record_rewritten_under_lock(
    wake_on: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A concurrent rewrite must not be unlinked by a stale only_if clearer."""
    del monkeypatch
    wake_process_mod._write_pid_record(1, "old")
    started = threading.Event()
    done_rewrite = threading.Event()

    def rewriter() -> None:
        with goal_lock():
            started.set()
            time.sleep(0.15)
            wake_process_mod._write_pid_record(2, "new")
        done_rewrite.set()

    thread = threading.Thread(target=rewriter)
    thread.start()
    assert started.wait(timeout=5)
    wake_process_mod._clear_pid(only_if_pid=1, only_if_token="old")
    assert done_rewrite.wait(timeout=5)
    thread.join(timeout=5)
    assert wake_process_mod._read_pid() == 2


def test_disarm_tokenless_pid_does_not_lock_timeout(wake_on: Path) -> None:
    wake_process_mod.wake_pid_path().write_text("12345\n", encoding="utf-8")
    started = time.monotonic()
    wake_mod.disarm(kill_loop=False)
    elapsed = time.monotonic() - started
    assert elapsed < 5
    assert not wake_process_mod.wake_pid_path().is_file()


def test_windows_ownership_probe_uses_pinned_powershell(
    wake_on: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    del wake_on
    captured: list[list[str]] = []

    class Res:
        stdout = "python run_goal.py wake loop"
        stderr = ""
        returncode = 0

    def fake_run(cmd: list[str], **_k: object) -> object:
        captured.append(list(cmd))
        return Res()

    pinned = r"C:\Windows\System32\WindowsPowerShell\v1.0\powershell.exe"
    monkeypatch.setattr(wake_process_mod, "_pinned_powershell", lambda: pinned)
    monkeypatch.setattr(wake_process_mod.subprocess, "run", fake_run)
    assert wake_process_mod._windows_pid_looks_owned(9) is True
    assert captured and captured[0][0] == pinned


def test_windows_ownership_probe_missing_pin_is_false(
    wake_on: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    del wake_on
    calls: list[object] = []
    monkeypatch.setattr(wake_process_mod, "_pinned_powershell", lambda: None)
    monkeypatch.setattr(
        wake_process_mod.subprocess, "run", lambda *_a, **_k: calls.append(1)
    )
    assert wake_process_mod._windows_pid_looks_owned(9) is False
    assert calls == []


def test_windows_kill_refuses_missing_taskkill(
    wake_on: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    del wake_on
    monkeypatch.setattr(wake_process_mod, "_read_pid_record", lambda: None)
    monkeypatch.setattr(wake_process_mod, "_pid_alive", lambda _p: True)
    monkeypatch.setattr(wake_process_mod.os, "name", "nt")
    monkeypatch.setattr(wake_process_mod, "_windows_pid_looks_owned", lambda _p: True)
    monkeypatch.setattr(wake_process_mod, "_pinned_taskkill", lambda: None)
    calls: list[object] = []
    monkeypatch.setattr(
        wake_process_mod.subprocess, "run", lambda *_a, **_k: calls.append(1)
    )
    wake_mod._kill_pid(4242, token="owned")
    assert calls == []
