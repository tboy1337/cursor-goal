"""Goal lifecycle: create, status, pause, resume, update, blocked, done, clear.

Install / health diagnostics (``manage doctor``) live in
:mod:`cursor_goal.doctor`; ``cmd_doctor`` (the CLI dispatch target) and the
two helpers this module actually calls (``_validation_mode``,
``_wake_loop_shell_hint``) are imported here. Doctor's internal check
functions are intentionally *not* re-exported — import ``cursor_goal.doctor``
directly (including in tests) to call them.
"""

from __future__ import annotations

import os
import sys
import time
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from cursor_goal.doctor import (  # noqa: F401  pylint: disable=unused-import
    _validation_mode,
    _wake_loop_shell_hint,
    cmd_doctor,
)
from cursor_goal.logging_config import get_logger
from cursor_goal.manage_common import (
    print_native_continuation_notes as _print_native_continuation_notes,
)
from cursor_goal.manage_common import wake_wanted as _wake_wanted
from cursor_goal.manage_create import (
    _create_goal_or_error,
    _CreateArgs,
    _normalize_workdir,
    _parse_create_argv,
    _print_created_goal_summary,
    _refuse_shell_metachar_validation,
    _validate_create,
)
from cursor_goal.manage_status import cmd_status
from cursor_goal.paths import harness_cmd_report
from cursor_goal.state import (  # pylint: disable=unused-import
    BLOCK_STREAK_REQUIRED,
    MAX_FIELD_CHARS,
    NATIVE_CONTINUATION_ENV,
    CorruptGoalError,
    GoalLockTimeoutError,
    GoalState,
    clamp_turn_budget,
    clamp_wake_budget,
    clear_goal_files,
    create_goal_atomic,
    default_wake_budget,
    mark_goal_achieved,
    mutate_goal,
    native_continuation_env_disabled,
    now_iso,
    record_block_attempt,
    refuse_if_acl_harden_failed,
    refuse_if_data_dir_insecure,
    reset_block_streak,
    resolve_native_continuation_flag,
    snapshot_goal,
    update_goal_condition,
)
from cursor_goal.validation import (
    redact_command,
    redact_secrets,
    weak_condition_warning,
)
from cursor_goal.wake import (
    NOTIFY_PATTERN,
)
from cursor_goal.wake import arm as wake_arm
from cursor_goal.wake import disarm as wake_disarm
from cursor_goal.wake import (
    format_wake_required_line,
)
from cursor_goal.wake import (
    status_info as wake_status_info,  # pylint: disable=unused-import
)
from cursor_goal.wake import (
    wake_enabled,
)

# Re-exports used by sibling modules and tests (monkeypatch surface).
__all__ = (
    "cmd_manage",
    "cmd_status",
    "create_goal_atomic",
    "snapshot_goal",
    "wake_status_info",
    "_validation_mode",
)

logger = get_logger("cursor_goal.manage")


def _refuse_if_data_dir_unsafe() -> str | None:
    """Return an error if the data dir is insecure or ACL harden failed."""
    insecure = refuse_if_data_dir_insecure()
    if insecure is not None:
        return insecure
    return refuse_if_acl_harden_failed()


def _resolve_create_workdir(args: _CreateArgs) -> str:
    """Resolve the workdir for a new goal: explicit --workdir, else cwd."""
    if args.workdir:
        return _normalize_workdir(args.workdir)
    try:
        return str(Path.cwd().resolve())
    except OSError as exc:
        logger.debug("Could not capture create cwd as workdir: %s", exc)
        return ""


@dataclass(frozen=True)
class _ArmWakeResult:
    """Outcome of post-create/resume wake arming."""

    status: str  # ok | disabled | failed
    detail: str = ""
    config: dict[str, Any] | None = None


