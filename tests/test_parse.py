"""Tests for cursor_goal.parse."""

from __future__ import annotations

import json

import pytest

from cursor_goal.parse import parse_raw
from tests.conftest import run_cli


def test_parse_cli_joins_unquoted_argv() -> None:
    code, out, _err = run_cli("parse", "fix", "the", "login", "bug")
    assert code == 0
    data = json.loads(out.strip())
    assert data["condition"] == "fix the login bug"


def test_parse_rejects_budget_over_max() -> None:
    with pytest.raises(ValueError, match="500"):
        parse_raw("ship it --budget 9999")


def test_parse_simple_condition() -> None:
    result = parse_raw("fix the login bug")
    assert result["action"] == "create"
    assert result["condition"] == "fix the login bug"
    assert result["test_cmd"] is None
    assert result["budget"] == 20


def test_parse_validation_hint() -> None:
    result = parse_raw("all tests pass, verified by npm test")
    assert result["condition"] == "all tests pass"
    assert result["test_cmd"] == "npm test"


def test_parse_validation_hint_refuses_shell_chains() -> None:
    result = parse_raw("ship it, verified by npm test && npm run lint")
    assert result["test_cmd"] is None
    assert "warning" in result
    assert "shell chain" in result["warning"].lower()
    assert "truncation" in result["warning"].lower()


def test_parse_explicit_test_keeps_shell_chains() -> None:
    result = parse_raw('ship it --test "npm test && npm run lint"')
    assert result["test_cmd"] == "npm test && npm run lint"
    assert "warning" not in result


def test_truncate_shell_chain_helper() -> None:
    from cursor_goal.parse import _truncate_shell_chain

    assert _truncate_shell_chain("npm test && npm run lint") == "npm test"
    assert _truncate_shell_chain("a | b") == "a"
    assert _truncate_shell_chain("plain") == "plain"


def test_parse_budget_hint() -> None:
    result = parse_raw("fix bugs, stop after 10 turns")
    assert result["condition"] == "fix bugs"
    assert result["budget"] == 10


def test_parse_full_combination() -> None:
    result = parse_raw("all tests pass, verified by pytest, stop after 15 turns")
    assert result["condition"] == "all tests pass"
    assert result["test_cmd"] == "pytest"
    assert result["budget"] == 15


def test_parse_explicit_flags() -> None:
    result = parse_raw('"all tests pass" --test "npm test" --budget 30')
    assert result["condition"] == "all tests pass"
    assert result["test_cmd"] == "npm test"
    assert result["budget"] == 30


@pytest.mark.parametrize(
    ("raw", "action"),
    [
        ("status", "status"),
        ("pause", "pause"),
        ("resume", "resume"),
        ("clear", "clear"),
        ("stop", "clear"),
        ("off", "clear"),
    ],
)
def test_parse_subcommands(raw: str, action: str) -> None:
    result = parse_raw(raw)
    assert result["subcommand"] == raw
    assert result["action"] == action


def test_parse_empty_input() -> None:
    with pytest.raises(ValueError):
        parse_raw("")
    code, _out, err = run_cli("parse", "")
    assert code == 1
    assert "Error" in err


def test_parse_cli_json_stdout() -> None:
    code, out, _err = run_cli("parse", "ship it, stop after 5 turns")
    assert code == 0
    payload = json.loads(out)
    assert payload["budget"] == 5
    assert payload["condition"] == "ship it"


def test_parse_quoted_condition_safe() -> None:
    result = parse_raw('say "hello" works')
    assert "hello" in result["condition"]
    code, out, _err = run_cli("parse", 'say "hello" works')
    assert code == 0
    json.loads(out)  # must be valid JSON


def test_parse_bare_test_flag() -> None:
    result = parse_raw("fix it --test pytest")
    assert result["test_cmd"] == "pytest"
    assert result["condition"] == "fix it"


def test_parse_budget_flag() -> None:
    result = parse_raw("ship --budget 12")
    assert result["budget"] == 12
    assert result["condition"] == "ship"


def test_parse_budget_flag_wins_over_nl() -> None:
    result = parse_raw("ship it --budget 30, stop after 10 turns")
    assert result["budget"] == 30
    assert "stop after" not in result["condition"].lower()


def test_parse_rejects_zero_budget() -> None:
    with pytest.raises(ValueError, match="positive integer"):
        parse_raw("ship --budget 0")


def test_parse_strips_goal_prefix() -> None:
    result = parse_raw("/goal fix the bug")
    assert result["condition"] == "fix the bug"


def test_parse_rejects_empty_condition_after_flags() -> None:
    with pytest.raises(ValueError, match="Could not extract"):
        parse_raw('--test "npm test"')


def test_parse_cli_missing_arg() -> None:
    code, _out, err = run_cli("parse")
    assert code == 1
    assert "Usage" in err


def test_parse_allow_shell_not_in_condition() -> None:
    result = parse_raw('compound check --test "npm test && npm run lint" --allow-shell')
    assert result["condition"] == "compound check"
    assert result["test_cmd"] == "npm test && npm run lint"
    assert result["allow_shell"] is True
    assert "--allow-shell" not in result["condition"]


def test_parse_deny_shell_flag() -> None:
    result = parse_raw("ship it --deny-shell --test pytest")
    assert result["condition"] == "ship it"
    assert result["allow_shell"] is False
    assert "--deny-shell" not in result["condition"]


def test_parse_force_flag() -> None:
    result = parse_raw('ship it --test "pytest -q" --force')
    assert result["condition"] == "ship it"
    assert result["force"] is True
    assert "--force" not in result["condition"]


def test_parse_wake_budget_flag() -> None:
    result = parse_raw("ship it --wake-budget 50 --budget 5")
    assert result["condition"] == "ship it"
    assert result["wake_budget"] == 50
    assert result["budget"] == 5
    assert "--wake-budget" not in result["condition"]


def test_parse_rejects_invalid_wake_budget() -> None:
    with pytest.raises(ValueError, match="Wake budget"):
        parse_raw("ship --wake-budget 0")
    with pytest.raises(ValueError, match="Wake budget"):
        parse_raw("ship --wake-budget 9999")


def test_parse_workdir_quoted_and_bare() -> None:
    quoted = parse_raw('ship --workdir "/tmp/my project" --test pytest')
    assert quoted["workdir"] == "/tmp/my project"
    assert quoted["condition"] == "ship"
    bare = parse_raw("ship --workdir /tmp/proj --test pytest")
    assert bare["workdir"] == "/tmp/proj"
    assert bare["condition"] == "ship"


def test_parse_quoted_condition_with_flags() -> None:
    result = parse_raw('/goal "quoted cond" --test "pytest -q" --force')
    assert result["condition"] == "quoted cond"
    assert result["test_cmd"] == "pytest -q"
    assert result["force"] is True
    assert "--force" not in result["condition"]
    assert '"' not in result["condition"]


def test_parse_omits_unset_optional_flags() -> None:
    result = parse_raw("fix the login bug")
    assert "allow_shell" not in result
    assert "force" not in result
    assert "wake_budget" not in result
    assert "workdir" not in result
