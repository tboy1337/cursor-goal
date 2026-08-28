"""Cursor stop hook: auto-continuation safety net."""

from __future__ import annotations

import hashlib
import json
import os
import sys
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import IO, Any

from cursor_goal.logging_config import get_logger
from cursor_goal.models import AUDIT_SUBAGENT_TYPE, EVAL_SUBAGENT_TYPE
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
    take_condition_updated_pending,
)
from cursor_goal.validation import (
    BUDGET_WRAPUP_RULE,
    NO_AGENT_PAUSE_RULE,
    condition_prompt_block,
    is_broad_condition,
    redact_command,
)
from cursor_goal.wake import disarm as wake_disarm
from cursor_goal.wake import record_agent_nudge

logger = get_logger("cursor_goal.stop")


def _now_iso_micros() -> str:
    """Microsecond-precision timestamp for on-disk diagnostic envelopes.

    Distinct from :func:`cursor_goal.state.now_iso` (second precision, used
    for user-facing ``goal.json`` fields): dedupe/last-response envelopes
    benefit from finer resolution when debugging rapid successive hook
    invocations.
    """
    return datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")


MAX_STDIN_BYTES = 1 * 1024 * 1024
DEFAULT_DRAIN_MS = 100
DEFAULT_DRAIN_MS_WINDOWS = 250
MAX_DRAIN_MS = 2000
STOP_SINGLEFLIGHT_NAME = "stop-emit.lock"
SUBAGENT_STOP_SINGLEFLIGHT_NAME = "subagent-stop-emit.lock"
STOP_DEDUPE_NAME = "stop-generation.json"
LAST_SUBAGENT_STOP_RESPONSE_NAME = "last-subagent-stop-response.json"
_FAIL_OPEN_CONTINUE_NAME = "stop-failopen-continues"
MAX_FAIL_OPEN_CONTINUES = 3
_FOLLOWUP_CONDITION_MARKERS = (
    "<untrusted_condition>",
    "toward:",
    "Goal:",
    "progress toward:",
)


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


def _refuse_if_data_dir_unsafe() -> str | None:
    """Combined insecure-dir / Windows-ACL-harden-failure gate.

    Every stop/subagentStop write path needs both checks before touching
    the data dir; keep the two-step preamble in one place instead of
    repeating it at each call site.
    """
    insecure = refuse_if_data_dir_insecure()
    if insecure is not None:
        return insecure
    return refuse_if_acl_harden_failed()


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
        unsafe = _refuse_if_data_dir_unsafe()
        if unsafe is not None:
            logger.warning("Skip last-stop-response write: %s", unsafe)
            return
        path = data_dir() / LAST_STOP_RESPONSE_NAME
        envelope = {
            "ts": _now_iso_micros(),
            "pid": os.getpid(),
            "payload": _redact_payload_for_disk(payload),
        }
        atomic_write_text(
            path,
            json.dumps(envelope, indent=2, ensure_ascii=False) + "\n",
        )
    except OSError as exc:
        logger.debug("Could not write %s: %s", LAST_STOP_RESPONSE_NAME, exc)


def _stop_dedupe_path() -> Path:
    return data_dir() / STOP_DEDUPE_NAME


def _read_stop_dedupe() -> dict[str, Any] | None:
    path = _stop_dedupe_path()
    if not path.is_file():
        return None
    try:
        data = json.loads(path.read_text(encoding="utf-8-sig"))
    except (OSError, json.JSONDecodeError):
        return None
    return data if isinstance(data, dict) else None


def _write_stop_dedupe(generation_id: str, response: dict[str, Any]) -> None:
    payload = {
        "generation_id": generation_id,
        "response": response,
        "ts": _now_iso_micros(),
    }
    try:
        atomic_write_text(
            _stop_dedupe_path(), json.dumps(payload, ensure_ascii=False) + "\n"
        )
    except OSError as exc:
        logger.debug("Could not persist stop dedupe stamp: %s", exc)


