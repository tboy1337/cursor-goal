"""Cursor stop hook: auto-continuation safety net."""

from __future__ import annotations

import json
import os
import sys
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import IO, Any

from cursor_goal.logging_config import get_logger
from cursor_goal.state import (
    LAST_STOP_RESPONSE_NAME,
    GoalState,
    atomic_write_text,
    budgets_exhausted,
    data_dir,
    goal_lock,
    mutate_goal,
    refuse_if_acl_harden_failed,
    refuse_if_data_dir_insecure,
    snapshot_goal,
)
from cursor_goal.validation import redact_command, redact_secrets
from cursor_goal.wake import disarm as wake_disarm
from cursor_goal.wake import record_agent_nudge

logger = get_logger("cursor_goal.stop")

MAX_STDIN_BYTES = 1 * 1024 * 1024
DEFAULT_DRAIN_MS = 100
DEFAULT_DRAIN_MS_WINDOWS = 250
MAX_DRAIN_MS = 2000
STOP_SINGLEFLIGHT_NAME = "stop-emit.lock"
_FAIL_OPEN_CONTINUE_NAME = "stop-failopen-continues"
MAX_FAIL_OPEN_CONTINUES = 3
_FOLLOWUP_CONDITION_MARKERS = ("toward:", "Goal:", "progress toward:")


def _default_drain_ms() -> int:
    """Platform default drain before exit so Cursor can capture stdout."""
    if os.name == "nt":
        return DEFAULT_DRAIN_MS_WINDOWS
    return DEFAULT_DRAIN_MS


def _drain_ms() -> int:
    """Milliseconds to wait after flush so Cursor can capture stdout."""
    raw = os.environ.get("CURSOR_GOAL_STOP_DRAIN_MS")
    if raw is None or raw == "":
        return _default_drain_ms()
    try:
        value = int(raw)
    except ValueError:
        default = _default_drain_ms()
        logger.warning(
            "Invalid CURSOR_GOAL_STOP_DRAIN_MS=%r; using %s",
            raw,
            default,
        )
        return default
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


def _redact_followup_for_disk(msg: str) -> str:
    """Strip trailing goal-condition text from followup messages for disk storage."""
    lowered = msg.lower()
    best_idx: int | None = None
    best_len = 0
    for marker in _FOLLOWUP_CONDITION_MARKERS:
        idx = lowered.rfind(marker.lower())
        if idx < 0:
            continue
        if best_idx is None or idx < best_idx:
            best_idx = idx
            best_len = len(marker)
    if best_idx is None:
        return msg
    original_slice = msg[best_idx : best_idx + best_len]
    return msg[:best_idx] + original_slice + " <redacted>"


def _redact_payload_for_disk(payload: dict[str, Any]) -> dict[str, Any]:
    """Copy payload and redact goal-condition text inside followup messages."""
    safe = dict(payload)
    msg = safe.get("followup_message")
    if isinstance(msg, str):
        safe["followup_message"] = _redact_followup_for_disk(msg)
        safe["has_followup"] = bool(msg.strip())
    return safe


def _write_last_stop_response(payload: dict[str, Any]) -> None:
    """Persist last stop response for diagnosis (always on; redacted)."""
    try:
        insecure = refuse_if_data_dir_insecure()
        if insecure is not None:
            logger.warning("Skip last-stop-response write: %s", insecure)
            return
        acl_fail = refuse_if_acl_harden_failed()
        if acl_fail is not None:
            logger.warning("Skip last-stop-response write: %s", acl_fail)
            return
        path = data_dir() / LAST_STOP_RESPONSE_NAME
        envelope = {
            "ts": datetime.now(timezone.utc).isoformat().replace("+00:00", "Z"),
            "pid": os.getpid(),
            "payload": _redact_payload_for_disk(payload),
        }
        atomic_write_text(
            path,
            json.dumps(envelope, indent=2, ensure_ascii=False) + "\n",
        )
    except OSError as exc:
        logger.debug("Could not write %s: %s", LAST_STOP_RESPONSE_NAME, exc)


def emit(payload: dict[str, Any]) -> None:
    """Write a single JSON object to stdout, flush, and drain for Cursor capture."""
    sys.stdout.write(json.dumps(payload, ensure_ascii=False) + "\n")
    sys.stdout.flush()
    _fsync_stdout()
    _write_last_stop_response(payload)
    followup = payload.get("followup_message")
    if isinstance(followup, str) and followup.strip():
        try:
            record_agent_nudge(source="stop")
        except OSError as exc:
            logger.debug("Could not record stop nudge stamp: %s", exc)
    drain = _drain_ms()
    if drain > 0:
        time.sleep(drain / 1000.0)