def _finalize_create_wake_state(*, wake_on: bool) -> int | None:
    """Arm wake (if enabled) and flip the just-created goal to pursuing.

    Returns an error exit code on failure, or ``None`` on success.
    """
    if not wake_on:
        _maybe_arm_wake()
        print("  Status: pursuing")
        return None

    print("  Status: paused (awaiting wake arm)")
    arm_result = _maybe_arm_wake()
    if arm_result.status == "failed":
        return _pause_after_arm_failure(arm_result.detail)
    # Flip paused → pursuing only after arm (or wake disabled mid-flight).
    if arm_result.status not in {"ok", "disabled"}:
        return None

    def activate(goal: GoalState) -> None:
        goal.status = "pursuing"
        goal.active = True
        if goal.last_reason == "awaiting wake arm":
            goal.last_reason = ""

    try:
        mutate_goal(activate)
    except GoalLockTimeoutError as exc:
        print(
            f"[goal] Error: wake armed but could not activate goal: {exc}",
            file=sys.stderr,
        )
        return _pause_after_arm_failure(f"activate after arm failed: {exc}")
    print("  Status: pursuing")
    return None


def cmd_create(argv: list[str]) -> int:
    try:
        args = _parse_create_argv(argv)
    except ValueError as exc:
        print(f"[goal] Error: {exc}", file=sys.stderr)
        return 1

    error = _validate_create(args)
    if error is not None:
        print(error, file=sys.stderr)
        return 1

    shell_error = _refuse_shell_metachar_validation(args)
    if shell_error is not None:
        print(shell_error, file=sys.stderr)
        return 1

    turn_budget = clamp_turn_budget(args.budget)
    wake_budget = (
        clamp_wake_budget(args.wake_budget)
        if args.wake_budget is not None
        else default_wake_budget(turn_budget)
    )
    workdir = _resolve_create_workdir(args)
    native = resolve_native_continuation_flag(args.native)
    if args.native and not native:
        print(
            f"[goal] Warning: {NATIVE_CONTINUATION_ENV}=0 — ignoring --native; "
            "using hooks+wake.",
            file=sys.stderr,
        )
    # When wake is enabled, create paused then arm then flip to pursuing so a
    # crash/lock failure cannot leave an unprotected pursuing goal.
    wake_on = _wake_wanted(native_requested=native)
    state = GoalState(
        active=not wake_on,
        condition=args.condition,
        validation_command=args.test_cmd,
        created_at=now_iso(),
        turn_budget=turn_budget,
        turns_used=0,
        wake_ticks=0,
        wake_budget=wake_budget,
        shell_ok=args.shell_ok,
        workdir=workdir,
        status="pursuing" if not wake_on else "paused",
        last_reason="" if not wake_on else "awaiting wake arm",
        last_validation_output="",
        last_validation_exit_code=None,
        last_eval_verdict="",
        native_continuation=native,
    )
    if args.force:
        try:
            wake_disarm(kill_loop=True)
        except OSError as exc:
            logger.warning("Could not disarm prior wake before force create: %s", exc)
    _created, create_exit_code = _create_goal_or_error(state, force=args.force)
    if create_exit_code is not None:
        return create_exit_code

    logger.info(
        "Created goal condition=%r budget=%s wake_budget=%s shell_ok=%s "
        "workdir=%r validation=%r",
        redact_secrets(args.condition, max_chars=200),
        turn_budget,
        wake_budget,
        args.shell_ok,
        workdir,
        redact_command(args.test_cmd) if args.test_cmd else "",
    )
    _print_created_goal_summary(
        args, state, turn_budget=turn_budget, wake_budget=wake_budget
    )
    if native:
        _print_native_continuation_notes()
    _print_weak_condition_warning(args.condition)
    # Status reflects real state: wake-on create stays paused until activate.
    wake_exit_code = _finalize_create_wake_state(wake_on=wake_on)
    if wake_exit_code is not None:
        return wake_exit_code
    if not os.environ.get("CURSOR_GOAL_LOG_FILE", "").strip():
        print(
            "[goal] Tip: set CURSOR_GOAL_LOG_FILE=1 for durable diagnostics "
            "while debugging stalls."
        )
    return 0


