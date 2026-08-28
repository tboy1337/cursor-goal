"""Tests for cursor_goal.evaluate."""

from __future__ import annotations

import io
import json
import sys
from contextlib import redirect_stdout
from pathlib import Path

import pytest
from hypothesis import given, settings
from hypothesis import strategies as st

from cursor_goal.evaluate import (
    MISSING_AUDIT_CLEAR,
    MISSING_AUDIT_CONFIRM,
    MISSING_VALIDATION_EVIDENCE,
    _emit_prompt,
    parse_audit_text,
    parse_result_text,
    validation_evidence_missing,
)
from cursor_goal.state import GoalState, load_goal, save_goal
from tests.conftest import explored_clear_text, run_cli, write_explored_tree


def test_eval_prompt_active_goal(goal_home: Path) -> None:
    run_cli("manage", "create", "all tests pass")
    code, out, _err = run_cli("eval", "prompt")
    assert code == 0
    assert "all tests pass" in out
    assert "<untrusted_condition>" in out
    assert "Keep the full original condition" in out


def test_eval_prompt_redacts_secretish_condition(goal_home: Path) -> None:
    run_cli("manage", "create", "deploy with api_key=supersecret123")
    code, out, _err = run_cli("eval", "prompt")
    assert code == 0
    assert "supersecret123" not in out
    assert "redacted" in out
    assert "<untrusted_condition>" in out


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


def test_eval_prompt_corrupt_goal(goal_home: Path) -> None:
    (goal_home / "goal.json").write_text("{not-json", encoding="utf-8")
    code, _out, err = run_cli("eval", "prompt")
    assert code == 1
    assert "corrupt" in err.lower() or "Error" in err


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
    assert "has not been run yet." in out
    assert MISSING_VALIDATION_EVIDENCE in out
    assert "You MUST answer NO" in out
    assert "Work summary is not a substitute" in out


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
    assert MISSING_VALIDATION_EVIDENCE not in out
    assert "has not been run yet." not in out


def test_eval_prompt_empty_output_with_exit_counts_as_run(goal_home: Path) -> None:
    run_cli("manage", "create", "g", "--test", "echo")
    state = GoalState(
        active=True,
        condition="g",
        validation_command="echo",
        created_at="t",
        turn_budget=20,
        turns_used=1,
        status="pursuing",
        last_validation_output="",
        last_validation_exit_code=0,
    )
    save_goal(state)
    code, out, _err = run_cli("eval", "prompt")
    assert code == 0
    assert "Exit code: 0" in out
    assert "passed" in out
    assert MISSING_VALIDATION_EVIDENCE not in out
    assert "has not been run yet." not in out


def test_eval_prompt_no_validation_command_skips_missing_evidence(
    goal_home: Path,
) -> None:
    run_cli("manage", "create", "g")
    code, out, _err = run_cli("eval", "prompt")
    assert code == 0
    assert "No validation command configured" in out
    assert MISSING_VALIDATION_EVIDENCE not in out


def test_validation_evidence_missing_helper() -> None:
    unset = GoalState(validation_command="pytest", last_validation_exit_code=None)
    ran = GoalState(
        validation_command="pytest",
        last_validation_output="",
        last_validation_exit_code=0,
    )
    none = GoalState(validation_command="", last_validation_exit_code=None)
    assert validation_evidence_missing(unset) is True
    assert validation_evidence_missing(ran) is False
    assert validation_evidence_missing(none) is False


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


def test_eval_validate_clears_clear_and_yes(goal_home: Path) -> None:
    cmd = f'{sys.executable} -c "print(1)"'
    run_cli("manage", "create", "g", "--test", cmd)
    assert run_cli("eval", "parse-result", "YES: ready")[0] == 0
    assert run_cli("eval", "parse-audit", "CLEAR: nothing remains")[0] == 0
    assert (goal_home / "goal-eval-done").is_file()
    assert (goal_home / "goal-audit-clear").is_file()
    assert run_cli("eval", "validate")[0] == 0
    assert not (goal_home / "goal-eval-done").exists()
    assert not (goal_home / "goal-audit-clear").exists()
    _code, prompt, _err = run_cli("eval", "prompt")
    assert "Remaining-work audit: not CLEAR" in prompt
    assert MISSING_AUDIT_CLEAR in prompt


