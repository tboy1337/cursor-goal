"""Tests for cursor_goal.parse."""

from __future__ import annotations

import json

import pytest

from cursor_goal.parse import parse_raw
from tests.conftest import run_cli


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


def test_parse_validation_hint_truncates_shell_chains() -> None:
    result = parse_raw("ship it, verified by npm test && npm run lint")
    assert result["test_cmd"] == "npm test"
    assert "&&" not in (result["test_cmd"] or "")


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