def cmd_pause(_argv: list[str]) -> int:
    unsafe = _refuse_if_data_dir_unsafe()
    if unsafe is not None:
        print(unsafe, file=sys.stderr)
        return 1

    try:
        snapshot_goal(raise_corrupt=True)
    except CorruptGoalError as exc:
        print(f"[goal] Error: {exc}", file=sys.stderr)
        return 1

    def mutator(state: GoalState) -> None:
        if state.status != "pursuing":
            raise ValueError(f"Cannot pause: goal is '{state.status}', not 'pursuing'.")
        state.status = "paused"
        state.active = False

    try:
        result = mutate_goal(mutator)
    except ValueError as exc:
        print(f"[goal] {exc}")
        return 1
    except GoalLockTimeoutError as exc:
        print(f"[goal] Error: {exc}", file=sys.stderr)
        return 1
    if result is None:
        print("[goal] No active goal to pause.")
        return 1
    wake_disarm(kill_loop=True)
    print(
        "[goal] Goal paused. Auto-continuation disabled. "
        "Use 'cursor-goal manage resume' to continue."
    )
    return 0


_RESUMABLE_STATUSES = frozenset({"paused", "blocked"})


def _load_resumable_goal() -> GoalState | None:
    """Load the current goal and confirm it is ``paused`` or ``blocked``.

    Prints a ``[goal] ...`` diagnostic and returns ``None`` for every
    failure mode; callers only need to check for ``None``.
    """
    try:
        current = snapshot_goal(raise_corrupt=True)
    except CorruptGoalError as exc:
        print(f"[goal] Error: {exc}", file=sys.stderr)
        return None
    if current is None:
        print("[goal] No goal to resume.")
        return None
    if current.status not in _RESUMABLE_STATUSES:
        print(
            f"[goal] Cannot resume: goal is '{current.status}', "
            "not 'paused' or 'blocked'."
        )
        return None
    return current


def cmd_resume(_argv: list[str]) -> int:
    unsafe = _refuse_if_data_dir_unsafe()
    if unsafe is not None:
        print(unsafe, file=sys.stderr)
        return 1

    current = _load_resumable_goal()
    if current is None:
        return 1

    # Arm while still paused, then flip to pursuing — avoids unprotected pursue.
    # Native continuation skips wake: CreateGoal owns keep-going.
    if current.native_continuation:
        print(
            "[goal] Native continuation: skipping wake arm "
            "(CreateGoal owns keep-going)."
        )
    else:
        arm_result = _maybe_arm_wake()
        if arm_result.status == "failed":
            return _pause_after_arm_failure(arm_result.detail)

    def mutator(state: GoalState) -> None:
        if state.status not in _RESUMABLE_STATUSES:
            raise ValueError(
                f"Cannot resume: goal is '{state.status}', "
                "not 'paused' or 'blocked'."
            )
        reset_block_streak(state)
        state.status = "pursuing"
        state.active = True

    try:
        result = mutate_goal(mutator)
    except ValueError as exc:
        print(f"[goal] {exc}")
        return _pause_after_arm_failure(f"resume mutate failed: {exc}")
    except GoalLockTimeoutError as exc:
        print(f"[goal] Error: {exc}", file=sys.stderr)
        return _pause_after_arm_failure(f"resume mutate lock timeout: {exc}")
    if result is None:
        print("[goal] No goal to resume.")
        return _pause_after_arm_failure("resume mutate found no goal")
    print(
        "[goal] Goal resumed. Continuing toward: "
        f"{redact_secrets(result.condition, max_chars=None)}"
    )
    if result.native_continuation:
        _print_native_continuation_notes()
    elif wake_enabled():
        print(
            "[goal] Confirm `wake status` continuation_ready=true before other work. "
            "Tip: CURSOR_GOAL_LOG_FILE=1 for durable diagnostics."
        )
    return 0


def _print_weak_condition_warning(condition: str) -> None:
    """Warn on activity-only conditions; never invent --test."""
    warning = weak_condition_warning(condition)
    if warning is None:
        return
    print(f"[goal] Warning: {warning}", file=sys.stderr)


