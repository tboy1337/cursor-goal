"""Tests for cursor_goal.manage."""

from __future__ import annotations

import sys
from pathlib import Path

from tests.conftest import load_goal_json, run_cli


def test_manage_create(goal_home: Path) -> None:
    code, out, _err = run_cli("manage", "create", "test condition")
    assert code == 0
    assert (goal_home / "goal.json").is_file()
    data = load_goal_json(goal_home)
    assert data["condition"] == "test condition"
    assert data["status"] == "pursuing"
    assert data["turn_budget"] == 20
    assert data["wake_budget"] == 200
    assert data["shell_ok"] is True
    assert data["schema_version"] == 3
    assert data["active"] is True
    assert "Goal created" in out
    assert "Wake budget: 200" in out


def test_manage_create_wake_budget_and_deny_shell(goal_home: Path) -> None:
    code, out, _err = run_cli(
        "manage",
        "create",
        "secure",
        "--test",
        "echo a && echo b",
        "--budget",
        "5",
        "--wake-budget",
        "40",
        "--deny-shell",
    )
    assert code == 0
    data = load_goal_json(goal_home)
    assert data["wake_budget"] == 40
    assert data["shell_ok"] is False
    assert data["turn_budget"] == 5
    assert "Shell ok: false" in out
    assert "Validation mode: denied" in out or "Validation mode: argv" in out


def test_manage_doctor_ok(goal_home: Path) -> None:
    code, out, _err = run_cli("manage", "doctor")
    assert code == 0
    assert "Doctor" in out
    assert "OK" in out


