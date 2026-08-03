"""Tests for cursor_goal.evaluate."""

from __future__ import annotations

import io
import json
import sys
from contextlib import redirect_stdout
from pathlib import Path

import pytest

from cursor_goal.evaluate import _emit_prompt, parse_result_text
from cursor_goal.state import GoalState, load_goal, save_goal
from tests.conftest import run_cli


def test_eval_prompt_active_goal(goal_home: Path) -> None:
    run_cli("manage", "create", "all tests pass")
    code, out, _err = run_cli("eval", "prompt")
    assert code == 0
    assert "Goal condition: all tests pass" in out


def test_eval_prompt_work_summary(goal_home: Path) -> None:
    run_cli("manage", "create", "fix the login bug")
    code, out, _err = run_cli("eval", "prompt", "--work-summary", "did X")
    assert code == 0
    assert "did X" in out


def test_eval_prompt_ignores_incomplete_work_summary_flag(goal_home: Path) -> None:
    """Cover the argv else-branch when --work-summary has no value."""
    run_cli("manage", "create", "fix the login bug")
    code, out, _err = run_cli("eval", "prompt", "--work-summary")
    assert code == 0
    assert "No work summary provided" in out


def test_emit_prompt_adds_trailing_newline() -> None:
    buf = io.StringIO()
    with redirect_stdout(buf):
        _emit_prompt("hello")
    assert buf.getvalue() == "hello\n"


def test_emit_prompt_keeps_existing_newline() -> None:
    buf = io.StringIO()
    with redirect_stdout(buf):
        _emit_prompt("hello\n")
    assert buf.getvalue() == "hello\n"


def test_eval_signal_requires_yes_or_force(goal_home: Path) -> None:
    run_cli("manage", "create", "test condition")
    assert run_cli("eval", "check")[0] == 1
    assert run_cli("eval", "signal")[0] == 1
    assert run_cli("eval", "signal", "--force")[0] == 0
    assert (goal_home / "goal-eval-done").is_file()
    raw = json.loads((goal_home / "goal-eval-done").read_text(encoding="utf-8"))
    assert raw["verdict"] == "YES"
    assert "condition_hash" in raw
    assert run_cli("eval", "check")[0] == 0


def test_eval_parse_result_yes_auto_signals(goal_home: Path) -> None:
    run_cli("manage", "create", "test condition")
    code, out, _err = run_cli("eval", "parse-result", "YES: all done")
    assert code == 0
    assert "VERDICT=YES" in out
    assert "YES signal recorded" in out
    assert run_cli("eval", "check")[0] == 0
    state = load_goal()
    assert state is not None
    assert state.last_eval_verdict == "YES"


def test_eval_signal_requires_goal(goal_home: Path) -> None:
    code, _out, err = run_cli("eval", "signal")
    assert code == 1
    assert "No active goal" in err


def test_eval_parse_result_no_clears_signal(goal_home: Path) -> None:
    run_cli("manage", "create", "test condition")
    run_cli("eval", "parse-result", "YES: done")
    assert run_cli("eval", "check")[0] == 0
    code, out, _err = run_cli("eval", "parse-result", "NO: 2 tests failing")
    assert code == 1
    assert "VERDICT=NO" in out
    assert run_cli("eval", "check")[0] == 1


def test_eval_parse_result_empty(goal_home: Path) -> None:
    run_cli("manage", "create", "test condition")
    code, _out, err = run_cli("eval", "parse-result", "")
    assert code == 1
    assert "Usage" in err


def test_eval_prompt_no_goal(goal_home: Path) -> None:
    code, _out, err = run_cli("eval", "prompt")
    assert code == 1
    assert "No active goal" in err


def test_parse_result_rejects_loose_substring() -> None:
    verdict, _reason = parse_result_text("I cannot say YES yet")
    assert verdict == "UNCLEAR"
    verdict2, reason2 = parse_result_text("NO: not ready\nextra")
    assert verdict2 == "NO"
    assert "not ready" in reason2


def test_parse_result_last_line_wins() -> None:
    verdict, reason = parse_result_text("YES: shipped\nNO: ignore")
    assert verdict == "NO"
    assert "ignore" in reason
    verdict2, reason2 = parse_result_text(
        "Analysis says YES: maybe\nMore prose\nYES: confirmed"
    )
    assert verdict2 == "YES"
    assert "confirmed" in reason2


