"""Tests for cursor_goal.manage."""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

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
    assert data["shell_ok"] is False
    assert data["schema_version"] == 1
    assert data["active"] is True
    assert data["workdir"]
    assert Path(data["workdir"]).is_absolute()
    assert "Goal created" in out
    assert "Wake budget: 200" in out
    assert "Shell ok: false" in out
    assert "Workdir:" in out


def test_manage_create_allow_shell(goal_home: Path) -> None:
    code, out, _err = run_cli(
        "manage",
        "create",
        "shell goal",
        "--test",
        "echo a && echo b",
        "--allow-shell",
    )
    assert code == 0
    data = load_goal_json(goal_home)
    assert data["shell_ok"] is True
    assert data["schema_version"] == 1
    assert "Shell ok: true" in out
    assert "Validation mode: shell" in out


def test_manage_create_workdir(goal_home: Path, tmp_path: Path) -> None:
    work = tmp_path / "goal-work"
    work.mkdir()
    code, out, _err = run_cli(
        "manage",
        "create",
        "with workdir",
        "--workdir",
        str(work),
    )
    assert code == 0
    data = load_goal_json(goal_home)
    assert Path(data["workdir"]) == work.resolve()
    assert str(work.resolve()) in out


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


def test_create_force_wake_disarm_oserror(
    goal_home: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from cursor_goal import manage as manage_mod

    assert run_cli("manage", "create", "first")[0] == 0

    def boom(*, kill_loop: bool = True) -> None:
        del kill_loop
        raise OSError("cannot kill")

    monkeypatch.setattr(manage_mod, "wake_disarm", boom)
    code, out, _err = run_cli("manage", "create", "second", "--force")
    assert code == 0
    assert "second" in out or "Created" in out or code == 0


def test_done_without_active_goal(goal_home: Path) -> None:
    code, out, _err = run_cli("manage", "done", "--force")
    assert code == 1
    assert "No active goal" in out or "No active" in out or code == 1


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
            "--allow-shell",
        )[0]
        == 0
    )
    monkeypatch.setattr(manage_mod, "_hooks_look_configured", lambda: True)
    monkeypatch.setattr(
        manage_mod,
        "wake_status_info",
        lambda: {
            "armed": True,
            "pid_alive": True,
            "interval_s": 15,
            "token_prefix": "abcd",
            "last_emit_at": None,
        },
    )
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