def cmd_update(argv: list[str]) -> int:
    """Change the condition in place without a new goal identity."""
    unsafe = _refuse_if_data_dir_unsafe()
    if unsafe is not None:
        print(unsafe, file=sys.stderr)
        return 1
    condition = " ".join(argv).strip()
    if not condition:
        print(
            '[goal] Error: Usage: cursor-goal manage update "<condition>"',
            file=sys.stderr,
        )
        return 1
    if len(condition) > MAX_FIELD_CHARS:
        print(
            f"[goal] Error: condition exceeds {MAX_FIELD_CHARS} character limit.",
            file=sys.stderr,
        )
        return 1
    try:
        snapshot_goal(raise_corrupt=True)
    except CorruptGoalError as exc:
        print(f"[goal] Error: {exc}", file=sys.stderr)
        return 1
    try:
        state, status = update_goal_condition(condition)
    except GoalLockTimeoutError as exc:
        print(f"[goal] Error: {exc}", file=sys.stderr)
        return 1
    except ValueError as exc:
        print(f"[goal] Error: {exc}", file=sys.stderr)
        return 1
    if status == "missing":
        print("[goal] No goal to update. Create one first.")
        return 1
    if status == "empty":
        print("[goal] Error: condition must not be empty.", file=sys.stderr)
        return 1
    if status == "not_updatable":
        print(
            "[goal] Cannot update: goal is "
            f"'{state.status if state else '?'}'. "
            "Resume or create a new goal.",
            file=sys.stderr,
        )
        return 1
    if status == "unchanged":
        print("[goal] Condition unchanged.")
        return 0
    if state is None:
        print("[goal] No goal to update. Create one first.")
        return 1
    print(
        "[goal] Goal condition updated (same identity). CLEAR+YES invalidated. "
        f"Continuing toward: {redact_secrets(state.condition, max_chars=None)}"
    )
    _print_weak_condition_warning(state.condition)
    return 0


def cmd_blocked(argv: list[str]) -> int:
    """Record a repeated impasse; block continuation after 3 same-reason turns."""
    unsafe = _refuse_if_data_dir_unsafe()
    if unsafe is not None:
        print(unsafe, file=sys.stderr)
        return 1
    reason = " ".join(argv).strip()
    if not reason:
        print(
            '[goal] Error: Usage: cursor-goal manage blocked "<reason>"',
            file=sys.stderr,
        )
        return 1
    try:
        snapshot_goal(raise_corrupt=True)
    except CorruptGoalError as exc:
        print(f"[goal] Error: {exc}", file=sys.stderr)
        return 1
    try:
        state, status = record_block_attempt(reason)
    except GoalLockTimeoutError as exc:
        print(f"[goal] Error: {exc}", file=sys.stderr)
        return 1
    except ValueError as exc:
        print(f"[goal] Error: {exc}", file=sys.stderr)
        return 1
    if status == "empty_reason":
        print("[goal] Error: block reason must not be empty.", file=sys.stderr)
        return 1
    if status == "missing":
        print("[goal] No active goal to mark blocked.")
        return 1
    if status == "not_pursuing":
        print(
            "[goal] Cannot record blocked: goal is "
            f"'{state.status if state else '?'}' (must be pursuing).",
            file=sys.stderr,
        )
        return 1
    if state is None:
        print("[goal] No active goal to mark blocked.")
        return 1
    if status == "blocked":
        try:
            wake_disarm(kill_loop=True)
        except OSError as exc:
            logger.warning("Could not disarm wake after blocked: %s", exc)
        print(
            f"[goal] Goal blocked after {state.block_streak} consecutive turns: "
            f"{redact_secrets(state.last_block_reason, max_chars=500)}"
        )
        print(
            "[goal] Auto-continuation stopped. User: /cursor-goal resume after "
            "resolving the blocker, or /cursor-goal clear."
        )
        if state.native_continuation:
            print(
                "[goal] Native /goal cannot be paused by the agent. "
                "User: pause the native goal (CLI Ctrl+C / UI pause)."
            )
        return 0
    print(
        f"[goal] Block recorded ({state.block_streak}/{BLOCK_STREAK_REQUIRED}): "
        f"{redact_secrets(state.last_block_reason, max_chars=500)}"
    )
    print(
        "[goal] Same blocker must repeat on "
        f"{BLOCK_STREAK_REQUIRED} consecutive pursuing turns "
        "(distinct stop/wake ticks). Status remains pursuing."
    )
    return 1


