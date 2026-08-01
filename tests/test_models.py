"""Tests for evaluator model / Task spawn config."""

from __future__ import annotations

import json

import pytest

from cursor_goal.models import (
    DEFAULT_EVAL_MODEL,
    resolve_eval_model,
    spawn_config_dict,
)
from tests.conftest import run_cli


def test_resolve_eval_model_default(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("CURSOR_GOAL_EVAL_MODEL", raising=False)
    assert resolve_eval_model() == DEFAULT_EVAL_MODEL


def test_resolve_eval_model_empty_falls_back(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("CURSOR_GOAL_EVAL_MODEL", "   ")
    assert resolve_eval_model() == DEFAULT_EVAL_MODEL


def test_resolve_eval_model_override(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("CURSOR_GOAL_EVAL_MODEL", "composer-2.5")
    assert resolve_eval_model() == "composer-2.5"


def test_spawn_config_dict_shape(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("CURSOR_GOAL_EVAL_MODEL", "fast")
    cfg = spawn_config_dict()
    assert cfg == {
        "subagent_type": "goal-evaluator",
        "model": "fast",
        "readonly": True,
    }


def test_eval_spawn_config_cli(
    goal_home: object, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("CURSOR_GOAL_EVAL_MODEL", "fast")
    code, out, _err = run_cli("eval", "spawn-config")
    assert code == 0
    data = json.loads(out)
    assert data["subagent_type"] == "goal-evaluator"
    assert data["readonly"] is True