def test_parse_result_ignores_mid_prose_yes() -> None:
    verdict, _reason = parse_result_text(
        "The answer would be YES: if tests passed, but they did not.\nStill working"
    )
    assert verdict == "UNCLEAR"


def test_eval_help_and_unknown(goal_home: Path) -> None:
    assert run_cli("eval")[0] == 1
    assert run_cli("eval", "help")[0] == 0
    code, out, err = run_cli("eval", "nope")
    assert code == 1
    assert "Usage:" in out
    assert "validate" in out
    assert "unknown eval command" in err


def test_eval_parse_result_stdin(goal_home: Path) -> None:
    from tests.conftest import run_cli_stdin

    run_cli("manage", "create", "g")
    code, out, _err = run_cli_stdin(
        "YES: evidence looks good\n",
        "eval",
        "parse-result",
        "--stdin",
    )
    assert code == 0
    assert "VERDICT=YES" in out
    assert (goal_home / "goal-eval-done").is_file()


def test_eval_parse_result_at_file(goal_home: Path) -> None:
    run_cli("manage", "create", "g")
    path = goal_home / "verdict.txt"
    path.write_text("NO: more work\n", encoding="utf-8")
    code, out, _err = run_cli("eval", "parse-result", f"@{path}")
    assert code == 1
    assert "VERDICT=NO" in out


def test_eval_prompt_redacts_secrets(goal_home: Path) -> None:
    run_cli(
        "manage",
        "create",
        "g",
        "--test",
        "tool --token=supersecret",
    )
    code, out, _err = run_cli("eval", "prompt")
    assert code == 0
    assert "supersecret" not in out
    assert "<redacted>" in out


def test_eval_prompt_redacts_validation_output_secrets(goal_home: Path) -> None:
    run_cli("manage", "create", "g", "--test", "echo")
    state = GoalState(
        active=True,
        condition="g",
        validation_command="echo",
        created_at="t",
        turn_budget=20,
        turns_used=1,
        status="pursuing",
        last_validation_output="token=supersecret-output\nall green",
        last_validation_exit_code=0,
    )
    save_goal(state)
    code, out, _err = run_cli("eval", "prompt")
    assert code == 0
    assert "supersecret-output" not in out
    assert "<redacted>" in out
    assert "all green" in out


def test_eval_help_lists_spawn_config(goal_home: Path) -> None:
    code, out, _err = run_cli("eval", "help")
    assert code == 0
    assert "spawn-config" in out
    assert "--stdin" in out


def test_eval_prompt_validation_not_run(goal_home: Path) -> None:
    run_cli("manage", "create", "g", "--test", "pytest")
    code, out, _err = run_cli("eval", "prompt")
    assert code == 0
    assert "has not been run yet" in out


def test_eval_prompt_with_validation_output(goal_home: Path) -> None:
    run_cli("manage", "create", "g", "--test", "echo")
    state = GoalState(
        active=True,
        condition="g",
        validation_command="echo",
        created_at="t",
        turn_budget=20,
        turns_used=1,
        status="pursuing",
        last_validation_output="all green",
        last_validation_exit_code=0,
    )
    save_goal(state)
    code, out, _err = run_cli("eval", "prompt")
    assert code == 0
    assert "all green" in out
    assert "Validation command: echo" in out
    assert "Exit code: 0" in out
    assert "passed" in out


def test_eval_parse_result_without_goal(goal_home: Path) -> None:
    code, out, _err = run_cli("eval", "parse-result", "YES: done")
    assert code == 0
    assert "VERDICT=YES" in out
    assert not (goal_home / "goal-eval-done").exists()


def test_eval_validate_persists_output(goal_home: Path) -> None:
    cmd = f'{sys.executable} -c "print(4242)"'
    run_cli("manage", "create", "g", "--test", cmd)
    code, out, _err = run_cli("eval", "validate")
    assert code == 0
    assert "4242" in out
    state = load_goal()
    assert state is not None
    assert "4242" in state.last_validation_output
    assert state.last_validation_exit_code == 0
    _code2, prompt, _err2 = run_cli("eval", "prompt")
    assert "4242" in prompt
    assert "Exit code: 0" in prompt


def test_eval_validate_requires_command(goal_home: Path) -> None:
    run_cli("manage", "create", "g")
    code, _out, err = run_cli("eval", "validate")
    assert code == 1
    assert "No validation command" in err


