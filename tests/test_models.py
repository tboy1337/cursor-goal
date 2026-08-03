"""Tests for evaluator model / Task spawn config."""

from __future__ import annotations

import json

import pytest

from cursor_goal.models import (
    DEFAULT_EVAL_MODEL,
    KNOWN_INVALID_EVAL_MODELS,
    eval_model_is_known_invalid,
    resolve_eval_model,
    spawn_config_dict,
)
from tests.conftest import run_cli


def test_default_eval_model_is_a_real_cursor_model() -> None:
    """`fast` is only a bracket parameter, not a valid `model` value.

    See https://cursor.com/docs/subagents.md#model-configuration -- the
    default must be a real model ID (or "inherit") so the checker actually
    runs on a different model than the worker.
    """
    assert DEFAULT_EVAL_MODEL == "composer-2.5"
    assert not eval_model_is_known_invalid(DEFAULT_EVAL_MODEL)


def test_known_invalid_eval_models_include_legacy_fast() -> None:
    assert "fast" in KNOWN_INVALID_EVAL_MODELS
    assert eval_model_is_known_invalid("fast") is True
    assert eval_model_is_known_invalid("FAST") is True
    assert eval_model_is_known_invalid("composer-2.5") is False
    assert eval_model_is_known_invalid("inherit") is False


def test_resolve_eval_model_default(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("CURSOR_GOAL_EVAL_MODEL", raising=False)
    assert resolve_eval_model() == DEFAULT_EVAL_MODEL


def test_resolve_eval_model_empty_falls_back(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("CURSOR_GOAL_EVAL_MODEL", "   ")
    assert resolve_eval_model() == DEFAULT_EVAL_MODEL


def test_resolve_eval_model_override(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("CURSOR_GOAL_EVAL_MODEL", "gpt-5.3-codex")
    assert resolve_eval_model() == "gpt-5.3-codex"


def test_resolve_eval_model_legacy_fast_falls_back(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A legacy `fast` override is invalid; resolve falls back to the default."""
    monkeypatch.setenv("CURSOR_GOAL_EVAL_MODEL", "fast")
    assert resolve_eval_model() == DEFAULT_EVAL_MODEL


def test_spawn_config_dict_shape(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("CURSOR_GOAL_EVAL_MODEL", "gpt-5.3-codex")
    cfg = spawn_config_dict()
    assert cfg == {
        "subagent_type": "goal-evaluator",
        "model": "gpt-5.3-codex",
        "readonly": True,
    }


def test_spawn_config_dict_legacy_fast_falls_back(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("CURSOR_GOAL_EVAL_MODEL", "fast")
    cfg = spawn_config_dict()
    assert cfg["model"] == DEFAULT_EVAL_MODEL


def test_eval_spawn_config_cli(
    goal_home: object, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("CURSOR_GOAL_EVAL_MODEL", "gpt-5.3-codex")
    code, out, _err = run_cli("eval", "spawn-config")
    assert code == 0
    data = json.loads(out)
    assert data["subagent_type"] == "goal-evaluator"
    assert data["readonly"] is True