def emit_empty() -> int:
    emit({})
    return 0


def _try_acquire_singleflight() -> IO[bytes] | None:
    """Non-blocking exclusive lock so dual marketplace hooks emit once."""
    insecure = refuse_if_data_dir_insecure()
    if insecure is not None:
        logger.warning("Stop singleflight refused: %s", insecure)
        return None
    acl_fail = refuse_if_acl_harden_failed()
    if acl_fail is not None:
        logger.warning("Stop singleflight refused: %s", acl_fail)
        return None
    path = data_dir() / STOP_SINGLEFLIGHT_NAME
    path.parent.mkdir(parents=True, exist_ok=True)
    # Lock must stay open until emit finishes (dual marketplace singleflight).
    handle = open(path, "a+b")  # pylint: disable=consider-using-with
    try:
        if sys.platform == "win32":
            import msvcrt  # isort: skip  # pylint: disable=import-outside-toplevel

            handle.seek(0, os.SEEK_END)
            if handle.tell() == 0:
                handle.write(b"\0")
                handle.flush()
            handle.seek(0)
            msvcrt.locking(handle.fileno(), msvcrt.LK_NBLCK, 1)
        else:  # pragma: no cover — exercised on Unix CI
            import fcntl  # isort: skip  # pylint: disable=import-outside-toplevel

            fcntl.flock(handle.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
    except OSError:
        handle.close()
        logger.info("Stop singleflight: another stop hook holds the lock")
        return None
    return handle


def _release_singleflight(handle: IO[bytes] | None) -> None:
    if handle is None:
        return
    try:
        if sys.platform == "win32":
            import msvcrt  # isort: skip  # pylint: disable=import-outside-toplevel

            handle.seek(0)
            msvcrt.locking(handle.fileno(), msvcrt.LK_UNLCK, 1)
        else:  # pragma: no cover — exercised on Unix CI
            import fcntl  # isort: skip  # pylint: disable=import-outside-toplevel

            fcntl.flock(handle.fileno(), fcntl.LOCK_UN)
    except OSError:
        pass
    try:
        handle.close()
    except OSError:
        pass


def _budget_limited_response(state: GoalState) -> dict[str, Any]:
    safe_condition = redact_secrets(state.condition, max_chars=None)
    return {
        "followup_message": (
            f"[GOAL BUDGET] Turn limit ({state.turn_budget}) reached. "
            f"Wrap up current work and summarize progress toward: {safe_condition}"
        )
    }


def _continue_followup(state: GoalState, remaining: int) -> dict[str, Any]:
    """Build a followup that does not run validation (avoids hook timeouts).

    Live Cursor followup keeps a usable (secret-scrubbed) condition. Disk /
    ``last-stop-response.json`` still strips condition text via
    ``_redact_payload_for_disk`` inside ``emit``.
    """
    remaining = max(0, remaining)
    safe_condition = redact_secrets(state.condition, max_chars=None)
    if state.validation_command:
        safe_cmd = redact_command(state.validation_command)
        raw = (
            f"[GOAL] Turn {state.turns_used}/{state.turn_budget} "
            f"({remaining} remaining). Run validation in-turn if needed "
            f"({safe_cmd}), then evaluate completion via "
            f"subagent. Goal: {safe_condition}"
        )
    else:
        raw = (
            f"[GOAL] Turn {state.turns_used}/{state.turn_budget} "
            f"({remaining} remaining). Continue working toward: {safe_condition}. "
            "Evaluate completion via subagent when ready."
        )
    return {"followup_message": raw}


def _fail_open_continue_count_path() -> Path:
    return data_dir() / _FAIL_OPEN_CONTINUE_NAME


def _read_fail_open_continues() -> int:
    path = _fail_open_continue_count_path()
    if not path.is_file():
        return 0
    try:
        return max(0, int(path.read_text(encoding="utf-8").strip() or "0"))
    except (OSError, ValueError):
        return 0


def _write_fail_open_continues(value: int) -> None:
    path = _fail_open_continue_count_path()
    try:
        atomic_write_text(path, f"{value}\n")
    except OSError as exc:
        logger.debug("Could not write fail-open continue counter: %s", exc)


def _clear_fail_open_continues() -> None:
    try:
        _fail_open_continue_count_path().unlink(missing_ok=True)
    except OSError:
        pass


def _bump_fail_open_continues() -> int:
    """Atomically increment fail-open counter under goal.lock. Returns new count."""
    with goal_lock():
        count = _read_fail_open_continues() + 1
        _write_fail_open_continues(count)
        return count


# pylint: disable-next=too-many-return-statements
def handle_stop(
    payload: dict[str, Any] | None,
) -> dict[str, Any]:
    """Compute stop-hook response. Never raises — fail open to {}."""
    if not isinstance(payload, dict):
        return {}

    insecure = refuse_if_data_dir_insecure()
    if insecure is not None:
        logger.warning("Stop refuse insecure data dir: %s", insecure)
        return {}
    acl_fail = refuse_if_acl_harden_failed()
    if acl_fail is not None:
        logger.warning("Stop refuse ACL harden failure: %s", acl_fail)
        return {}

    status = payload.get("status", "unknown")
    loop_count = payload.get("loop_count", 0)
    logger.info("stop hook status=%s loop_count=%s", status, loop_count)

    if status != "completed":
        return {}

    budget_hit = False

    def mutator(state: GoalState) -> None:
        nonlocal budget_hit
        if not state.active or state.status != "pursuing":
            raise ValueError("inactive")
        state.turns_used = int(state.turns_used) + 1
        # Stop path charges turns only; wake budget is enforced in wake.py.
        if budgets_exhausted(
            state.turns_used,
            state.turn_budget,
            state.wake_ticks,
            state.wake_budget,
        ):
            state.status = "budget-limited"
            state.active = False
            budget_hit = True
            if state.turns_used >= state.turn_budget:
                state.last_reason = (
                    f"turn budget exhausted ({state.turns_used}/{state.turn_budget})"
                )
            else:
                state.last_reason = (
                    f"wake budget exhausted ({state.wake_ticks}/{state.wake_budget})"
                )

    try:
        state = mutate_goal(mutator)
        with goal_lock():
            _clear_fail_open_continues()
    except ValueError:
        return {}
    except OSError as exc:
        logger.error("Failed to persist stop-hook turn update: %s", exc)
        # Cap fail-open continuations to avoid unbounded free loops.
        try:
            count = _bump_fail_open_continues()
        except OSError as lock_exc:
            logger.error("Fail-open counter lock failed: %s", lock_exc)
            return {}
        if count > MAX_FAIL_OPEN_CONTINUES:
            logger.error(
                "Stop persist failures exceeded %s; fail-open empty",
                MAX_FAIL_OPEN_CONTINUES,
            )
            return {}
        state = snapshot_goal()
        if state is None or not state.active or state.status != "pursuing":
            return {}
        # Account fail-open continues against turn budget so free loops cannot
        # bypass the budget beyond MAX_FAIL_OPEN_CONTINUES.
        effective_turns = int(state.turns_used) + count
        if effective_turns >= int(state.turn_budget) or budgets_exhausted(
            effective_turns,
            state.turn_budget,
            state.wake_ticks,
            state.wake_budget,
        ):
            logger.warning(
                "Fail-open continue would exhaust budget "
                "(turns_used=%s + failopen=%s >= %s); stopping",
                state.turns_used,
                count,
                state.turn_budget,
            )
            return {}
        remaining = max(0, state.turn_budget - effective_turns)
        return _continue_followup(state, remaining)

    if state is None:
        return {}

    if state.status == "budget-limited" or budget_hit:
        try:
            wake_disarm(kill_loop=True)
        except OSError as exc:
            logger.debug("Could not disarm wake after budget limit: %s", exc)
        return _budget_limited_response(state)

    remaining = max(0, state.turn_budget - state.turns_used)
    return _continue_followup(state, remaining)


def cmd_stop(_argv: list[str] | None = None) -> int:
    """Read Cursor stop JSON from stdin; always exit 0.

    Dual marketplace hooks use singleflight: the lock holder emits JSON; the
    loser exits silently (no stdout, no last-stop-response write) so Cursor
    cannot overwrite a real followup with ``{}``.
    """
    lock = _try_acquire_singleflight()
    if lock is None:
        logger.info("Stop singleflight miss: silent exit (no stdout)")
        return 0
    try:
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

        try:
            emit(response if isinstance(response, dict) else {})
        except OSError as exc:
            logger.error("Stop emit failed (fail-open empty): %s", exc)
            try:
                sys.stdout.write("{}\n")
                sys.stdout.flush()
            except OSError as write_exc:
                logger.error("Stop fail-open stdout write failed: %s", write_exc)
            return 0
        return 0
    finally:
        _release_singleflight(lock)
