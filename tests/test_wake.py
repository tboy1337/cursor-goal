"""Tests for cursor_goal.wake watchdog."""

from __future__ import annotations

import io
import json
from contextlib import redirect_stdout
from pathlib import Path

import pytest

import cursor_goal.wake as wake_mod
from tests.conftest import run_cli


@pytest.fixture()
def wake_on(goal_home: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    monkeypatch.setenv("CURSOR_GOAL_WAKE", "1")
    monkeypatch.setenv("CURSOR_GOAL_WAKE_INTERVAL_S", "5")
    return goal_home


def test_wake_disabled_skips_arm(goal_home: Path, monkeypatch: pytest.MonkeyPatch) -> None:
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
    assert data["notify_pattern"] == "^AGENT_GOAL_WAKE"


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
    payload = json.loads(out.split(" ", 1)[1])
    assert "[GOAL]" in payload["prompt"]
    assert "wake goal" in payload["prompt"]


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


def test_manage_create_arms_wake(wake_on: Path) -> None:
    code, out, _err = run_cli("manage", "create", "armed")
    assert code == 0
    assert "Wake armed" in out
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
    assert json.loads(line.split(" ", 1)[1]) == {"prompt": "hello"}


def test_wake_loop_exits_when_disarmed(
    wake_on: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Short loop: disarm config mid-sleep via tiny interval + clear config."""
    monkeypatch.setenv("CURSOR_GOAL_WAKE_INTERVAL_S", "5")
    assert run_cli("manage", "create", "loop goal")[0] == 0

    sleeps: list[float] = []

    def fake_sleep(seconds: float) -> None:
        sleeps.append(seconds)
        # After first sleep, clear wake.json so loop exits.
        if len(sleeps) == 1:
            wake_mod.disarm(kill_loop=False)

    monkeypatch.setattr(wake_mod.time, "sleep", fake_sleep)
    code = wake_mod.run_loop(interval=5)
    assert code == 0
    assert sleeps == [5]
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
    assert run_cli("manage", "create", "no arm file")[0] == 0
    wake_mod.disarm(kill_loop=False)
    code, out, _err = run_cli("wake", "tick")
    assert code == 0
    assert out.startswith("AGENT_GOAL_WAKE ")


def test_pid_helpers(wake_on: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    wake_mod._write_pid(1)
    assert wake_mod._read_pid() == 1
    # pid 1 may or may not be killable; _pid_alive should not raise
    assert isinstance(wake_mod._pid_alive(1), bool)
    assert wake_mod._pid_alive(-1) is False
    wake_mod._clear_pid()
    assert wake_mod._read_pid() is None

    # Corrupt pid file
    (wake_on / "wake.pid").write_text("not-a-pid\n", encoding="utf-8")
    assert wake_mod._read_pid() is None


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
    assert wake_mod._interval_from_env_or(45) == 45


def test_read_wake_config_corrupt_and_non_dict(wake_on: Path) -> None:
    path = wake_on / "wake.json"
    path.write_text("{not-json", encoding="utf-8")
    assert wake_mod._read_wake_config() is None
    path.write_text("[1,2,3]\n", encoding="utf-8")
    assert wake_mod._read_wake_config() is None


def test_pid_alive_exception_paths(
    wake_on: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    def raise_lookup(_pid: int, _sig: int) -> None:
        raise ProcessLookupError()

    monkeypatch.setattr(wake_mod.os, "kill", raise_lookup)
    assert wake_mod._pid_alive(12345) is False

    def raise_perm(_pid: int, _sig: int) -> None:
        raise PermissionError()

    monkeypatch.setattr(wake_mod.os, "kill", raise_perm)
    assert wake_mod._pid_alive(12345) is True

    def raise_os(_pid: int, _sig: int) -> None:
        raise OSError("nope")

    monkeypatch.setattr(wake_mod.os, "kill", raise_os)
    assert wake_mod._pid_alive(12345) is False


def test_clear_pid_oserror(wake_on: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    class BoomPath:
        def unlink(self, *, missing_ok: bool = False) -> None:
            raise OSError("busy")

    monkeypatch.setattr(wake_mod, "wake_pid_path", lambda: BoomPath())
    wake_mod._clear_pid()


def test_kill_pid_dead_and_oserror(
    wake_on: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(wake_mod, "_pid_alive", lambda _pid: False)
    wake_mod._kill_pid(999999)

    monkeypatch.setattr(wake_mod, "_pid_alive", lambda _pid: True)

    def boom(_pid: int, _sig: int) -> None:
        raise OSError("denied")

    monkeypatch.setattr(wake_mod.os, "kill", boom)
    wake_mod._kill_pid(4242)


def test_followup_prompt_without_goal(wake_on: Path) -> None:
    msg = wake_mod._followup_prompt()
    assert "[GOAL]" in msg


def test_tick_when_wake_disabled(
    wake_on: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("CURSOR_GOAL_WAKE", "0")
    assert wake_mod.tick() == 0


def test_run_loop_when_disabled(
    wake_on: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
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
    code, out, _err = run_cli("manage", "create", "arm fail unique", "--force")
    assert code == 0
    assert "Wake armed" not in out


def test_disarm_kills_foreign_pid(
    wake_on: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    wake_mod.arm(interval=5)
    wake_mod._write_pid(999001)
    killed: list[int] = []

    def fake_kill(pid: int) -> None:
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