def _cached_stop_response_for(generation_id: str) -> dict[str, Any] | None:
    """Return the cached response for *generation_id*, or None if unseen.

    Guards against *sequential* dual marketplace stop hooks: the singleflight
    lock only prevents concurrent double-processing, but two hook entries
    that run one after another (each acquiring the now-free lock in turn)
    would otherwise both mutate ``turns_used`` and both emit a
    ``followup_message`` for the exact same Cursor turn. Cursor's stop
    payload carries a per-turn ``generation_id``; once one process has fully
    handled it, a repeat is answered from the cached response with no further
    goal-state mutation.
    """
    if not generation_id:
        return None
    with goal_lock():
        dedupe = _read_stop_dedupe()
    if dedupe is None or str(dedupe.get("generation_id") or "") != generation_id:
        return None
    response = dedupe.get("response")
    return response if isinstance(response, dict) else {}


def _remember_stop_response(generation_id: str, response: dict[str, Any]) -> None:
    if not generation_id:
        return
    with goal_lock():
        _write_stop_dedupe(generation_id, response)


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


def _write_last_subagent_stop_response(payload: dict[str, Any]) -> None:
    """Persist last subagentStop response for diagnosis (always on; redacted)."""
    try:
        unsafe = _refuse_if_data_dir_unsafe()
        if unsafe is not None:
            logger.warning("Skip last-subagent-stop-response write: %s", unsafe)
            return
        path = data_dir() / LAST_SUBAGENT_STOP_RESPONSE_NAME
        envelope = {
            "ts": _now_iso_micros(),
            "pid": os.getpid(),
            "payload": _redact_payload_for_disk(payload),
        }
        atomic_write_text(
            path,
            json.dumps(envelope, indent=2, ensure_ascii=False) + "\n",
        )
    except OSError as exc:
        logger.debug("Could not write %s: %s", LAST_SUBAGENT_STOP_RESPONSE_NAME, exc)


def emit_subagent_stop(payload: dict[str, Any]) -> None:
    """Write a subagentStop JSON response to stdout, flush, and drain."""
    sys.stdout.write(json.dumps(payload, ensure_ascii=False) + "\n")
    sys.stdout.flush()
    _fsync_stdout()
    _write_last_subagent_stop_response(payload)
    followup = payload.get("followup_message")
    if isinstance(followup, str) and followup.strip():
        try:
            record_agent_nudge(source="subagent_stop")
        except OSError as exc:
            logger.debug("Could not record subagent-stop nudge stamp: %s", exc)
    drain = _drain_ms()
    if drain > 0:
        time.sleep(drain / 1000.0)


def _try_acquire_singleflight(
    lock_name: str = STOP_SINGLEFLIGHT_NAME,
) -> IO[bytes] | None:
    """Non-blocking exclusive lock so dual marketplace hooks emit once.

    Caller must already refuse insecure/ACL paths (those emit ``{}``). A
    lock miss returns None for silent exit so a dual-hook loser cannot
    overwrite a real followup with empty JSON. ``stop`` and ``subagentStop``
    events use separate lock files (*lock_name*) so one event type can never
    block the other.
    """
    path = data_dir() / lock_name
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


def _condition_followup_block(state: GoalState) -> str:
    """Untrusted condition + fidelity for live followups; consume update flag."""
    updated = bool(state.condition_updated_pending)
    if updated:
        take_condition_updated_pending()
    return condition_prompt_block(state.condition, objective_updated=updated)


def _budget_limited_response(state: GoalState) -> dict[str, Any]:
    """Build the budget-exhausted followup, naming whichever budget tripped.

    Turn and wake budgets are independent counters (see
    ``budgets_exhausted``); always saying "Turn limit" even when the *wake*
    budget was the one that tripped is misleading to whoever reads the
    followup.
    """
    if state.turns_used >= state.turn_budget:
        budget_label = f"Turn limit ({state.turn_budget})"
    else:
        budget_label = f"Wake tick limit ({state.wake_budget})"
    block = _condition_followup_block(state)
    return {
        "followup_message": (
            f"[GOAL BUDGET] {budget_label} reached. {BUDGET_WRAPUP_RULE}\n" f"{block}"
        )
    }


