"""Cursor stop hook: auto-continuation safety net."""

from __future__ import annotations

import json
import logging
import os
import sys
import time
from typing import Any

from cursor_goal.logging_config import get_logger
from cursor_goal.state import GoalState, data_dir, load_goal, mutate_goal
from cursor_goal.validation import redact_command

logger = get_logger("cursor_goal.stop")

MAX_STDIN_BYTES = 1 * 1024 * 1024
DEFAULT_DRAIN_MS = 100
MAX_DRAIN_MS = 2000
LAST_STOP_RESPONSE_NAME = "last-stop-response.json"


def _drain_ms() -> int:
    """Milliseconds to wait after flush so Cursor can capture stdout."""
    raw = os.environ.get("CURSOR_GOAL_STOP_DRAIN_MS", str(DEFAULT_DRAIN_MS))
    try:
        value = int(raw)
    except ValueError:
        logger.warning(
            "Invalid CURSOR_GOAL_STOP_DRAIN_MS=%r; using %s",
            raw,
            DEFAULT_DRAIN_MS,
        )
        return DEFAULT_DRAIN_MS
    if value < 0:
        return 0
    if value > MAX_DRAIN_MS:
        logger.warning(
            "CURSOR_GOAL_STOP_DRAIN_MS=%s exceeds max %s; clamping "
            "(hook timeout is typically 30s)",
            value,
            MAX_DRAIN_MS,
        )
        return MAX_DRAIN_MS
    return value


def _fsync_stdout() -> None:
    """Best-effort fsync of stdout (may fail for pipes / StringIO)."""
    try:
        fileno = sys.stdout.fileno()
    except (AttributeError, OSError, ValueError):
        return
    try:
        os.fsync(fileno)
    except OSError:
        pass


def _maybe_write_debug_response(payload: dict[str, Any]) -> None:
    """When DEBUG logging is on, persist last stop response for Windows diagnosis."""
    if logger.getEffectiveLevel() > logging.DEBUG:
        return
    try:
        path = data_dir() / LAST_STOP_RESPONSE_NAME
        path.write_text(
            json.dumps(payload, indent=2, ensure_ascii=False) + "\n",
            encoding="utf-8",
        )
    except OSError as exc:
        logger.debug("Could not write %s: %s", LAST_STOP_RESPONSE_NAME, exc)


def emit(payload: dict[str, Any]) -> None:
    """Write a single JSON object to stdout, flush, and drain for Cursor capture."""
    sys.stdout.write(json.dumps(payload, ensure_ascii=False) + "\n")
    sys.stdout.flush()
    _fsync_stdout()
    _maybe_write_debug_response(payload)
    drain = _drain_ms()
    if drain > 0:
        time.sleep(drain / 1000.0)


def emit_empty() -> int:
    emit({})
    return 0


def _budget_limited_response(state: GoalState) -> dict[str, Any]:
    return {
        "followup_message": (
            f"[GOAL BUDGET] Turn limit ({state.turn_budget}) reached. "
            f"Wrap up current work and summarize progress toward: {state.condition}"
        )
    }


def _continue_followup(state: GoalState, remaining: int) -> dict[str, Any]:
    """Build a followup that does not run validation (avoids hook timeouts)."""
    if state.validation_command:
        safe_cmd = redact_command(state.validation_command)
        return {
            "followup_message": (
                f"[GOAL] Turn {state.turns_used}/{state.turn_budget} "
                f"({remaining} remaining). Run validation in-turn if needed "
                f"({safe_cmd}), then evaluate completion via "
                f"subagent. Goal: {state.condition}"
            )
        }
    return {
        "followup_message": (
            f"[GOAL] Turn {state.turns_used}/{state.turn_budget} "
            f"({remaining} remaining). Continue working toward: {state.condition}. "
            "Evaluate completion via subagent when ready."
        )
    }


def handle_stop(payload: dict[str, Any] | None) -> dict[str, Any]:
    """Compute stop-hook response. Never raises — fail open to {}."""
    if not isinstance(payload, dict):
        return {}

    status = payload.get("status", "unknown")
    loop_count = payload.get("loop_count", 0)
    logger.info("stop hook status=%s loop_count=%s", status, loop_count)

    if status != "completed":
        return {}

    def mutator(state: GoalState) -> None:
        if not state.active or state.status != "pursuing":
            raise ValueError("inactive")
        state.turns_used = int(state.turns_used) + 1
        if state.turns_used >= state.turn_budget:
            state.status = "budget-limited"
            state.active = False

    try:
        state = mutate_goal(mutator)
    except ValueError:
        return {}
    except OSError as exc:
        logger.error("Failed to persist stop-hook turn update: %s", exc)
        # Fail-open: still try to continue based on loaded state if possible.
        state = load_goal()
        if state is None or not state.active or state.status != "pursuing":
            return {}
        # Do not invent a turn bump if persist failed; return continue prompt.
        remaining = max(0, state.turn_budget - state.turns_used)
        return _continue_followup(state, remaining)

    if state is None:
        return {}

    if state.status == "budget-limited":
        return _budget_limited_response(state)

    remaining = state.turn_budget - state.turns_used
    return _continue_followup(state, remaining)


def cmd_stop(_argv: list[str] | None = None) -> int:
    """Read Cursor stop JSON from stdin; always exit 0 with a JSON object."""
    try:
        raw = sys.stdin.read(MAX_STDIN_BYTES + 1)
    except OSError as exc:
        logger.error("Failed to read stdin: %s", exc)
        return emit_empty()

    if len(raw) > MAX_STDIN_BYTES:
        logger.error("Stop stdin exceeds %s bytes; fail-open", MAX_STDIN_BYTES)
        return emit_empty()

    if not raw.strip():
        return emit_empty()

    try:
        payload = json.loads(raw)
    except json.JSONDecodeError as exc:
        logger.error("Invalid stop JSON: %s", exc)
        return emit_empty()

    try:
        response = handle_stop(payload if isinstance(payload, dict) else None)
    except Exception as exc:  # noqa: BLE001 — fail-open for stop hook
        logger.error("Unhandled stop error: %s", exc)
        return emit_empty()

    emit(response if isinstance(response, dict) else {})
    return 0