def cmd_done(argv: list[str]) -> int:
    unsafe = _refuse_if_data_dir_unsafe()
    if unsafe is not None:
        print(unsafe, file=sys.stderr)
        return 1
    force = "--force" in argv
    if force:
        logger.warning(
            "manage done --force (recovery bypass — not cryptographic attestation)"
        )
        print(
            "[goal] Warning: --force bypasses maker≠checker protocol "
            "(not cryptographic attestation).",
            file=sys.stderr,
        )
    try:
        state, status = mark_goal_achieved(require_signal=not force)
    except GoalLockTimeoutError as exc:
        print(f"[goal] Error: {exc}", file=sys.stderr)
        return 1
    if status == "missing":
        print("[goal] No active goal to mark done.")
        return 1
    if status == "not_pursuing":
        print(
            "[goal] REJECTED: Goal is not pursuing "
            f"(status={state.status if state else '?'}). "
            "Resume or create a goal before manage done.",
            file=sys.stderr,
        )
        return 1
    if status == "rejected":
        print(
            "[goal] REJECTED: No YES-bound evaluator signal for this cycle.",
            file=sys.stderr,
        )
        print(
            "[goal] Run: cursor-goal eval parse-result --stdin "
            '(or parse-result "YES: <reason>") after spawning an evaluator.',
            file=sys.stderr,
        )
        print("[goal] Then retry: cursor-goal manage done", file=sys.stderr)
        return 1
    if status == "rejected_audit":
        print(
            "[goal] REJECTED: No CLEAR remaining-work audit signal for this cycle.",
            file=sys.stderr,
        )
        print(
            "[goal] Run: cursor-goal eval parse-audit --stdin "
            "after spawning goal-auditor.",
            file=sys.stderr,
        )
        print("[goal] Then retry: cursor-goal manage done", file=sys.stderr)
        return 1
    if status == "rejected_audit_stale":
        print(
            "[goal] REJECTED: Working tree changed since CLEAR remaining-work "
            "audit. Spawn goal-auditor again.",
            file=sys.stderr,
        )
        print(
            "[goal] Run: cursor-goal eval parse-audit --stdin "
            "after spawning a new goal-auditor on the current tree.",
            file=sys.stderr,
        )
        print("[goal] Then retry: cursor-goal manage done", file=sys.stderr)
        return 1
    if status == "rejected_audit_confirm":
        print(
            "[goal] REJECTED: No confirm-pass CLEAR remaining-work audit "
            "signal for this cycle. Broad goals require "
            "eval audit-prompt --confirm then eval parse-audit --confirm.",
            file=sys.stderr,
        )
        print(
            "[goal] Run: cursor-goal eval parse-audit --confirm --stdin "
            "after spawning a new goal-auditor with eval audit-prompt "
            "--confirm.",
            file=sys.stderr,
        )
        print("[goal] Then retry: cursor-goal manage done", file=sys.stderr)
        return 1
    if status == "forced":
        print(
            "[goal] --force flag set, proceeding anyway "
            "(protocol violation — not cryptographic attestation).",
            file=sys.stderr,
        )
        logger.warning("done --force without evaluator signal")
    if state is None:  # pragma: no cover — mark_goal_achieved returns missing first
        print("[goal] No active goal to mark done.")
        return 1
    wake_disarm(kill_loop=True)
    print(
        f"[goal] Goal achieved in {state.turns_used} turns: "
        f"{redact_secrets(state.condition, max_chars=None)}"
    )
    if state.native_continuation:
        _print_native_continuation_notes(achieved=True)
    return 0


def cmd_clear(_argv: list[str]) -> int:
    unsafe = _refuse_if_data_dir_unsafe()
    if unsafe is not None:
        print(unsafe, file=sys.stderr)
        return 1
    try:
        existed = clear_goal_files()
    except GoalLockTimeoutError as exc:
        print(f"[goal] Error: {exc}", file=sys.stderr)
        return 1
    wake_disarm(kill_loop=True)
    if existed:
        print("[goal] Goal cleared.")
    else:
        print("[goal] No active goal.")
    return 0


