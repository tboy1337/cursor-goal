"""Evaluator model resolution for Cursor Task spawn config."""

from __future__ import annotations

import os

from cursor_goal.logging_config import get_logger

logger = get_logger("cursor_goal.models")

# Cursor's subagent `model` field only honors "inherit" or a real model ID
# (https://cursor.com/docs/subagents.md#model-configuration); unrecognized
# values silently fall back to a Cursor-chosen model. "composer-2.5" is a
# real model ID in the included Cursor Models pool, so it stays both valid
# and cheap as the evaluator default.
DEFAULT_EVAL_MODEL = "composer-2.5"
EVAL_MODEL_ENV = "CURSOR_GOAL_EVAL_MODEL"
EVAL_SUBAGENT_TYPE = "goal-evaluator"

# Legacy/invalid values that look like a model but are not honored by Cursor's
# subagent `model` field today. "fast" in particular is only a bracket option
# on a real model ID (e.g. "composer-2.5[fast=false]"), never a model ID by
# itself, so setting model="fast" silently falls back to a different model.
KNOWN_INVALID_EVAL_MODELS = frozenset({"fast", "slow", "default", "auto"})


def eval_model_is_known_invalid(model: str) -> bool:
    """True when *model* is a known-invalid Cursor subagent ``model`` value."""
    return model.strip().lower() in KNOWN_INVALID_EVAL_MODELS


def resolve_eval_model() -> str:
    """Return the evaluator model slug for Task spawn.

    Reads ``CURSOR_GOAL_EVAL_MODEL``. Empty/whitespace values, and known-invalid
    legacy values (e.g. ``fast``, which Cursor does not accept as a subagent
    model ID), fall back to :data:`DEFAULT_EVAL_MODEL`.
    """
    raw = os.environ.get(EVAL_MODEL_ENV)
    if raw is None:
        logger.debug("eval model default=%s (env unset)", DEFAULT_EVAL_MODEL)
        return DEFAULT_EVAL_MODEL
    model = raw.strip()
    if not model:
        logger.warning(
            "Invalid %s=%r (empty); using default %s",
            EVAL_MODEL_ENV,
            raw,
            DEFAULT_EVAL_MODEL,
        )
        return DEFAULT_EVAL_MODEL
    if eval_model_is_known_invalid(model):
        logger.warning(
            '%s=%r is not a valid Cursor subagent model (only "inherit" or a '
            "real model ID is honored); using default %s instead. "
            "See https://cursor.com/docs/subagents.md#model-configuration",
            EVAL_MODEL_ENV,
            raw,
            DEFAULT_EVAL_MODEL,
        )
        return DEFAULT_EVAL_MODEL
    logger.info("eval model resolved=%s from %s", model, EVAL_MODEL_ENV)
    return model


def spawn_config_dict() -> dict[str, str | bool]:
    """Build Task parameters for the readonly goal evaluator."""
    model = resolve_eval_model()
    return {
        "subagent_type": EVAL_SUBAGENT_TYPE,
        "model": model,
        "readonly": True,
    }


__all__ = [
    "DEFAULT_EVAL_MODEL",
    "EVAL_MODEL_ENV",
    "EVAL_SUBAGENT_TYPE",
    "KNOWN_INVALID_EVAL_MODELS",
    "eval_model_is_known_invalid",
    "resolve_eval_model",
    "spawn_config_dict",
]
