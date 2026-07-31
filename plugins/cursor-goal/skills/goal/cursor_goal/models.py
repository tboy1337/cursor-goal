"""Evaluator model resolution for Cursor Task spawn config."""

from __future__ import annotations

import os

from cursor_goal.logging_config import get_logger

logger = get_logger("cursor_goal.models")

DEFAULT_EVAL_MODEL = "fast"
EVAL_MODEL_ENV = "CURSOR_GOAL_EVAL_MODEL"
EVAL_SUBAGENT_TYPE = "goal-evaluator"


def resolve_eval_model() -> str:
    """Return the evaluator model slug for Task spawn.

    Reads ``CURSOR_GOAL_EVAL_MODEL``. Empty/whitespace values fall back to
    :data:`DEFAULT_EVAL_MODEL` (``fast``).
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
    "resolve_eval_model",
    "spawn_config_dict",
]
