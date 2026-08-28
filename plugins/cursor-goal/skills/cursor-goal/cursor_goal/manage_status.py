"""manage status command."""

# pylint: disable=import-outside-toplevel

from __future__ import annotations

import sys

from cursor_goal.doctor import _validation_mode, _wake_loop_shell_hint
from cursor_goal.state import (
    BLOCK_STREAK_REQUIRED,
    CorruptGoalError,
    GoalLockTimeoutError,
    has_audit_confirm_signal,
)
from cursor_goal.validation import is_broad_condition, redact_command, redact_secrets
from cursor_goal.wake import NOTIFY_PATTERN, wake_enabled


# pylint: disable-next=too-many-branches,too-many-statements
def cmd_status(_argv: list[str]) -> int:
    from cursor_goal import manage as manage_mod  # isort: skip

    try:
        state = manage_mod.snapshot_goal(raise_corrupt=True)
    except CorruptGoalError as exc:
        print(f"[goal] Error: {exc}", file=sys.stderr)
        print(
            "[goal] Fix or remove ~/.cursor-goal/data/goal.json "
            "(or CURSOR_GOAL_DATA), then retry. Corrupt files are renamed to "
            "goal.json.corrupt.<UTC>.",
            file=sys.stderr,
        )
        return 1
    except GoalLockTimeoutError as exc:
        print(f"[goal] Error: {exc}", file=sys.stderr)
        return 1

    if state is None:
        print("[goal] No active goal.")
        return 0

    # Derive displayed activity from pursuing status so paused goals are clear.
    display_active = state.active and state.status == "pursuing"
    wake_info = manage_mod.wake_status_info()
    print("[goal] Status Report")
    print(f"  Active: {str(display_active).lower()}")
    print(f"  Status: {state.status}")
    print(f"  Condition: {redact_secrets(state.condition, max_chars=None)}")
    if state.block_streak:
        print(f"  Block streak: {state.block_streak} / {BLOCK_STREAK_REQUIRED}")
        if state.last_block_reason:
            print(
                "  Last block reason: "
                f"{redact_secrets(state.last_block_reason, max_chars=500)}"
            )
    if state.condition_updated_pending:
        print("  Condition updated: pending (next followup will note it)")
    print(f"  Progress: {state.turns_used} / {state.turn_budget} turns")
    print(f"  Wake ticks: {state.wake_ticks} / {state.wake_budget}")
    print(f"  Schema: {state.schema_version}")
    print(f"  Native continuation: {str(state.native_continuation).lower()}")
    print(f"  Shell ok: {str(state.shell_ok).lower()}")
    mode = _validation_mode(state)
    print(f"  Validation mode: {mode}")
    if state.workdir:
        print(f"  Workdir: {state.workdir}")
    if state.validation_command:
        print(f"  Validation: {redact_command(state.validation_command)}")
        if state.last_validation_exit_code is not None:
            print(f"  Last validation exit: {state.last_validation_exit_code}")
    if not wake_enabled() or state.native_continuation:
        if state.native_continuation:
            print("  Wake service: skipped (native continuation)")
            print("  Continuation ready: true (native CreateGoal/UpdateGoal)")
        else:
            print("  Wake service: disabled (CURSOR_GOAL_WAKE=0)")
    elif wake_info.get("armed"):
        alive = "yes" if wake_info.get("pid_alive") else "no"
        print(
            f"  Wake service: armed gen={wake_info.get('token_prefix', '?')} "
            f"alive={alive} interval_s={wake_info.get('interval_s')}"
        )
    else:
        print("  Wake service: not armed")
    wake_gate = wake_enabled() and not state.native_continuation
    ready = bool(wake_info.get("continuation_ready"))
    reason = str(wake_info.get("continuation_reason") or "")
    if not state.native_continuation:
        print(f"  Continuation ready: {str(ready).lower()} ({reason})")
    if wake_gate and wake_info.get("heartbeat_stale"):
        print(
            "  Warning: wake heartbeat_stale — loop PID is alive but "
            "last_emit_at is older than 2× interval; restart wake loop if stalled"
        )
    if display_active and wake_gate and not ready:
        hint = str(wake_info.get("command") or _wake_loop_shell_hint())
        pattern = str(
            wake_info.get("notify_pattern")
            or wake_info.get("pattern")
            or NOTIFY_PATTERN
        )
        if reason == "not_armed" or not wake_info.get("armed"):
            print(
                "  ACTION REQUIRED: wake not armed while pursuing — start background "
                f"Shell `{hint}` with notify_on_output matching {pattern}, "
                "then confirm `wake status` shows continuation_ready=true"
            )
        else:
            print(
                "  ACTION REQUIRED: wake loop not alive — start background Shell "
                f"`{hint}` with notify_on_output matching {pattern}, "
                "then confirm continuation_ready=true / pid_alive=true"
            )
    if state.last_reason:
        print(f"  Last evaluation: {redact_secrets(state.last_reason, max_chars=500)}")
    if state.last_eval_verdict:
        print(f"  Last verdict: {state.last_eval_verdict}")
    if state.last_audit_verdict:
        print(f"  Last audit: {state.last_audit_verdict}")
    if is_broad_condition(state.condition):
        print("  Audit scope: broad (confirm-pass required)")
        confirm_line = "CLEAR" if has_audit_confirm_signal() else "missing"
        print(f"  Confirm audit: {confirm_line}")
    else:
        print("  Audit scope: narrow")
    print(f"  Created: {state.created_at}")
    if display_active and wake_gate and not ready:
        return 1
    return 0