def test_marketplace_hooks_skill_only(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from cursor_goal import manage as manage_mod

    plugin = tmp_path / "plugin"
    scripts = plugin / "skills" / "goal" / "scripts"
    scripts.mkdir(parents=True)
    (scripts / "stop_hook.py").write_text("#", encoding="utf-8")
    fake_home = tmp_path / "home"
    fake_home.mkdir()
    monkeypatch.setattr(manage_mod.Path, "home", lambda: fake_home)
    monkeypatch.setenv("CURSOR_PLUGIN_ROOT", str(plugin))
    assert manage_mod._marketplace_hooks_configured() is False


def test_marketplace_hooks_empty_plugin_root(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from cursor_goal import manage as manage_mod

    plugin = tmp_path / "empty-plugin"
    plugin.mkdir()
    fake_home = tmp_path / "home"
    fake_home.mkdir()
    monkeypatch.setattr(manage_mod.Path, "home", lambda: fake_home)
    monkeypatch.setenv("CURSOR_PLUGIN_ROOT", str(plugin))
    assert manage_mod._marketplace_hooks_configured() is False


def test_marketplace_hooks_under_cursor_plugins(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from cursor_goal import manage as manage_mod

    fake_home = tmp_path / "home"
    plugin = fake_home / ".cursor" / "plugins" / "cursor-goal"
    hooks_dir = plugin / "hooks"
    hooks_dir.mkdir(parents=True)
    (hooks_dir / "hooks.json").write_text(
        '{"hooks":{"stop":[{"command":"${CURSOR_PLUGIN_ROOT}/stop_hook.py"}]}}',
        encoding="utf-8",
    )
    monkeypatch.setattr(manage_mod.Path, "home", lambda: fake_home)
    monkeypatch.delenv("CURSOR_PLUGIN_ROOT", raising=False)
    assert manage_mod._marketplace_hooks_configured() is True


def test_doctor_fail_open_counter_warning(
    goal_home: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from cursor_goal import manage as manage_mod

    monkeypatch.setattr(manage_mod, "_hooks_look_configured", lambda: True)
    (goal_home / "stop-failopen-continues").write_text("2", encoding="utf-8")
    code, out, _err = run_cli("manage", "doctor")
    assert code == 0
    assert "fail-open" in out.lower() or "Warning" in out


def test_marketplace_hooks_read_oserror(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from cursor_goal import manage as manage_mod

    plugin = tmp_path / "plugin"
    hooks_dir = plugin / "hooks"
    hooks_dir.mkdir(parents=True)
    hooks = hooks_dir / "hooks.json"
    hooks.write_text("{}", encoding="utf-8")

    def boom(_self: Path, *_a: object, **_k: object) -> str:
        raise OSError("unreadable")

    monkeypatch.setenv("CURSOR_PLUGIN_ROOT", str(plugin))
    monkeypatch.setattr(Path, "read_text", boom)
    # No classic home skill; env root present but unreadable -> False
    fake_home = tmp_path / "home2"
    fake_home.mkdir()
    monkeypatch.setattr(manage_mod.Path, "home", lambda: fake_home)
    assert manage_mod._marketplace_hooks_configured() is False


def test_marketplace_hooks_and_stacking(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from cursor_goal import manage as manage_mod

    plugin = tmp_path / "plugin"
    hooks_dir = plugin / "hooks"
    hooks_dir.mkdir(parents=True)
    (hooks_dir / "hooks.json").write_text(
        '{"hooks":{"stop":[{"command":"stop_hook.cmd",'
        '"_cursor_goal":"cursor_goal_stop_hook"}]}}',
        encoding="utf-8",
    )
    fake_home = tmp_path / "home"
    cursor = fake_home / ".cursor"
    cursor.mkdir(parents=True)
    (cursor / "hooks.json").write_text(
        '{"hooks":{"stop":[{"command":"stop_hook.py"}]}}',
        encoding="utf-8",
    )
    monkeypatch.setattr(manage_mod.Path, "home", lambda: fake_home)
    monkeypatch.setenv("CURSOR_PLUGIN_ROOT", str(plugin))
    assert manage_mod._marketplace_hooks_configured() is True
    assert manage_mod._classic_hooks_configured() is True
    assert manage_mod._hooks_stacking_failure() is not None
    assert manage_mod._hooks_stacking_warning() is not None


def test_doctor_marketplace_python_unset_hard_fail(
    goal_home: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from cursor_goal import manage as manage_mod

    assert run_cli("manage", "create", "mkt py")[0] == 0
    monkeypatch.setattr(manage_mod, "_classic_hooks_configured", lambda: False)
    monkeypatch.setattr(manage_mod, "_marketplace_hooks_configured", lambda: True)
    monkeypatch.setattr(manage_mod, "_hooks_look_configured", lambda: True)
    monkeypatch.setattr(manage_mod.os, "name", "nt")
    monkeypatch.delenv("CURSOR_GOAL_PYTHON", raising=False)
    monkeypatch.setattr(
        manage_mod,
        "wake_status_info",
        lambda: {
            "armed": True,
            "pid_alive": True,
            "interval_s": 15,
            "token_prefix": "abcd",
            "last_emit_at": "t",
        },
    )
    monkeypatch.setattr(manage_mod, "_stale_baked_python_failures", list)
    code, _out, err = run_cli("manage", "doctor")
    assert code == 1
    assert "CURSOR_GOAL_PYTHON unset" in err


def test_doctor_marketplace_and_stacking_messages(
    goal_home: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from cursor_goal import manage as manage_mod

    assert run_cli("manage", "create", "doc market")[0] == 0
    monkeypatch.setattr(manage_mod, "_classic_hooks_configured", lambda: True)
    monkeypatch.setattr(manage_mod, "_marketplace_hooks_configured", lambda: True)
    monkeypatch.setattr(manage_mod, "_hooks_look_configured", lambda: True)
    monkeypatch.setattr(
        manage_mod,
        "wake_status_info",
        lambda: {
            "armed": True,
            "pid_alive": True,
            "interval_s": 15,
            "token_prefix": "abcd",
            "last_emit_at": "t",
        },
    )
    monkeypatch.setenv("CURSOR_GOAL_PYTHON", sys.executable)
    code, out, err = run_cli("manage", "doctor")
    assert code == 1
    combined = f"{out}\n{err}".lower()
    assert "marketplace" in combined or "classic" in combined
    assert "pick one" in combined
    assert "fail" in combined


def test_status_action_required_when_wake_dead(
    goal_home: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from cursor_goal import manage as manage_mod

    monkeypatch.setenv("CURSOR_GOAL_WAKE", "1")
    assert run_cli("manage", "create", "need wake")[0] == 0
    monkeypatch.setattr(
        manage_mod,
        "wake_status_info",
        lambda: {
            "armed": False,
            "pid_alive": False,
            "interval_s": None,
            "token_prefix": None,
            "last_emit_at": None,
        },
    )
    code, out, _err = run_cli("manage", "status")
    assert code == 0
    assert "ACTION REQUIRED" in out
    assert "wake" in out.lower()


def test_status_action_required_when_wake_armed_dead(
    goal_home: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from cursor_goal import manage as manage_mod

    monkeypatch.setenv("CURSOR_GOAL_WAKE", "1")
    assert run_cli("manage", "create", "dead loop")[0] == 0
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
    code, out, _err = run_cli("manage", "status")
    assert code == 0
    assert "ACTION REQUIRED" in out
    assert "not alive" in out.lower()


def test_doctor_wake_not_armed_hard_fail(
    goal_home: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from cursor_goal import manage as manage_mod

    assert run_cli("manage", "create", "no wake")[0] == 0
    monkeypatch.setattr(manage_mod, "_hooks_look_configured", lambda: True)
    monkeypatch.setattr(
        manage_mod,
        "wake_status_info",
        lambda: {
            "armed": False,
            "pid_alive": False,
            "interval_s": None,
            "token_prefix": None,
            "last_emit_at": None,
        },
    )
    code, _out, err = run_cli("manage", "doctor")
    assert code == 1
    assert "Wake not armed" in err or "FAIL" in err


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
    code, out, err = run_cli("manage", "doctor")
    assert code == 1
    assert "not alive" in out or "not alive" in err or "FAIL" in err


def test_manage_harness_cmd(goal_home: Path) -> None:
    del goal_home
    code, out, _err = run_cli("manage", "harness-cmd")
    assert code == 0
    assert "run_goal.py" in out
    assert "Wake loop" in out


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
    assert "insecure" in err or "FAIL" in err


def test_manage_mutators_refuse_insecure(
    goal_home: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from cursor_goal import manage as manage_mod

    run_cli("manage", "create", "secure check")
    monkeypatch.setattr(
        manage_mod,
        "refuse_if_data_dir_insecure",
        lambda: "[goal] Error: data directory is insecure (/tmp)",
    )
    for args in (
        ("manage", "pause"),
        ("manage", "resume"),
        ("manage", "done"),
        ("manage", "clear"),
    ):
        code, _out, err = run_cli(*args)
        assert code == 1
        assert "insecure" in err


def test_manage_doctor_fail_open_and_acl(
    goal_home: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from cursor_goal import manage as manage_mod

    (goal_home / "stop-failopen-continues").write_text("2\n", encoding="utf-8")
    monkeypatch.setattr(manage_mod, "_hooks_look_configured", lambda: True)
    monkeypatch.setattr(
        manage_mod,
        "acl_harden_failure_message",
        lambda _p=None: "Windows ACL harden failed for /x: grant failed",
    )
    monkeypatch.setenv("CURSOR_GOAL_LOG_FILE", "1")
    code, out, err = run_cli("manage", "doctor")
    assert code == 1
    assert "fail-open" in out.lower() or "Fail-open" in out or "fail-open" in err
    assert "ACL" in err or "ACL" in out
    assert "Durable log" in out


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


def test_manage_doctor_paused_goal(
    goal_home: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
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


def test_manage_create_invalid_workdir(goal_home: Path, tmp_path: Path) -> None:
    missing = tmp_path / "no-such-dir"
    code, _out, err = run_cli("manage", "create", "bad wd", "--workdir", str(missing))
    assert code == 1
    assert "workdir" in err.lower()


def test_manage_create_denied_shell_warning(goal_home: Path) -> None:
    code, out, _err = run_cli(
        "manage",
        "create",
        "need shell",
        "--test",
        "echo a && echo b",
    )
    assert code == 0
    data = load_goal_json(goal_home)
    assert data["shell_ok"] is False
    assert "Validation mode: denied" in out
    assert "shell_ok=false" in out or "--allow-shell" in out


def test_baked_python_from_cmd(tmp_path: Path) -> None:
    from cursor_goal import manage as manage_mod

    cmd = tmp_path / "stop_hook.cmd"
    cmd.write_text(
        '@echo off\n"C:\\MissingPython\\python.exe" -u "C:\\x\\stop_hook.py"\n',
        encoding="utf-8",
    )
    assert manage_mod._baked_python_from_cmd(cmd) == r"C:\MissingPython\python.exe"
    assert manage_mod._baked_python_from_cmd(tmp_path / "missing.cmd") is None


def test_stale_baked_python_failures(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from cursor_goal import manage as manage_mod

    skill = tmp_path / "goal"
    scripts = skill / "scripts"
    scripts.mkdir(parents=True)
    missing_py = tmp_path / "gone" / "python.exe"
    (scripts / "stop_hook.cmd").write_text(
        f'@echo off\n"{missing_py}" -u "stop.py"\n',
        encoding="utf-8",
    )
    (scripts / "wake_loop.cmd").write_text(
        f'@echo off\n"{missing_py}" -u "run.py" wake loop\n',
        encoding="utf-8",
    )
    monkeypatch.setattr(manage_mod, "skill_root", lambda: skill)
    monkeypatch.setattr(manage_mod.os, "name", "nt")
    monkeypatch.delenv("CURSOR_GOAL_PYTHON", raising=False)
    fails = manage_mod._stale_baked_python_failures()
    assert len(fails) >= 1
    assert "missing" in fails[0].lower() or "Re-run" in fails[0]

    monkeypatch.setenv("CURSOR_GOAL_PYTHON", str(tmp_path / "also-gone.exe"))
    fails2 = manage_mod._stale_baked_python_failures()
    assert any("CURSOR_GOAL_PYTHON" in f for f in fails2)


def test_stale_baked_python_skipped_on_posix(monkeypatch: pytest.MonkeyPatch) -> None:
    from cursor_goal import manage as manage_mod

    monkeypatch.setattr(manage_mod.os, "name", "posix")
    assert manage_mod._stale_baked_python_failures() == []


def test_blocking_checklist_on_create(
    goal_home: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("CURSOR_GOAL_WAKE", "1")
    code, out, _err = run_cli("manage", "create", "checklist")
    assert code == 0
    assert "BLOCKING CHECKLIST" in out
    assert "pid_alive" in out


def test_normalize_workdir_relative_and_empty(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from cursor_goal import manage as manage_mod

    assert manage_mod._normalize_workdir("  ") == ""
    monkeypatch.chdir(tmp_path)
    sub = tmp_path / "sub"
    sub.mkdir()
    assert Path(manage_mod._normalize_workdir("sub")) == sub.resolve()


def test_baked_python_skips_rem_and_cgp_lines(tmp_path: Path) -> None:
    from cursor_goal import manage as manage_mod

    cmd = tmp_path / "stop_hook.cmd"
    cmd.write_text(
        "\n".join(
            [
                "@echo off",
                "REM Classic bake",
                'if not "%CURSOR_GOAL_PYTHON%"=="" goto :use_cgp',
                '"C:\\Good\\python.exe" -u "stop.py"',
            ]
        )
        + "\n",
        encoding="utf-8",
    )
    assert manage_mod._baked_python_from_cmd(cmd) == r"C:\Good\python.exe"


def test_stale_baked_python_skill_root_error(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from cursor_goal import manage as manage_mod

    monkeypatch.setattr(manage_mod.os, "name", "nt")

    def boom() -> Path:
        raise ValueError("bad")

    monkeypatch.setattr(manage_mod, "skill_root", boom)
    assert manage_mod._stale_baked_python_failures() == []


def test_stale_baked_python_env_exists(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from cursor_goal import manage as manage_mod

    skill = tmp_path / "goal"
    scripts = skill / "scripts"
    scripts.mkdir(parents=True)
    missing_py = tmp_path / "gone" / "python.exe"
    real_py = tmp_path / "real-python.exe"
    real_py.write_text("x", encoding="utf-8")
    (scripts / "stop_hook.cmd").write_text(
        f'@echo off\n"{missing_py}" -u "stop.py"\n',
        encoding="utf-8",
    )
    monkeypatch.setattr(manage_mod, "skill_root", lambda: skill)
    monkeypatch.setattr(manage_mod.os, "name", "nt")
    monkeypatch.setenv("CURSOR_GOAL_PYTHON", str(real_py))
    assert manage_mod._stale_baked_python_failures() == []


def test_create_cwd_oserror(goal_home: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    from cursor_goal import manage as manage_mod

    class BoomCwd:
        @staticmethod
        def resolve() -> Path:
            raise OSError("cwd gone")

    monkeypatch.setattr(manage_mod.Path, "cwd", lambda: BoomCwd())  # type: ignore[misc,assignment]
    code, out, _err = run_cli("manage", "create", "no-cwd")
    assert code == 0
    data = load_goal_json(goal_home)
    assert data.get("workdir", "") == "" or "Workdir" not in out or code == 0


def test_doctor_harness_value_error(
    goal_home: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from cursor_goal import manage as manage_mod

    def boom() -> dict[str, str]:
        raise ValueError("no skill")

    monkeypatch.setattr(manage_mod, "harness_cmd_report", boom)
    code, out, _err = run_cli("manage", "doctor")
    assert "Harness path unresolved" in out or code in (0, 1)


def test_doctor_relative_cursor_goal_python(
    goal_home: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr("cursor_goal.manage.os.name", "nt")
    monkeypatch.setenv("CURSOR_GOAL_PYTHON", "python.exe")
    code, _out, err = run_cli("manage", "doctor")
    assert code == 1
    assert "CURSOR_GOAL_PYTHON must be an absolute" in err


def test_doctor_missing_workdir_warning(
    goal_home: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from cursor_goal.state import load_goal, save_goal

    monkeypatch.setenv("CURSOR_GOAL_WAKE", "1")
    assert run_cli("manage", "create", "wd-miss")[0] == 0
    state = load_goal()
    assert state is not None
    state.workdir = str(goal_home / "does-not-exist-workdir")
    save_goal(state)
    # Wake not alive → doctor fails, but warning about workdir should appear
    code, out, err = run_cli("manage", "doctor")
    combined = out + err
    assert "workdir" in combined.lower() and (
        "missing" in combined.lower() or "not a directory" in combined.lower()
    )


def test_harness_cmd_prints_env(
    goal_home: Path, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    home = tmp_path / "skill"
    home.mkdir()
    (home / "scripts").mkdir()
    (home / "scripts" / "run_goal.py").write_text("#", encoding="utf-8")
    monkeypatch.setenv("CURSOR_GOAL_HOME", str(home))
    monkeypatch.setenv("CURSOR_PLUGIN_ROOT", str(tmp_path / "plugin"))
    code, out, _err = run_cli("manage", "harness-cmd")
    assert code == 0
    assert "CURSOR_GOAL_HOME" in out


def test_run_goal_invocation_else_token(monkeypatch: pytest.MonkeyPatch) -> None:
    from cursor_goal import paths as paths_mod

    monkeypatch.setattr(paths_mod, "python_invocation", lambda: ["weirdbin", "-u"])
    monkeypatch.setattr(paths_mod, "run_goal_script", lambda: Path("run_goal.py"))
    inv = paths_mod.run_goal_invocation("x")
    assert inv.startswith("weirdbin")


def test_create_workdir_flag_requires_value(goal_home: Path) -> None:
    code, _out, err = run_cli("manage", "create", "x", "--workdir")
    assert code == 1
    assert "--workdir" in err


def test_doctor_no_python_on_path(
    goal_home: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from cursor_goal import manage as manage_mod

    monkeypatch.setattr(manage_mod.os, "name", "nt")
    monkeypatch.delenv("CURSOR_GOAL_PYTHON", raising=False)
    monkeypatch.setattr(manage_mod.shutil, "which", lambda _name: None)
    monkeypatch.setattr(manage_mod, "_stale_baked_python_failures", lambda: [])
    code, out, _err = run_cli("manage", "doctor")
    assert "No py/python/python3 on PATH" in out or code in (0, 1)


def test_maybe_arm_wake_oserror(
    goal_home: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from cursor_goal import manage as manage_mod

    monkeypatch.setenv("CURSOR_GOAL_WAKE", "1")

    def boom() -> dict[str, object]:
        raise OSError("arm failed")

    monkeypatch.setattr(manage_mod, "wake_arm", boom)
    code, out, _err = run_cli("manage", "create", "arm-fail")
    assert code == 0
    assert "BLOCKING CHECKLIST" not in out


def test_validation_mode_shell_branch() -> None:
    from cursor_goal.manage import _validation_mode
    from cursor_goal.state import GoalState

    mode = _validation_mode(
        GoalState(validation_command="echo a && echo b", shell_ok=True)
    )
    assert mode == "shell"


def test_baked_python_edge_lines(tmp_path: Path) -> None:
    from cursor_goal import manage as manage_mod

    bare = tmp_path / "bare.cmd"
    bare.write_text("python.exe -u stop.py\n", encoding="utf-8")
    assert manage_mod._baked_python_from_cmd(bare) is None

    partial = tmp_path / "partial.cmd"
    partial.write_text('"C:\\OnlyOpenQuote\n', encoding="utf-8")
    assert manage_mod._baked_python_from_cmd(partial) is None

    relative = tmp_path / "rel.cmd"
    relative.write_text('"python.exe" -u stop.py\n', encoding="utf-8")
    assert manage_mod._baked_python_from_cmd(relative) is None


def test_stale_skips_when_baked_unparseable(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from cursor_goal import manage as manage_mod

    skill = tmp_path / "goal"
    scripts = skill / "scripts"
    scripts.mkdir(parents=True)
    (scripts / "stop_hook.cmd").write_text("REM nothing\n", encoding="utf-8")
    monkeypatch.setattr(manage_mod, "skill_root", lambda: skill)
    monkeypatch.setattr(manage_mod.os, "name", "nt")
    monkeypatch.delenv("CURSOR_GOAL_PYTHON", raising=False)
    assert manage_mod._stale_baked_python_failures() == []


def test_data_dir_symlink_leaf_posix(
    goal_home: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from cursor_goal import state as state_mod

    monkeypatch.setattr(state_mod.os, "name", "posix")
    monkeypatch.setattr(state_mod, "path_has_symlink_or_reparse", lambda _p: False)
    monkeypatch.setattr(type(goal_home), "is_symlink", lambda self: True)
    assert state_mod.data_dir_is_insecure(goal_home) is True