def _pause_after_arm_failure(detail: str) -> int:
    """Leave goal paused after wake arm failure; never pursue unprotected."""
    reason = f"wake arm failed: {detail}"[:500]

    def mutator(state: GoalState) -> None:
        state.status = "paused"
        state.active = False
        state.last_reason = reason

    last_exc: Exception | None = None
    for attempt in range(8):
        try:
            mutate_goal(mutator)
            last_exc = None
            break
        except GoalLockTimeoutError as exc:
            last_exc = exc
            time.sleep(0.05 * float(attempt + 1))
    # Always disarm — even when pause mutate fails — so wake is not left armed.
    try:
        wake_disarm(kill_loop=True)
    except OSError as exc:
        logger.debug("Disarm after arm failure: %s", exc)
    if last_exc is not None:
        print(
            f"[goal] Error: wake arm failed and pause also failed after retries: "
            f"{last_exc}",
            file=sys.stderr,
        )
        print(
            "[goal] CRITICAL: goal may still be pursuing without wake. "
            "Retry `manage pause` or `manage clear` immediately.",
            file=sys.stderr,
        )
        return 1
    print(
        f"[goal] Error: wake arm failed — goal paused (not pursuing). {detail}",
        file=sys.stderr,
    )
    print(
        "[goal] Fix data-dir/ACL issues, then `manage resume` "
        "(or set CURSOR_GOAL_WAKE=0 to opt out of wake).",
        file=sys.stderr,
    )
    return 1


def _maybe_arm_wake() -> _ArmWakeResult:
    """Arm wake.json after create/resume; emit GOAL_WAKE_REQUIRED on success.

    Returns ok / disabled / failed. Callers must pause+exit 1 on failed so a
    pursuing goal is never left without an armed wake when wake is enabled.
    """
    try:
        current = snapshot_goal()
    except GoalLockTimeoutError as exc:
        return _ArmWakeResult(status="failed", detail=str(exc))
    if current is not None and current.native_continuation:
        logger.info("Wake arm skipped (native continuation)")
        print(
            "[goal] Native continuation: skipping wake arm "
            "(CreateGoal owns keep-going)."
        )
        return _ArmWakeResult(status="disabled")
    if not wake_enabled():
        print(
            "[goal] Wake: disabled (CURSOR_GOAL_WAKE=0). "
            "Continuation relies on in-turn evaluation and stop hook only."
        )
        return _ArmWakeResult(status="disabled")
    try:
        config = wake_arm()
    except OSError as exc:
        logger.warning("Could not arm wake watchdog: %s", exc)
        return _ArmWakeResult(status="failed", detail=str(exc))
    if not config:
        print(
            "[goal] Wake: disabled (CURSOR_GOAL_WAKE=0). "
            "Continuation relies on in-turn evaluation and stop hook only."
        )
        return _ArmWakeResult(status="disabled")

    print(format_wake_required_line(config))
    hint = _wake_loop_shell_hint()
    pattern = str(config.get("notify_pattern") or NOTIFY_PATTERN)
    print(
        f"[goal] Wake armed (every {config['interval_s']}s). "
        "REQUIRED next step: start `wake loop` in a background Shell with "
        f"notify_on_output matching {pattern}:"
    )
    print(f"  {hint}")
    print("[goal] BLOCKING CHECKLIST — do not start other goal work until all pass:")
    print(f"  1) Background Shell: {hint}")
    print(f"     with notify_on_output matching {pattern}")
    print(
        "  2) Run `wake status` and confirm continuation_ready=true "
        "(pid_alive=true and armed=true)"
    )
    print("  3) Only then continue working toward the condition")
    print(
        "[goal] BLOCKING: continuation_ready=false until wake loop started "
        "with notify_on_output matching the pattern above"
    )
    return _ArmWakeResult(status="ok", config=config)