def test_manage_doctor_with_pursuing_shell_goal(
    goal_home: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from cursor_goal import manage as manage_mod

    assert (
        run_cli(
            "manage",
            "create",
            "doc",
            "--test",
            "echo a && echo b",
            "--budget",
            "3",
        )[0]
        == 0
    )
    monkeypatch.setattr(manage_mod, "_hooks_look_configured", lambda: True)
    code, out, _err = run_cli("manage", "doctor")
    assert code == 0
    assert "pursuing" in out
    assert "shell" in out.lower() or "Warning" in out


def test_manage_doctor_hooks_false(
    goal_home: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from cursor_goal import manage as manage_mod

    del goal_home
    monkeypatch.setattr(manage_mod, "_hooks_look_configured", lambda: False)
    code, _out, err = run_cli("manage", "doctor")
    assert code == 1
    assert "FAIL" in err or "no stop hook" in err


def test_manage_doctor_corrupt_goal(goal_home: Path) -> None:
    (goal_home / "goal.json").write_text("{bad", encoding="utf-8")
    code, _out, err = run_cli("manage", "doctor")
    assert code == 1
    assert "Corrupt" in err or "FAIL" in err


def test_manage_status_shows_wake_budget(goal_home: Path) -> None:
    assert run_cli("manage", "create", "s", "--budget", "4")[0] == 0
    code, out, _err = run_cli("manage", "status")
    assert code == 0
    assert "Wake ticks: 0 / 40" in out
    assert "Validation mode:" in out
    assert "Wake service:" in out


def test_hooks_look_configured_true(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from cursor_goal import manage as manage_mod

    fake_home = tmp_path / "home"
    cursor = fake_home / ".cursor"
    cursor.mkdir(parents=True)
    (cursor / "hooks.json").write_text(
        '{"hooks":{"stop":[{"command":"stop_hook.cmd"}]}}',
        encoding="utf-8",
    )
    monkeypatch.setattr(manage_mod.Path, "home", lambda: fake_home)
    assert manage_mod._hooks_look_configured() is True


def test_hooks_look_configured_skill_without_hooks(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from cursor_goal import manage as manage_mod

    fake_home = tmp_path / "home"
    skill = fake_home / ".cursor" / "skills" / "goal" / "scripts"
    skill.mkdir(parents=True)
    (skill / "stop_hook.py").write_text("#", encoding="utf-8")
    monkeypatch.setattr(manage_mod.Path, "home", lambda: fake_home)
    assert manage_mod._hooks_look_configured() is False


def test_hooks_look_configured_none(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from cursor_goal import manage as manage_mod

    fake_home = tmp_path / "emptyhome"
    fake_home.mkdir()
    monkeypatch.setattr(manage_mod.Path, "home", lambda: fake_home)
    assert manage_mod._hooks_look_configured() is None


def test_manage_doctor_wake_armed_dead(
    goal_home: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from cursor_goal import manage as manage_mod
    from cursor_goal import wake as wake_mod

    assert run_cli("manage", "create", "armed")[0] == 0
    wake_mod.arm(interval=5)
    monkeypatch.setattr(manage_mod, "_hooks_look_configured", lambda: True)
    monkeypatch.setattr(
        manage_mod,
        "wake_status_info",
        lambda: {
            "armed": True,
            "pid_alive": False,
            "interval_s": 15,
            "token_prefix": "abcd",
            "last_emit_at": None,
        },
    )
    code, out, _err = run_cli("manage", "doctor")
    assert code == 0
    assert "not alive" in out or "Warning" in out


def test_manage_doctor_log_secrets_warning(
    goal_home: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from cursor_goal import manage as manage_mod

    monkeypatch.setenv("CURSOR_GOAL_LOG_SECRETS", "1")
    monkeypatch.setattr(manage_mod, "_hooks_look_configured", lambda: None)
    code, out, _err = run_cli("manage", "doctor")
    assert code == 0
    assert "CURSOR_GOAL_LOG_SECRETS" in out


def test_manage_doctor_insecure_dir(
    goal_home: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from cursor_goal import manage as manage_mod

    monkeypatch.setattr(manage_mod, "data_dir_is_insecure", lambda _p=None: True)
    monkeypatch.setattr(manage_mod, "_hooks_look_configured", lambda: True)
    code, _out, err = run_cli("manage", "doctor")
    assert code == 1
    assert "world-writable" in err or "FAIL" in err


def test_hooks_look_configured_read_oserror(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from cursor_goal import manage as manage_mod

    fake_home = tmp_path / "home"
    cursor = fake_home / ".cursor"
    cursor.mkdir(parents=True)
    hooks = cursor / "hooks.json"
    hooks.write_text("{}", encoding="utf-8")

    def boom_read(self: Path, *_a: object, **_k: object) -> str:
        if self.name == "hooks.json":
            raise OSError("denied")
        return Path.read_text(self, *_a, **_k)

    monkeypatch.setattr(manage_mod.Path, "home", lambda: fake_home)
    monkeypatch.setattr(Path, "read_text", boom_read)
    assert manage_mod._hooks_look_configured() is None


def test_manage_create_wake_budget_requires_value(goal_home: Path) -> None:
    code, _out, err = run_cli("manage", "create", "x", "--wake-budget")
    assert code == 1
    assert "--wake-budget requires a value" in err


def test_validation_mode_deny_shell_argv(
    goal_home: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from cursor_goal.manage import _validation_mode
    from cursor_goal.state import GoalState

    del goal_home
    monkeypatch.setenv("CURSOR_GOAL_DENY_SHELL", "1")
    assert (
        _validation_mode(GoalState(validation_command="pytest -q", shell_ok=True))
        == "argv"
    )


def test_manage_status_corrupt(goal_home: Path) -> None:
    (goal_home / "goal.json").write_text("{bad", encoding="utf-8")
    code, _out, err = run_cli("manage", "status")
    assert code == 1
    assert "Error" in err


def test_manage_doctor_paused_goal(goal_home: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    from cursor_goal import manage as manage_mod

    assert run_cli("manage", "create", "p")[0] == 0
    assert run_cli("manage", "pause")[0] == 0
    monkeypatch.setattr(manage_mod, "_hooks_look_configured", lambda: True)
    (goal_home / "last-stop-response.json").write_text("{}", encoding="utf-8")
    code, out, _err = run_cli("manage", "doctor")
    assert code == 0
    assert "paused" in out
    assert "last-stop-response" in out


def test_manage_create_empty(goal_home: Path) -> None:
    code, _out, err = run_cli("manage", "create", "")
    assert code == 1
    assert "condition is required" in err


def test_manage_create_invalid_budget(goal_home: Path) -> None:
    code, _out, err = run_cli("manage", "create", "x", "--budget", "abc")
    assert code == 1
    assert "Budget" in err


def test_manage_create_refuses_overwrite(goal_home: Path) -> None:
    assert run_cli("manage", "create", "first")[0] == 0
    code, _out, err = run_cli("manage", "create", "second")
    assert code == 1
    assert "already exists" in err
    assert load_goal_json(goal_home)["condition"] == "first"


def test_manage_create_force_overwrite(goal_home: Path) -> None:
    assert run_cli("manage", "create", "first")[0] == 0
    code, _out, _err = run_cli("manage", "create", "second", "--force")
    assert code == 0
    assert load_goal_json(goal_home)["condition"] == "second"


def test_manage_status(goal_home: Path) -> None:
    run_cli("manage", "create", "test condition")
    code, out, _err = run_cli("manage", "status")
    assert code == 0
    assert "Condition: test condition" in out
    assert "Status: pursuing" in out


def test_manage_done_without_signal(goal_home: Path) -> None:
    run_cli("manage", "create", "test condition")
    code, _out, err = run_cli("manage", "done")
    assert code == 1
    assert "REJECTED" in err


def test_manage_done_with_signal(goal_home: Path) -> None:
    run_cli("manage", "create", "test condition")
    run_cli("manage", "pause")
    run_cli("manage", "resume")
    run_cli("eval", "parse-result", "YES: ready")
    code, out, _err = run_cli("manage", "done")
    assert code == 0
    data = load_goal_json(goal_home)
    assert data["status"] == "achieved"
    assert data["active"] is False
    assert "Goal achieved" in out
    assert not (goal_home / "goal-eval-done").exists()


def test_manage_done_force(goal_home: Path) -> None:
    run_cli("manage", "create", "test condition")
    code, _out, _err = run_cli("manage", "done", "--force")
    assert code == 0
    assert load_goal_json(goal_home)["status"] == "achieved"


def test_manage_pause_resume(goal_home: Path) -> None:
    run_cli("manage", "create", "test condition")
    assert run_cli("manage", "pause")[0] == 0
    data = load_goal_json(goal_home)
    assert data["status"] == "paused"
    assert data["active"] is False
    code, out, _err = run_cli("manage", "status")
    assert code == 0
    assert "Active: false" in out
    assert run_cli("manage", "resume")[0] == 0
    assert load_goal_json(goal_home)["status"] == "pursuing"
    assert load_goal_json(goal_home)["active"] is True


def test_manage_status_corrupt_goal(goal_home: Path) -> None:
    (goal_home / "goal.json").write_text("{not-json", encoding="utf-8")
    code, _out, err = run_cli("manage", "status")
    assert code == 1
    assert "corrupt" in err.lower() or "unreadable" in err.lower()


def test_manage_create_rejects_huge_budget(goal_home: Path) -> None:
    code, _out, err = run_cli("manage", "create", "x", "--budget", "501")
    assert code == 1
    assert "500" in err


def test_manage_help_and_unknown(goal_home: Path) -> None:
    assert run_cli("manage")[0] == 1
    assert run_cli("manage", "help")[0] == 0
    code, out, err = run_cli("manage", "nope")
    assert code == 1
    assert "Usage:" in out
    assert "unknown manage command" in err


def test_manage_clear_removes_signal(goal_home: Path) -> None:
    run_cli("manage", "create", "test condition")
    run_cli("eval", "parse-result", "YES: done")
    assert (goal_home / "goal-eval-done").is_file()
    assert run_cli("manage", "clear")[0] == 0
    assert not (goal_home / "goal.json").exists()
    assert not (goal_home / "goal-eval-done").exists()


def test_manage_status_no_goal(goal_home: Path) -> None:
    code, out, _err = run_cli("manage", "status")
    assert code == 0
    assert "No active goal" in out


def test_manage_status_with_validation_and_reason(goal_home: Path) -> None:
    run_cli("manage", "create", "cond", "--test", "echo ok")
    run_cli("eval", "parse-result", "NO: still broken")
    code, out, _err = run_cli("manage", "status")
    assert code == 0
    assert "Validation: echo ok" in out
    assert "Last evaluation: still broken" in out
    assert "Last verdict: NO" in out


def test_manage_status_shows_validation_exit(goal_home: Path) -> None:
    run_cli("manage", "create", "cond", "--test", f'{sys.executable} -c "print(1)"')
    assert run_cli("eval", "validate")[0] == 0
    code, out, _err = run_cli("manage", "status")
    assert code == 0
    assert "Last validation exit: 0" in out


def test_manage_create_with_test_and_budget(goal_home: Path) -> None:
    code, out, _err = run_cli(
        "manage", "create", "ship it", "--test", "pytest -q", "--budget", "8"
    )
    assert code == 0
    data = load_goal_json(goal_home)
    assert data["validation_command"] == "pytest -q"
    assert data["turn_budget"] == 8
    assert "Validation: pytest -q" in out


def test_manage_create_flags_before_condition(goal_home: Path) -> None:
    """Cover create argv when the first token is a flag (no positional)."""
    code, _out, err = run_cli("manage", "create", "--budget", "5")
    assert code == 1
    assert "condition is required" in err


def test_manage_create_rejects_unknown_tokens(goal_home: Path) -> None:
    """Unknown create flags must fail loudly."""
    code, _out, err = run_cli(
        "manage", "create", "ship it", "--unknown", "x", "--budget", "7"
    )
    assert code == 1
    assert "Unknown argument" in err


def test_manage_create_rejects_missing_flag_value(goal_home: Path) -> None:
    code, _out, err = run_cli("manage", "create", "ship it", "--test")
    assert code == 1
    assert "--test requires a value" in err
    code2, _out2, err2 = run_cli("manage", "create", "ship it", "--budget")
    assert code2 == 1
    assert "--budget requires a value" in err2


def test_manage_create_rejects_overlong_condition(goal_home: Path) -> None:
    code, _out, err = run_cli("manage", "create", "x" * 4001)
    assert code == 1
    assert "4000" in err


def test_manage_create_rejects_zero_budget(goal_home: Path) -> None:
    code, _out, err = run_cli("manage", "create", "x", "--budget", "0")
    assert code == 1
    assert "Budget" in err


def test_manage_pause_resume_errors(goal_home: Path) -> None:
    assert run_cli("manage", "pause")[0] == 1
    assert run_cli("manage", "resume")[0] == 1
    run_cli("manage", "create", "g")
    assert run_cli("manage", "resume")[0] == 1
    run_cli("manage", "pause")
    assert run_cli("manage", "pause")[0] == 1


def test_manage_done_no_goal(goal_home: Path) -> None:
    code, out, _err = run_cli("manage", "done")
    assert code == 1
    assert "No active goal" in out


def test_manage_clear_when_absent(goal_home: Path) -> None:
    code, out, _err = run_cli("manage", "clear")
    assert code == 0
    assert "No active goal" in out