def _continue_followup(state: GoalState, remaining: int) -> dict[str, Any]:
    """Build a followup that does not run validation (avoids hook timeouts).

    Live Cursor followup keeps a usable (secret-scrubbed) condition. Disk /
    ``last-stop-response.json`` still strips condition text via
    ``_redact_payload_for_disk`` inside ``emit``.
    """
    remaining = max(0, remaining)
    block = _condition_followup_block(state)
    broad_note = ""
    if is_broad_condition(state.condition):
        broad_note = (
            " Broad condition: after a primary CLEAR, spawn a new "
            "goal-auditor with `eval audit-prompt --confirm` and "
            "`eval parse-audit --confirm`."
        )
    if state.validation_command:
        safe_cmd = redact_command(state.validation_command)
        raw = (
            f"[GOAL] Turn {state.turns_used}/{state.turn_budget} "
            f"({remaining} remaining). Status is still pursuing — an earlier "
            '"this is complete" message is invalid. Run `manage status` and '
            f"continue. Run validation in-turn if needed "
            f"({safe_cmd}), then remaining-work audit, then evaluate. "
            f"{NO_AGENT_PAUSE_RULE}{broad_note} {block}"
        )
    else:
        raw = (
            f"[GOAL] Turn {state.turns_used}/{state.turn_budget} "
            f"({remaining} remaining). Status is still pursuing — an earlier "
            '"this is complete" message is invalid. Run `manage status` and '
            "continue working toward the full original condition. "
            "Then remaining-work audit, then evaluate via subagent. "
            f"{NO_AGENT_PAUSE_RULE}{broad_note} {block}"
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


def handle_subagent_stop(payload: dict[str, Any] | None) -> dict[str, Any]:
    """Compute subagentStop-hook response. Never raises — fail open to {}.

    Documented (https://cursor.com/docs/hooks.md), race-free continuation
    point: the instant a goal-evaluator or goal-auditor subagent finishes,
    nudge the worker to parse its verdict — *without* ever calling
    ``manage done`` itself. Defensive ``subagent_type`` check backs up the
    installer-side ``matcher`` so no other subagent type can ever trigger
    this followup, even if a hooks.json is hand-edited to drop the matcher.
    """
    if not isinstance(payload, dict):
        return {}
    subagent_type = payload.get("subagent_type")
    if subagent_type not in {EVAL_SUBAGENT_TYPE, AUDIT_SUBAGENT_TYPE}:
        return {}
    if payload.get("status") != "completed":
        return {}

    unsafe = _refuse_if_data_dir_unsafe()
    if unsafe is not None:
        logger.warning("Subagent-stop refuse data dir unsafe: %s", unsafe)
        return {}

    state = snapshot_goal()
    if state is None or not state.active or state.status != "pursuing":
        return {}
    block = _condition_followup_block(state)
    if subagent_type == AUDIT_SUBAGENT_TYPE:
        message = (
            "[GOAL] The remaining-work auditor finished. Run `eval parse-audit` "
            "on its response now — REMAINING: implement that list; CLEAR: spawn "
            "goal-evaluator. Status is still pursuing. "
            f"{NO_AGENT_PAUSE_RULE} {block}"
        )
    else:
        message = (
            "[GOAL] The evaluator subagent finished. Run `eval parse-result` on "
            "its response now — YES: `manage done` only if a CLEAR audit signal "
            "also exists; NO: continue working toward the full original "
            "condition. Status is still pursuing; an earlier "
            '"this is complete" message is invalid. '
            f"{NO_AGENT_PAUSE_RULE} {block}"
        )
    return {"followup_message": message}


def _handle_stop_persist_failure(exc: OSError) -> dict[str, Any]:
    """Best-effort continuation when persisting the turn-count mutation fails.

    Caps fail-open continuations against ``MAX_FAIL_OPEN_CONTINUES`` and the
    turn/wake budgets so a persistently failing data dir cannot grant an
    unbounded number of free turns.
    """
    logger.error("Failed to persist stop-hook turn update: %s", exc)
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


def handle_stop(
    payload: dict[str, Any] | None,
) -> dict[str, Any]:
    """Compute stop-hook response. Never raises — fail open to {}."""
    if not isinstance(payload, dict):
        return {}

    unsafe = _refuse_if_data_dir_unsafe()
    if unsafe is not None:
        logger.warning("Stop refuse data dir unsafe: %s", unsafe)
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
        return _handle_stop_persist_failure(exc)

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
    """Read Cursor stop/subagentStop JSON from stdin; always exit 0.

    The same launcher is registered for both the ``stop`` and
    ``subagentStop`` (matcher: goal-evaluator) hook events, so this reads one
    payload and dispatches on shape: only ``subagentStop`` payloads carry
    ``subagent_type``. Each event uses its own singleflight lock file so one
    event type can never block the other.

    Dual marketplace hooks use singleflight: the lock holder emits JSON; the
    loser exits silently (no stdout, no last-response write) so Cursor
    cannot overwrite a real followup with ``{}``. A ``generation_id``-keyed
    dedupe stamp (falling back to a SHA-256 of status/loop_count/event/
    conversation_id when ``generation_id`` is omitted) additionally guards
    *sequential* dual hooks (one hook fully finishes, then the other starts)
    from re-charging ``turns_used`` or emitting a second followup for the
    same Cursor turn.

    Insecure/ACL refuse paths emit ``{}`` (fail-open), distinct from a
    singleflight lock miss.
    """
    unsafe = _refuse_if_data_dir_unsafe()
    if unsafe is not None:
        logger.warning("Stop refused (fail-open {}): %s", unsafe)
        return emit_empty()

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

    payload_dict = payload if isinstance(payload, dict) else None
    is_subagent_event = (
        isinstance(payload_dict, dict) and "subagent_type" in payload_dict
    )

    if is_subagent_event:
        return _handle_subagent_stop_invocation(payload_dict)
    return _handle_stop_invocation(payload_dict)


def _handle_subagent_stop_invocation(payload_dict: dict[str, Any] | None) -> int:
    lock = _try_acquire_singleflight(SUBAGENT_STOP_SINGLEFLIGHT_NAME)
    if lock is None:
        logger.info("Subagent-stop singleflight miss: silent exit (no stdout)")
        return 0
    try:
        try:
            response = handle_subagent_stop(payload_dict)
        except Exception as exc:  # noqa: BLE001 — fail-open for subagentStop hook
            logger.error("Unhandled subagent-stop error: %s", exc)
            response = {}
        try:
            emit_subagent_stop(response if isinstance(response, dict) else {})
        except OSError as exc:
            logger.error("Subagent-stop emit failed (fail-open empty): %s", exc)
            try:
                sys.stdout.write("{}\n")
                sys.stdout.flush()
            except OSError as write_exc:
                logger.error(
                    "Subagent-stop fail-open stdout write failed: %s", write_exc
                )
        return 0
    finally:
        _release_singleflight(lock)


def _stop_generation_id(payload_dict: dict[str, Any] | None) -> str:
    if payload_dict is None:
        return ""
    raw_gen = payload_dict.get("generation_id")
    return raw_gen.strip() if isinstance(raw_gen, str) else ""


def _stop_payload_fallback_key(payload_dict: dict[str, Any]) -> str:
    """Stable key when Cursor omits ``generation_id`` (sequential dual hooks)."""
    canonical = {
        "conversation_id": payload_dict.get("conversation_id"),
        "hook_event_name": payload_dict.get("hook_event_name"),
        "loop_count": payload_dict.get("loop_count"),
        "status": payload_dict.get("status"),
    }
    blob = json.dumps(canonical, sort_keys=True, default=str, separators=(",", ":"))
    digest = hashlib.sha256(blob.encode("utf-8")).hexdigest()
    return f"payload:{digest}"


def _stop_dedupe_key(payload_dict: dict[str, Any] | None) -> str:
    """Return generation_id, or a payload hash when generation_id is absent."""
    generation_id = _stop_generation_id(payload_dict)
    if generation_id:
        return generation_id
    if not isinstance(payload_dict, dict):
        return ""
    return _stop_payload_fallback_key(payload_dict)


def _handle_stop_invocation(payload_dict: dict[str, Any] | None) -> int:
    lock = _try_acquire_singleflight(STOP_SINGLEFLIGHT_NAME)
    if lock is None:
        logger.info("Stop singleflight miss: silent exit (no stdout)")
        return 0
    try:
        generation_id = _stop_dedupe_key(payload_dict)
        cached = _cached_stop_response_for(generation_id) if generation_id else None
        if cached is not None:
            logger.info(
                "Stop dedupe hit for generation_id=%s...; re-emitting cached "
                "response without re-charging turns_used",
                generation_id[:8],
            )
            response = cached
        else:
            try:
                response = handle_stop(payload_dict)
            except Exception as exc:  # noqa: BLE001 — fail-open for stop hook
                logger.error("Unhandled stop error: %s", exc)
                return emit_empty()
            if generation_id:
                _remember_stop_response(
                    generation_id, response if isinstance(response, dict) else {}
                )

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