def cmd_native_on(_argv: list[str]) -> int:
    """Mark an existing goal as using native CreateGoal continuation.

    Agent-facing: call after CreateGoal succeeds when a ``/cursor-goal``
    create ran without ``--native``.
    Disarms wake so hooks+wake do not compete with the platform runtime.
    """
    unsafe = _refuse_if_data_dir_unsafe()
    if unsafe is not None:
        print(unsafe, file=sys.stderr)
        return 1
    if native_continuation_env_disabled():
        print(
            f"[goal] Error: {NATIVE_CONTINUATION_ENV}=0 forbids native "
            "continuation; using hooks+wake.",
            file=sys.stderr,
        )
        return 1
    try:
        snapshot_goal(raise_corrupt=True)
    except CorruptGoalError as exc:
        print(f"[goal] Error: {exc}", file=sys.stderr)
        return 1

    def mutator(state: GoalState) -> None:
        state.native_continuation = True

    try:
        result = mutate_goal(mutator)
    except GoalLockTimeoutError as exc:
        print(f"[goal] Error: {exc}", file=sys.stderr)
        return 1
    if result is None:
        print("[goal] No active goal to mark native.")
        return 1
    try:
        wake_disarm(kill_loop=True)
    except OSError as exc:
        logger.warning("Could not disarm wake after native-on: %s", exc)
    print("[goal] Native continuation recorded.")
    _print_native_continuation_notes()
    return 0


def cmd_harness_cmd(_argv: list[str]) -> int:
    """Print resolved harness invocation (classic or marketplace)."""
    try:
        report = harness_cmd_report()
    except ValueError as exc:
        print(f"[goal] Error: {exc}", file=sys.stderr)
        return 1
    print(f"[goal] Skill root: {report['skill_root']}")
    print(f"[goal] run_goal.py: {report['run_goal']} (exists={report['exists']})")
    print(f"[goal] Invocation template: {report['invocation']}")
    print(f"[goal] Wake loop: {report['wake_loop']}")
    if report["cursor_goal_home"]:
        print(f"[goal] CURSOR_GOAL_HOME: {report['cursor_goal_home']}")
    if report["cursor_plugin_root"]:
        print(f"[goal] CURSOR_PLUGIN_ROOT: {report['cursor_plugin_root']}")
    if not report["exists"]:
        print(
            "[goal] Error: run_goal.py does not exist at the resolved path. "
            "Install the skill (install-goal.sh / install-goal.ps1), enable the "
            "Teams marketplace plugin, or set CURSOR_GOAL_HOME / "
            "CURSOR_PLUGIN_ROOT to a tree containing "
            "skills/cursor-goal/scripts/run_goal.py.",
            file=sys.stderr,
        )
        return 1
    return 0


def cmd_manage(argv: list[str]) -> int:
    if not argv:
        _print_help()
        return 1
    command = argv[0]
    rest = argv[1:]
    if command == "help":
        return _print_help()
    dispatch: dict[str, Callable[[list[str]], int]] = {
        "create": cmd_create,
        "status": cmd_status,
        "pause": cmd_pause,
        "resume": cmd_resume,
        "update": cmd_update,
        "blocked": cmd_blocked,
        "done": cmd_done,
        "clear": cmd_clear,
        "doctor": cmd_doctor,
        "harness-cmd": cmd_harness_cmd,
        "native-on": cmd_native_on,
    }
    handler = dispatch.get(command)
    if handler is None:
        print(f"[goal] Error: unknown manage command: {command}", file=sys.stderr)
        _print_help()
        return 1
    return handler(rest)


def _print_help() -> int:
    print("Usage: cursor-goal manage <command> [args...]")
    print(
        '  create "<condition>" [--test "<cmd>"] [--budget <N>] '
        "[--wake-budget <N>] [--workdir <path>] [--allow-shell] "
        "[--deny-shell] [--force] [--native] [--no-native]"
    )
    print("  status     Show current goal state")
    print("  doctor     Install / health diagnostics")
    print("  harness-cmd  Print resolved run_goal.py / wake loop invocation")
    print("  native-on  Record CreateGoal success; skip wake / worker stop followups")
    print("  pause      Pause auto-continuation (user /cursor-goal pause only)")
    print("  resume     Resume a paused or blocked goal")
    print('  update "<condition>"  Change condition in place (invalidates CLEAR+YES)')
    print('  blocked "<reason>"    Record an impasse; blocks after 3 same-reason turns')
    print(
        "  done       Mark goal as achieved "
        "(requires YES + CLEAR; broad also needs confirm-pass)"
    )
    print("  clear      Remove goal entirely")
    return 0
