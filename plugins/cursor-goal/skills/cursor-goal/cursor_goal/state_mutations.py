"""Locked goal.json field updates and parse-result persistence.

Imports :mod:`cursor_goal.state` lazily inside each function to avoid a
circular import (state re-exports these helpers).
"""

# pylint: disable=import-outside-toplevel,protected-access

from __future__ import annotations

from typing import Any

from cursor_goal.goal_schema import (
    _UPDATABLE_FIELDS,
    GoalState,
    _apply_field,
    _clamp_field_chars,
)
from cursor_goal.logging_config import get_logger

logger = get_logger("cursor_goal.state")


def update_goal_fields(**fields: Any) -> GoalState | None:
    from cursor_goal import state as state_mod  # isort: skip

    with state_mod.goal_lock():
        state = state_mod.load_goal()
        if state is None:
            return None
        for key, value in fields.items():
            if key not in _UPDATABLE_FIELDS:
                logger.debug("Ignoring unknown goal field update: %s", key)
                continue
            _apply_field(state, key, value)
        state_mod._save_goal_unlocked(state)
        return state


def create_goal_atomic(
    state: GoalState,
    *,
    force: bool = False,
) -> tuple[GoalState | None, str]:
    """Create (or overwrite) a goal and clear eval/audit signals under one lock.

    Returns ``(state, status)`` where status is ``ok`` or ``exists``.
    Any existing ``goal.json`` blocks create unless *force* is True.
    """
    from cursor_goal import state as state_mod  # isort: skip

    with state_mod.goal_lock():
        existing = state_mod.load_goal()
        if existing is not None and not force:
            return existing, "exists"
        state_mod._clear_eval_signal_unlocked()
        state_mod._clear_audit_signal_unlocked()
        state_mod._save_goal_unlocked(state)
        return state, "ok"


def record_parse_result(verdict: str, reason: str) -> GoalState | None:
    """Persist eval verdict/reason and YES signal under one lock.

    Returns the updated goal, or None if no goal was present.
    Raises ``ValueError`` when a YES verdict is recorded for a non-pursuing goal.
    A non-YES verdict also clears the remaining-work CLEAR signal.
    """
    from cursor_goal import state as state_mod  # isort: skip

    with state_mod.goal_lock():
        state = state_mod.load_goal()
        if state is None:
            return None
        if verdict == "YES" and state.status != "pursuing":
            raise ValueError(
                f"Cannot record YES while goal status is '{state.status}' "
                "(must be pursuing)"
            )
        state.last_reason = _clamp_field_chars("last_reason", str(reason or ""))
        state.last_eval_verdict = _clamp_field_chars(
            "last_eval_verdict", str(verdict or "")
        )
        if verdict == "YES":
            state_mod._write_eval_signal_unlocked(state, reason=reason)
        else:
            state_mod._clear_eval_signal_unlocked()
            state_mod._clear_audit_signal_unlocked()
        state_mod._save_goal_unlocked(state)
        return state


def record_parse_audit(
    verdict: str,
    reason: str,
    *,
    confirm: bool = False,
    response_text: str = "",
) -> GoalState | None:
    """Persist remaining-work audit verdict and CLEAR signal under one lock.

    Returns the updated goal, or None if no goal was present.
    Raises ``ValueError`` when CLEAR is recorded for a non-pursuing goal,
    when confirm CLEAR lacks a primary CLEAR, or when confirm copies the
    primary response hash.
    A non-CLEAR verdict also clears the YES evaluator signal and both
    remaining-work flags.
    """
    from cursor_goal import state as state_mod  # isort: skip

    with state_mod.goal_lock():
        state = state_mod.load_goal()
        if state is None:
            return None
        if verdict == "CLEAR" and state.status != "pursuing":
            raise ValueError(
                f"Cannot record CLEAR while goal status is '{state.status}' "
                "(must be pursuing)"
            )
        state.last_reason = _clamp_field_chars("last_reason", str(reason or ""))
        state.last_audit_verdict = _clamp_field_chars(
            "last_audit_verdict", str(verdict or "")
        )
        if verdict == "CLEAR":
            if confirm:
                state_mod._write_audit_confirm_signal_unlocked(
                    state, reason=reason, response_text=response_text
                )
            else:
                state_mod._clear_audit_confirm_signal_unlocked()
                state_mod._write_audit_signal_unlocked(
                    state, reason=reason, response_text=response_text
                )
        else:
            state_mod._clear_audit_signal_unlocked()
            state_mod._clear_eval_signal_unlocked()
        state_mod._save_goal_unlocked(state)
        logger.info(
            "record_parse_audit verdict=%s confirm=%s response_chars=%s",
            verdict,
            confirm,
            len(response_text or ""),
        )
        return state