def test_eval_validate_nonzero(goal_home: Path) -> None:
    cmd = f'{sys.executable} -c "raise SystemExit(3)"'
    run_cli("manage", "create", "g", "--test", cmd)
    code, _out, _err = run_cli("eval", "validate")
    assert code == 1
    state = load_goal()
    assert state is not None
    assert state.last_validation_exit_code == 3


def test_eval_validate_uses_workdir(goal_home: Path, tmp_path: Path) -> None:
    work = tmp_path / "proj"
    work.mkdir()
    marker = work / "marker.txt"
    # Write a small script that fails unless cwd has marker.txt
    script = work / "check_cwd.py"
    script.write_text(
        "from pathlib import Path\n"
        "import sys\n"
        "sys.exit(0 if Path('marker.txt').is_file() else 2)\n",
        encoding="utf-8",
    )
    marker.write_text("ok", encoding="utf-8")
    cmd = f"{sys.executable} check_cwd.py"
    run_cli("manage", "create", "wd", "--test", cmd, "--workdir", str(work))
    code, _out, _err = run_cli("eval", "validate")
    assert code == 0


def test_eval_validate_missing_workdir_fails(goal_home: Path, tmp_path: Path) -> None:
    from cursor_goal.state import GoalState, save_goal

    missing = tmp_path / "gone-workdir"
    cmd = f'{sys.executable} -c "raise SystemExit(0)"'
    run_cli("manage", "create", "g", "--test", cmd)
    state = load_goal()
    assert state is not None
    state.workdir = str(missing)
    save_goal(state)
    code, _out, err = run_cli("eval", "validate")
    assert code == 1
    assert "workdir" in err.lower()


def test_eval_prompt_failed_exit_note(goal_home: Path) -> None:
    state = GoalState(
        active=True,
        condition="g",
        validation_command="false",
        created_at="t",
        status="pursuing",
        last_validation_output="boom",
        last_validation_exit_code=1,
    )
    save_goal(state)
    code, out, _err = run_cli("eval", "prompt")
    assert code == 0
    assert "Exit code: 1" in out
    assert "failed" in out
    assert "maker" in out.lower() or "checker" in out.lower()


def test_eval_spawn_config_default(
    goal_home: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.delenv("CURSOR_GOAL_EVAL_MODEL", raising=False)
    code, out, _err = run_cli("eval", "spawn-config")
    assert code == 0
    data = json.loads(out.strip())
    assert data["subagent_type"] == "goal-evaluator"
    assert data["model"] == "fast"
    assert data["readonly"] is True


def test_eval_spawn_config_override(
    goal_home: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("CURSOR_GOAL_EVAL_MODEL", "composer-2.5")
    code, out, _err = run_cli("eval", "spawn-config")
    assert code == 0
    data = json.loads(out.strip())
    assert data["model"] == "composer-2.5"
    assert data["subagent_type"] == "goal-evaluator"


def test_eval_spawn_config_empty_env_falls_back(
    goal_home: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("CURSOR_GOAL_EVAL_MODEL", "   ")
    code, out, _err = run_cli("eval", "spawn-config")
    assert code == 0
    data = json.loads(out.strip())
    assert data["model"] == "fast"


def test_eval_check_no_signal_and_ok(goal_home: Path) -> None:
    run_cli("manage", "create", "check coverage")
    code, out, _err = run_cli("eval", "check")
    assert code == 1
    assert "FAIL" in out
    assert "evaluator signal" in out.lower() or "Evaluator" in out
    run_cli("eval", "parse-result", "YES: evidence complete")
    code2, out2, _err2 = run_cli("eval", "check")
    assert code2 == 0
    assert "OK" in out2


def test_eval_prompt_refuses_dead_wake(
    goal_home: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("CURSOR_GOAL_WAKE", "1")
    monkeypatch.delenv("CURSOR_GOAL_ALLOW_DEAD_WAKE", raising=False)
    assert run_cli("manage", "create", "need eval")[0] == 0
    # Create with wake=1 arms wake.json but no live loop PID → not ready.
    code, _out, err = run_cli("eval", "prompt")
    assert code == 1
    assert "wake" in err.lower()


def test_eval_spawn_config_refuses_dead_wake(
    goal_home: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("CURSOR_GOAL_WAKE", "1")
    monkeypatch.delenv("CURSOR_GOAL_ALLOW_DEAD_WAKE", raising=False)
    assert run_cli("manage", "create", "need spawn")[0] == 0
    code, _out, err = run_cli("eval", "spawn-config")
    assert code == 1
    assert "wake" in err.lower()