def test_eval_validate_clears_confirm_flag(goal_home: Path, tmp_path: Path) -> None:
    work = tmp_path / "proj"
    files = write_explored_tree(work)
    cmd = f'{sys.executable} -c "print(1)"'
    assert (
        run_cli(
            "manage",
            "create",
            "production audit",
            "--test",
            cmd,
            "--workdir",
            str(work),
        )[0]
        == 0
    )
    primary = explored_clear_text(files, root=work, reason="primary")
    confirm = explored_clear_text(files, root=work, reason="confirm")
    assert run_cli("eval", "parse-result", "YES: ready")[0] == 0
    assert run_cli("eval", "parse-audit", primary)[0] == 0
    assert run_cli("eval", "parse-audit", "--confirm", confirm)[0] == 0
    assert (goal_home / "goal-audit-confirm").is_file()
    assert run_cli("eval", "validate")[0] == 0
    assert not (goal_home / "goal-audit-clear").exists()
    assert not (goal_home / "goal-audit-confirm").exists()
    assert not (goal_home / "goal-eval-done").exists()


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
    assert data["model"] == "composer-2.5"
    assert data["readonly"] is True


def test_eval_spawn_config_override(
    goal_home: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("CURSOR_GOAL_EVAL_MODEL", "gpt-5.3-codex")
    code, out, _err = run_cli("eval", "spawn-config")
    assert code == 0
    data = json.loads(out.strip())
    assert data["model"] == "gpt-5.3-codex"
    assert data["subagent_type"] == "goal-evaluator"


def test_eval_spawn_config_empty_env_falls_back(
    goal_home: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("CURSOR_GOAL_EVAL_MODEL", "   ")
    code, out, _err = run_cli("eval", "spawn-config")
    assert code == 0
    data = json.loads(out.strip())
    assert data["model"] == "composer-2.5"


def test_eval_spawn_config_legacy_fast_falls_back(
    goal_home: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """CURSOR_GOAL_EVAL_MODEL=fast is not a real Cursor model; fall back silently."""
    monkeypatch.setenv("CURSOR_GOAL_EVAL_MODEL", "fast")
    code, out, _err = run_cli("eval", "spawn-config")
    assert code == 0
    data = json.loads(out.strip())
    assert data["model"] == "composer-2.5"


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


def test_eval_parse_result_yes_rejects_paused(goal_home: Path) -> None:
    assert run_cli("manage", "create", "paused-yes")[0] == 0
    assert run_cli("manage", "pause")[0] == 0
    code, _out, err = run_cli("eval", "parse-result", "YES: done")
    assert code == 1
    assert "pursuing" in err.lower()


def test_eval_validate_redacts_live_stdout(
    goal_home: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from cursor_goal import evaluate as eval_mod
    from cursor_goal.validation import ValidationResult

    run_cli("manage", "create", "g", "--test", f'{sys.executable} -c "pass"')

    def fake_run(
        _cmd: str,
        *,
        shell_ok: bool = False,
        cwd: str | None = None,
        timeout_sec: float | None = None,
    ) -> ValidationResult:
        del shell_ok, cwd, timeout_sec
        return ValidationResult(
            exit_code=0,
            output="api_key=sk-secretvalue1234567890\n",
            timed_out=False,
        )

    monkeypatch.setattr(eval_mod, "run_validation", fake_run)
    code, out, _err = run_cli("eval", "validate")
    assert code == 0
    assert "sk-secretvalue1234567890" not in out
    assert "<redacted>" in out


def test_eval_prompt_refuses_dead_wake(
    goal_home: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("CURSOR_GOAL_WAKE", "1")
    monkeypatch.setenv("CURSOR_GOAL_REQUIRE_WAKE", "1")
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
    monkeypatch.setenv("CURSOR_GOAL_REQUIRE_WAKE", "1")
    monkeypatch.delenv("CURSOR_GOAL_ALLOW_DEAD_WAKE", raising=False)
    assert run_cli("manage", "create", "need spawn")[0] == 0
    code, _out, err = run_cli("eval", "spawn-config")
    assert code == 1
    assert "wake" in err.lower()


def test_eval_prompt_skips_wake_gate_when_native(
    goal_home: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("CURSOR_GOAL_WAKE", "1")
    monkeypatch.setenv("CURSOR_GOAL_REQUIRE_WAKE", "1")
    monkeypatch.delenv("CURSOR_GOAL_ALLOW_DEAD_WAKE", raising=False)
    assert run_cli("manage", "create", "native eval", "--native")[0] == 0
    code, _out, err = run_cli("eval", "prompt")
    assert code == 0
    assert "wake" not in err.lower()


def test_eval_prompt_warns_but_continues_by_default(
    goal_home: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("CURSOR_GOAL_WAKE", "1")
    monkeypatch.delenv("CURSOR_GOAL_REQUIRE_WAKE", raising=False)
    monkeypatch.delenv("CURSOR_GOAL_ALLOW_DEAD_WAKE", raising=False)
    assert run_cli("manage", "create", "need eval warn")[0] == 0
    code, _out, err = run_cli("eval", "prompt")
    assert code == 0
    assert "wake" in err.lower()


def test_eval_spawn_config_warns_but_continues_by_default(
    goal_home: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("CURSOR_GOAL_WAKE", "1")
    monkeypatch.delenv("CURSOR_GOAL_REQUIRE_WAKE", raising=False)
    monkeypatch.delenv("CURSOR_GOAL_ALLOW_DEAD_WAKE", raising=False)
    assert run_cli("manage", "create", "need spawn warn")[0] == 0
    code, out, err = run_cli("eval", "spawn-config")
    assert code == 0
    assert "wake" in err.lower()
    data = json.loads(out.strip())
    assert data["subagent_type"] == "goal-evaluator"


def test_eval_prompt_rejects_validation_as_sufficient(goal_home: Path) -> None:
    run_cli("manage", "create", "production audit")
    code, out, _err = run_cli("eval", "prompt")
    assert code == 0
    assert "strong evidence" not in out.lower()
    assert "NOT sufficient" in out
    assert MISSING_AUDIT_CLEAR in out
    assert "Remaining-work audit: not CLEAR" in out


def test_eval_prompt_shows_clear_audit(goal_home: Path) -> None:
    run_cli("manage", "create", "all tests pass")
    assert run_cli("eval", "parse-audit", "CLEAR: nothing remains")[0] == 0
    code, out, _err = run_cli("eval", "prompt")
    assert code == 0
    assert "Remaining-work audit: CLEAR this cycle" in out
    assert MISSING_AUDIT_CLEAR not in out
    assert MISSING_AUDIT_CONFIRM not in out


def test_eval_prompt_stale_audit_when_fingerprint_drifts(goal_home: Path) -> None:
    run_cli("manage", "create", "all tests pass")
    assert run_cli("eval", "parse-audit", "CLEAR: nothing remains")[0] == 0
    flag = goal_home / "goal-audit-clear"
    raw = json.loads(flag.read_text(encoding="utf-8"))
    raw["tree_fingerprint"] = "not-the-current-tree"
    flag.write_text(json.dumps(raw), encoding="utf-8")
    code, out, _err = run_cli("eval", "prompt")
    assert code == 0
    assert "Remaining-work audit: not CLEAR" in out
    assert "tree changed" in out.lower()
    assert MISSING_AUDIT_CLEAR in out


def test_eval_prompt_missing_fingerprint_is_stale(goal_home: Path) -> None:
    run_cli("manage", "create", "all tests pass")
    assert run_cli("eval", "parse-audit", "CLEAR: nothing remains")[0] == 0
    flag = goal_home / "goal-audit-clear"
    raw = json.loads(flag.read_text(encoding="utf-8"))
    del raw["tree_fingerprint"]
    flag.write_text(json.dumps(raw), encoding="utf-8")
    code, out, _err = run_cli("eval", "prompt")
    assert code == 0
    assert "Remaining-work audit: not CLEAR" in out
    assert MISSING_AUDIT_CLEAR in out


def test_eval_audit_prompt_has_no_work_summary(goal_home: Path) -> None:
    run_cli("manage", "create", "production audit")
    code, out, _err = run_cli("eval", "audit-prompt", "--work-summary", "did X")
    assert code == 0
    assert "did X" not in out
    assert "production audit" in out
    assert "<untrusted_condition>" in out
    assert "inspect" in out.lower()
    assert "CHANGELOG" in out
    assert "explore" in out.lower()
    assert "EXPLORED" in out
    assert "CLEAR:" in out
    assert "REMAINING:" in out
    assert "CONFIRM-PASS" not in out


def test_eval_audit_prompt_narrow_does_not_require_explore(goal_home: Path) -> None:
    run_cli("manage", "create", "all tests pass")
    code, out, _err = run_cli("eval", "audit-prompt")
    assert code == 0
    assert "tests pass" in out.lower()
    assert "CONFIRM-PASS" not in out
    assert "Task explore" not in out
    assert "invent extra hardening" in out


def test_eval_audit_spawn_config(goal_home: Path) -> None:
    code, out, _err = run_cli("eval", "audit-spawn-config")
    assert code == 0
    data = json.loads(out.strip())
    assert data["subagent_type"] == "goal-auditor"
    assert data["model"] == "inherit"
    assert data["readonly"] is True


def test_parse_audit_text_last_line_wins() -> None:
    verdict, reason = parse_audit_text("CLEAR: nope\nREMAINING: src/foo.py still leaks")
    assert verdict == "REMAINING"
    assert "src/foo.py" in reason
    verdict2, reason2 = parse_audit_text(
        "Notes\nREMAINING: maybe\nCLEAR: in-scope work is done"
    )
    assert verdict2 == "CLEAR"
    assert "in-scope" in reason2


def test_parse_audit_unclear_without_verdict() -> None:
    verdict, _reason = parse_audit_text("looks fine overall")
    assert verdict == "UNCLEAR"


def test_eval_parse_audit_clear_auto_signals(goal_home: Path) -> None:
    run_cli("manage", "create", "test condition")
    code, out, _err = run_cli("eval", "parse-audit", "CLEAR: nothing in-scope remains")
    assert code == 0
    assert "VERDICT=CLEAR" in out
    assert "CLEAR remaining-work" in out
    assert (goal_home / "goal-audit-clear").is_file()
    state = load_goal()
    assert state is not None
    assert state.last_audit_verdict == "CLEAR"
    _code, status_out, _err = run_cli("manage", "status")
    assert "Last audit: CLEAR" in status_out


def test_eval_parse_audit_remaining_clears_yes(goal_home: Path) -> None:
    run_cli("manage", "create", "test condition")
    run_cli("eval", "parse-result", "YES: looks done")
    run_cli("eval", "parse-audit", "CLEAR: first pass")
    assert (goal_home / "goal-eval-done").is_file()
    assert (goal_home / "goal-audit-clear").is_file()
    code, out, _err = run_cli(
        "eval", "parse-audit", "REMAINING: src/foo.py rooted C:foo path"
    )
    assert code == 1
    assert "VERDICT=REMAINING" in out
    assert not (goal_home / "goal-audit-clear").exists()
    assert not (goal_home / "goal-eval-done").exists()


def test_eval_parse_result_no_clears_audit(goal_home: Path) -> None:
    run_cli("manage", "create", "test condition")
    run_cli("eval", "parse-audit", "CLEAR: ok")
    assert (goal_home / "goal-audit-clear").is_file()
    code, out, _err = run_cli("eval", "parse-result", "NO: tests still fail")
    assert code == 1
    assert "VERDICT=NO" in out
    assert not (goal_home / "goal-audit-clear").exists()


def test_eval_parse_audit_clear_rejects_paused(goal_home: Path) -> None:
    assert run_cli("manage", "create", "paused-clear")[0] == 0
    assert run_cli("manage", "pause")[0] == 0
    code, _out, err = run_cli("eval", "parse-audit", "CLEAR: done")
    assert code == 1
    assert "pursuing" in err.lower()


def test_eval_help_lists_audit_commands(goal_home: Path) -> None:
    code, out, _err = run_cli("eval", "help")
    assert code == 0
    assert "audit-prompt" in out
    assert "parse-audit" in out
    assert "audit-spawn-config" in out


def test_eval_audit_prompt_no_goal(goal_home: Path) -> None:
    code, _out, err = run_cli("eval", "audit-prompt")
    assert code == 1
    assert "No active goal" in err


def test_eval_audit_prompt_corrupt_goal(goal_home: Path) -> None:
    (goal_home / "goal.json").write_text("{not-json", encoding="utf-8")
    code, _out, err = run_cli("eval", "audit-prompt")
    assert code == 1
    assert "corrupt" in err.lower() or "Error" in err


def test_eval_audit_prompt_refuses_dead_wake(
    goal_home: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("CURSOR_GOAL_WAKE", "1")
    monkeypatch.setenv("CURSOR_GOAL_REQUIRE_WAKE", "1")
    monkeypatch.delenv("CURSOR_GOAL_ALLOW_DEAD_WAKE", raising=False)
    assert run_cli("manage", "create", "need audit")[0] == 0
    code, _out, err = run_cli("eval", "audit-prompt")
    assert code == 1
    assert "wake" in err.lower()


def test_eval_audit_spawn_config_refuses_dead_wake(
    goal_home: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("CURSOR_GOAL_WAKE", "1")
    monkeypatch.setenv("CURSOR_GOAL_REQUIRE_WAKE", "1")
    monkeypatch.delenv("CURSOR_GOAL_ALLOW_DEAD_WAKE", raising=False)
    assert run_cli("manage", "create", "need audit spawn")[0] == 0
    code, _out, err = run_cli("eval", "audit-spawn-config")
    assert code == 1
    assert "wake" in err.lower()


def test_eval_audit_prompt_confirm_banner(goal_home: Path) -> None:
    run_cli("manage", "create", "production audit")
    code, out, _err = run_cli(
        "eval", "audit-prompt", "--confirm", "--work-summary", "did X"
    )
    assert code == 0
    assert "CONFIRM-PASS" in out
    assert "did X" not in out
    assert "EXPLORED" in out


def test_eval_help_lists_confirm_flags(goal_home: Path) -> None:
    code, out, _err = run_cli("eval", "help")
    assert code == 0
    assert "--confirm" in out


def test_eval_parse_audit_broad_clear_without_explored_is_unclear(
    goal_home: Path, tmp_path: Path
) -> None:
    work = tmp_path / "proj"
    write_explored_tree(work)
    assert (
        run_cli("manage", "create", "production audit", "--workdir", str(work))[0] == 0
    )
    code, out, _err = run_cli("eval", "parse-audit", "CLEAR: nothing remains")
    assert code == 1
    assert "VERDICT=UNCLEAR" in out
    assert "EXPLORED" in out
    assert not (goal_home / "goal-audit-clear").exists()


def test_eval_parse_audit_broad_clear_fake_paths_rejected(
    goal_home: Path, tmp_path: Path
) -> None:
    work = tmp_path / "proj"
    write_explored_tree(work)
    assert (
        run_cli("manage", "create", "production audit", "--workdir", str(work))[0] == 0
    )
    fake = "\n".join(f"- missing/dir{i}.py" for i in range(6))
    body = f"EXPLORED:\n{fake}\nCLEAR: nothing remains\n"
    code, out, _err = run_cli("eval", "parse-audit", body)
    assert code == 1
    assert "VERDICT=UNCLEAR" in out
    assert not (goal_home / "goal-audit-clear").exists()


def test_eval_parse_audit_broad_clear_with_explored(
    goal_home: Path, tmp_path: Path
) -> None:
    work = tmp_path / "proj"
    files = write_explored_tree(work)
    assert (
        run_cli("manage", "create", "production audit", "--workdir", str(work))[0] == 0
    )
    body = explored_clear_text(files, root=work)
    code, out, _err = run_cli("eval", "parse-audit", body)
    assert code == 0
    assert "VERDICT=CLEAR" in out
    assert (goal_home / "goal-audit-clear").is_file()
    assert not (goal_home / "goal-audit-confirm").exists()
    _code, prompt, _err = run_cli("eval", "prompt")
    assert MISSING_AUDIT_CONFIRM in prompt
    assert "confirm-pass is not CLEAR" in prompt


def test_eval_parse_audit_auto_confirm_second_distinct_clear(
    goal_home: Path, tmp_path: Path
) -> None:
    work = tmp_path / "proj"
    files = write_explored_tree(work)
    assert (
        run_cli("manage", "create", "production audit", "--workdir", str(work))[0] == 0
    )
    primary = explored_clear_text(files, root=work, reason="primary pass")
    confirm = explored_clear_text(files, root=work, reason="confirm pass found nothing")
    assert run_cli("eval", "parse-audit", primary)[0] == 0
    code, out, _err = run_cli("eval", "parse-audit", confirm)
    assert code == 0
    assert "confirm-pass" in out
    assert (goal_home / "goal-audit-confirm").is_file()
    _code, prompt, _err = run_cli("eval", "prompt")
    assert "primary + confirm-pass" in prompt
    assert MISSING_AUDIT_CONFIRM not in prompt


def test_eval_parse_audit_confirm_copy_paste_rejected(
    goal_home: Path, tmp_path: Path
) -> None:
    work = tmp_path / "proj"
    files = write_explored_tree(work)
    assert (
        run_cli("manage", "create", "production audit", "--workdir", str(work))[0] == 0
    )
    body = explored_clear_text(files, root=work, reason="same text")
    assert run_cli("eval", "parse-audit", body)[0] == 0
    code, _out, err = run_cli("eval", "parse-audit", "--confirm", body)
    assert code == 1
    assert "copy-paste" in err.lower()
    assert not (goal_home / "goal-audit-confirm").exists()


def test_eval_parse_audit_confirm_without_primary_rejected(
    goal_home: Path, tmp_path: Path
) -> None:
    work = tmp_path / "proj"
    files = write_explored_tree(work)
    assert (
        run_cli("manage", "create", "production audit", "--workdir", str(work))[0] == 0
    )
    body = explored_clear_text(files, root=work, reason="confirm only")
    code, _out, err = run_cli("eval", "parse-audit", "--confirm", body)
    assert code == 1
    assert "primary" in err.lower()


def test_eval_parse_audit_idempotent_primary_reparse(
    goal_home: Path, tmp_path: Path
) -> None:
    work = tmp_path / "proj"
    files = write_explored_tree(work)
    assert (
        run_cli("manage", "create", "production audit", "--workdir", str(work))[0] == 0
    )
    body = explored_clear_text(files, root=work, reason="primary")
    assert run_cli("eval", "parse-audit", body)[0] == 0
    code, out, _err = run_cli("eval", "parse-audit", body)
    assert code == 0
    assert "already recorded" in out
    assert not (goal_home / "goal-audit-confirm").exists()


def test_eval_parse_audit_broad_one_directory_rejected(
    goal_home: Path, tmp_path: Path
) -> None:
    work = tmp_path / "proj"
    files: list[Path] = []
    for index in range(6):
        path = work / "src" / f"f{index}.py"
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text("x\n", encoding="utf-8")
        files.append(path)
    assert (
        run_cli("manage", "create", "production audit", "--workdir", str(work))[0] == 0
    )
    body = explored_clear_text(files, root=work)
    code, out, _err = run_cli("eval", "parse-audit", body)
    assert code == 1
    assert "VERDICT=UNCLEAR" in out
    assert "directories" in out.lower()


def test_eval_parse_audit_inline_explored_clear(
    goal_home: Path, tmp_path: Path
) -> None:
    work = tmp_path / "proj"
    files = write_explored_tree(work)
    assert (
        run_cli("manage", "create", "production audit", "--workdir", str(work))[0] == 0
    )
    rels = " ".join(path.relative_to(work).as_posix() for path in files)
    body = f"**EXPLORED:** {rels}\nCLEAR: inline cites\n"
    code, out, _err = run_cli("eval", "parse-audit", body)
    assert code == 0
    assert "VERDICT=CLEAR" in out


def test_eval_prompt_confirm_stale_when_fingerprint_drifts(
    goal_home: Path, tmp_path: Path
) -> None:
    work = tmp_path / "proj"
    files = write_explored_tree(work)
    assert (
        run_cli("manage", "create", "production audit", "--workdir", str(work))[0] == 0
    )
    primary = explored_clear_text(files, root=work, reason="primary")
    confirm = explored_clear_text(files, root=work, reason="confirm")
    assert run_cli("eval", "parse-audit", primary)[0] == 0
    assert run_cli("eval", "parse-audit", "--confirm", confirm)[0] == 0
    flag = goal_home / "goal-audit-confirm"
    raw = json.loads(flag.read_text(encoding="utf-8"))
    raw["tree_fingerprint"] = "not-the-current-tree"
    flag.write_text(json.dumps(raw), encoding="utf-8")
    code, out, _err = run_cli("eval", "prompt")
    assert code == 0
    assert "stale" in out.lower()
    assert MISSING_AUDIT_CONFIRM in out


def test_eval_parse_audit_confirm_rejects_stale_primary(
    goal_home: Path, tmp_path: Path
) -> None:
    work = tmp_path / "proj"
    files = write_explored_tree(work)
    assert (
        run_cli("manage", "create", "production audit", "--workdir", str(work))[0] == 0
    )
    primary = explored_clear_text(files, root=work, reason="primary")
    confirm = explored_clear_text(files, root=work, reason="confirm later")
    assert run_cli("eval", "parse-audit", primary)[0] == 0
    flag = goal_home / "goal-audit-clear"
    raw = json.loads(flag.read_text(encoding="utf-8"))
    raw["tree_fingerprint"] = "not-the-current-tree"
    flag.write_text(json.dumps(raw), encoding="utf-8")
    code, _out, err = run_cli("eval", "parse-audit", "--confirm", confirm)
    assert code == 1
    assert "stale" in err.lower()


def test_eval_parse_audit_usage_and_corrupt_goal(goal_home: Path) -> None:
    code, _out, err = run_cli("eval", "parse-audit")
    assert code == 1
    assert "Usage" in err or "Error" in err
    run_cli("manage", "create", "all tests pass")
    (goal_home / "goal.json").write_text("{not-json", encoding="utf-8")
    code2, _out2, err2 = run_cli("eval", "parse-audit", "CLEAR: x")
    assert code2 == 1
    assert "corrupt" in err2.lower() or "Error" in err2


def test_extract_explored_block_helpers() -> None:
    from cursor_goal.evaluate import extract_explored_block

    assert extract_explored_block("looks fine") is None
    inline = extract_explored_block("EXPLORED: src/a.py\nCLEAR: done")
    assert inline is not None
    assert "src/a.py" in inline
    marked = extract_explored_block("**EXPLORED:** docs/e.md\nVERDICT: ignore")
    assert marked is not None
    assert "docs/e.md" in marked


@given(st.text(max_size=400))
@settings(max_examples=40, deadline=None)
def test_parse_result_text_never_raises(text: str) -> None:
    verdict, reason = parse_result_text(text)
    assert verdict in {"YES", "NO", "UNCLEAR"}
    assert isinstance(reason, str)
