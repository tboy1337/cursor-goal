"""Goal lifecycle: create, status, pause, resume, done, clear.

Install / health diagnostics (``manage doctor``) live in
:mod:`cursor_goal.doctor` and are imported/re-exported here so the CLI
dispatch table and existing ``from cursor_goal.manage import ...`` call
sites (including tests) keep working unchanged.
"""

from __future__ import annotations

import os
import sys
import time
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from cursor_goal.doctor import _baked_python_from_cmd  # noqa: F401 — re-export
from cursor_goal.doctor import _classic_hooks_configured  # noqa: F401
from cursor_goal.doctor import _cursor_goal_python_is_unsafe  # noqa: F401
from cursor_goal.doctor import _hooks_look_configured  # noqa: F401
from cursor_goal.doctor import _hooks_stacking_failure  # noqa: F401
from cursor_goal.doctor import _hooks_stacking_warning  # noqa: F401
from cursor_goal.doctor import _install_version_failures  # noqa: F401
from cursor_goal.doctor import _is_absolute_interpreter_path  # noqa: F401
from cursor_goal.doctor import _marketplace_hooks_configured  # noqa: F401
from cursor_goal.doctor import _stale_baked_python_failures  # noqa: F401
from cursor_goal.doctor import _validation_mode, _wake_loop_shell_hint, cmd_doctor
from cursor_goal.logging_config import get_logger
from cursor_goal.paths import skill_root  # noqa: F401 — re-export
from cursor_goal.paths import (
    harness_cmd_report,
    wake_loop_invocation,
)
from cursor_goal.state import (
    MAX_FIELD_CHARS,
    MAX_TURN_BUDGET,
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
    normalize_workdir,
    now_iso,
    refuse_if_acl_harden_failed,
    refuse_if_data_dir_insecure,
    snapshot_goal,
)
from cursor_goal.validation import (
    deny_shell_enabled,
    redact_command,
    redact_secrets,
    try_split_argv,
)
from cursor_goal.wake import NOTIFY_PATTERN
from cursor_goal.wake import arm as wake_arm
from cursor_goal.wake import disarm as wake_disarm
from cursor_goal.wake import format_wake_required_line
from cursor_goal.wake import status_info as wake_status_info
from cursor_goal.wake import wake_enabled

logger = get_logger("cursor_goal.manage")


@dataclass(frozen=True)
class _CreateArgs:
    condition: str
    test_cmd: str
    budget: int
    wake_budget: int | None
    shell_ok: bool
    workdir: str
    force: bool


@dataclass(frozen=True)
class _ArmWakeResult:
    """Outcome of post-create/resume wake arming."""

    status: str  # ok | disabled | failed
    detail: str = ""
    config: dict[str, Any] | None = None


def _parse_budget(raw: str, *, label: str = "Budget") -> int:
    try:
        value = int(raw)
    except ValueError as exc:
        raise ValueError(f"{label} must be a positive integer, got {raw!r}") from exc
    if value < 1:
        raise ValueError(f"{label} must be a positive integer, got {value}")
    if value > MAX_TURN_BUDGET:
        raise ValueError(f"{label} must be <= {MAX_TURN_BUDGET}, got {value}")
    return value


def _normalize_workdir(raw: str) -> str:
    """Expand and validate workdir; return absolute path string or raise ValueError."""
    return normalize_workdir(raw)


# pylint: disable-next=too-many-branches
def _parse_create_argv(argv: list[str]) -> _CreateArgs:
    """Parse create CLI flags into a typed args object."""
    condition = ""
    test_cmd = ""
    budget = 20
    wake_budget: int | None = None
    shell_ok = False
    workdir = ""
    force = False

    args = list(argv)
    if args and not args[0].startswith("--"):
        condition = args.pop(0)

    i = 0
    while i < len(args):
        arg = args[i]
        if arg == "--test":
            if i + 1 >= len(args):
                raise ValueError("--test requires a value")
            test_cmd = args[i + 1]
            i += 2
        elif arg == "--budget":
            if i + 1 >= len(args):
                raise ValueError("--budget requires a value")
            budget = _parse_budget(args[i + 1])
            i += 2
        elif arg == "--wake-budget":
            if i + 1 >= len(args):
                raise ValueError("--wake-budget requires a value")
            wake_budget = _parse_budget(args[i + 1], label="Wake budget")
            i += 2
        elif arg == "--workdir":
            if i + 1 >= len(args):
                raise ValueError("--workdir requires a value")
            workdir = args[i + 1]
            i += 2
        elif arg == "--deny-shell":
            shell_ok = False
            i += 1
        elif arg == "--allow-shell":
            shell_ok = True
            i += 1
        elif arg == "--force":
            force = True
            i += 1
        else:
            raise ValueError(f"Unknown argument: {arg}")

    return _CreateArgs(
        condition=condition,
        test_cmd=test_cmd,
        budget=budget,
        wake_budget=wake_budget,
        shell_ok=shell_ok,
        workdir=workdir,
        force=force,
    )


def _validate_create(args: _CreateArgs) -> str | None:
    """Return an error message if create args are invalid, else None."""
    if not args.condition:
        return (
            "[goal] Error: condition is required. "
            'Usage: cursor-goal manage create "<condition>"'
        )
    if len(args.condition) > MAX_FIELD_CHARS:
        return (
            f"[goal] Error: condition exceeds {MAX_FIELD_CHARS} character limit "
            f"({len(args.condition)} chars)"
        )
    if len(args.test_cmd) > MAX_FIELD_CHARS:
        return (
            f"[goal] Error: validation command exceeds {MAX_FIELD_CHARS} "
            f"character limit ({len(args.test_cmd)} chars)"
        )
    if args.workdir:
        try:
            _normalize_workdir(args.workdir)
        except ValueError as exc:
            return f"[goal] Error: {exc}"
    insecure = refuse_if_data_dir_insecure()
    if insecure is not None:
        return insecure
    acl_fail = refuse_if_acl_harden_failed()
    if acl_fail is not None:
        return acl_fail
    return None


def cmd_create(argv: list[str]) -> int:  # pylint: disable=too-many-branches
    try:
        args = _parse_create_argv(argv)
    except ValueError as exc:
        print(f"[goal] Error: {exc}", file=sys.stderr)
        return 1

    error = _validate_create(args)
    if error is not None:
        print(error, file=sys.stderr)
        return 1

    # Refuse shell-metachar validation when shell_ok is false (fail closed).
    if args.test_cmd and not args.shell_ok and try_split_argv(args.test_cmd) is None:
        if deny_shell_enabled():
            print(
                "[goal] Error: validation requires shell metacharacters but "
                "CURSOR_GOAL_DENY_SHELL is set. Use an argv-safe --test.",
                file=sys.stderr,
            )
        else:
            print(
                "[goal] Error: validation requires shell metacharacters but "
                "shell_ok=false. Pass --allow-shell or use an argv-safe --test.",
                file=sys.stderr,
            )
        return 1

    turn_budget = clamp_turn_budget(args.budget)
    wake_budget = (
        clamp_wake_budget(args.wake_budget)
        if args.wake_budget is not None
        else default_wake_budget(turn_budget)
    )
    workdir = ""
    if args.workdir:
        workdir = _normalize_workdir(args.workdir)
    else:
        try:
            workdir = str(Path.cwd().resolve())
        except OSError as exc:
            logger.debug("Could not capture create cwd as workdir: %s", exc)
            workdir = ""
    # When wake is enabled, create paused then arm then flip to pursuing so a
    # crash/lock failure cannot leave an unprotected pursuing goal.
    wake_on = wake_enabled()
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
    )
    if args.force:
        try:
            wake_disarm(kill_loop=True)
        except OSError as exc:
            logger.warning("Could not disarm prior wake before force create: %s", exc)
    try:
        # Surface quarantine before overwrite/create.
        try:
            snapshot_goal(raise_corrupt=True)
        except CorruptGoalError as exc:
            print(f"[goal] Error: {exc}", file=sys.stderr)
            print(
                "[goal] Fix or remove the quarantined goal.json, then retry "
                "(use --force only after clearing corrupt state).",
                file=sys.stderr,
            )
            return 1
        created, status = create_goal_atomic(state, force=args.force)
    except GoalLockTimeoutError as exc:
        print(f"[goal] Error: {exc}", file=sys.stderr)
        return 1
    if status == "exists" and created is not None:
        print(
            "[goal] Error: a goal already exists. "
            "Use --force to overwrite, or clear first.",
            file=sys.stderr,
        )
        safe_existing = redact_secrets(created.condition, max_chars=None)
        print(f"[goal] Existing condition: {safe_existing}", file=sys.stderr)
        print(f"[goal] Existing status: {created.status}", file=sys.stderr)
        return 1

    safe_condition = redact_secrets(args.condition, max_chars=None)
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

    print("[goal] Goal created:")
    print(f"  Condition: {safe_condition}")
    if args.test_cmd:
        print(f"  Validation: {redact_command(args.test_cmd)}")
        mode = _validation_mode(state)
        print(f"  Validation mode: {mode}")
        if mode == "shell":
            print(
                "  Warning: shell-mode validation (trusted-user goal.json). "
                "Prefer argv-safe commands; shell was explicitly enabled "
                "with --allow-shell."
            )
    if workdir:
        print(f"  Workdir: {workdir}")
    print(f"  Budget: {turn_budget} turns")
    print(f"  Wake budget: {wake_budget} ticks")
    print(f"  Shell ok: {str(args.shell_ok).lower()}")
    # Status reflects real state: wake-on create stays paused until activate.
    if wake_on:
        print("  Status: paused (awaiting wake arm)")
        arm_result = _maybe_arm_wake()
        if arm_result.status == "failed":
            return _pause_after_arm_failure(arm_result.detail)
        # Flip paused → pursuing only after arm (or wake disabled mid-flight).
        if arm_result.status in {"ok", "disabled"}:

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
    else:
        _maybe_arm_wake()
        print("  Status: pursuing")
    if not os.environ.get("CURSOR_GOAL_LOG_FILE", "").strip():
        print(
            "[goal] Tip: set CURSOR_GOAL_LOG_FILE=1 for durable diagnostics "
            "while debugging stalls."
        )
    return 0


def cmd_status(_argv: list[str]) -> int:  # pylint: disable=too-many-branches
    try:
        state = snapshot_goal(raise_corrupt=True)
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
    wake_info = wake_status_info()
    print("[goal] Status Report")
    print(f"  Active: {str(display_active).lower()}")
    print(f"  Status: {state.status}")
    print(f"  Condition: {redact_secrets(state.condition, max_chars=None)}")
    print(f"  Progress: {state.turns_used} / {state.turn_budget} turns")
    print(f"  Wake ticks: {state.wake_ticks} / {state.wake_budget}")
    print(f"  Schema: {state.schema_version}")
    print(f"  Shell ok: {str(state.shell_ok).lower()}")
    mode = _validation_mode(state)
    print(f"  Validation mode: {mode}")
    if state.workdir:
        print(f"  Workdir: {state.workdir}")
    if state.validation_command:
        print(f"  Validation: {redact_command(state.validation_command)}")
        if state.last_validation_exit_code is not None:
            print(f"  Last validation exit: {state.last_validation_exit_code}")
    if not wake_enabled():
        print("  Wake service: disabled (CURSOR_GOAL_WAKE=0)")
    elif wake_info.get("armed"):
        alive = "yes" if wake_info.get("pid_alive") else "no"
        print(
            f"  Wake service: armed gen={wake_info.get('token_prefix', '?')} "
            f"alive={alive} interval_s={wake_info.get('interval_s')}"
        )
    else:
        print("  Wake service: not armed")
    ready = bool(wake_info.get("continuation_ready"))
    reason = str(wake_info.get("continuation_reason") or "")
    print(f"  Continuation ready: {str(ready).lower()} ({reason})")
    if wake_info.get("heartbeat_stale"):
        print(
            "  Warning: wake heartbeat_stale — loop PID is alive but "
            "last_emit_at is older than 2× interval; restart wake loop if stalled"
        )
    if display_active and wake_enabled() and not ready:
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
    print(f"  Created: {state.created_at}")
    if display_active and wake_enabled() and not ready:
        return 1
    return 0


def cmd_pause(_argv: list[str]) -> int:
    insecure = refuse_if_data_dir_insecure()
    if insecure is not None:
        print(insecure, file=sys.stderr)
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


def cmd_resume(_argv: list[str]) -> int:
    insecure = refuse_if_data_dir_insecure()
    if insecure is not None:
        print(insecure, file=sys.stderr)
        return 1

    try:
        current = snapshot_goal(raise_corrupt=True)
    except CorruptGoalError as exc:
        print(f"[goal] Error: {exc}", file=sys.stderr)
        return 1
    if current is None:
        print("[goal] No goal to resume.")
        return 1
    if current.status != "paused":
        print(f"[goal] Cannot resume: goal is '{current.status}', not 'paused'.")
        return 1

    # Arm while still paused, then flip to pursuing — avoids unprotected pursue.
    arm_result = _maybe_arm_wake()
    if arm_result.status == "failed":
        return _pause_after_arm_failure(arm_result.detail)

    def mutator(state: GoalState) -> None:
        if state.status != "paused":
            raise ValueError(f"Cannot resume: goal is '{state.status}', not 'paused'.")
        state.status = "pursuing"
        state.active = True

    try:
        result = mutate_goal(mutator)
    except ValueError as exc:
        print(f"[goal] {exc}")
        return 1
    except GoalLockTimeoutError as exc:
        print(f"[goal] Error: {exc}", file=sys.stderr)
        return 1
    if result is None:
        print("[goal] No goal to resume.")
        return 1
    print(
        "[goal] Goal resumed. Continuing toward: "
        f"{redact_secrets(result.condition, max_chars=None)}"
    )
    if wake_enabled():
        print(
            "[goal] Confirm `wake status` continuation_ready=true before other work. "
            "Tip: CURSOR_GOAL_LOG_FILE=1 for durable diagnostics."
        )
    return 0


def cmd_done(argv: list[str]) -> int:
    insecure = refuse_if_data_dir_insecure()
    if insecure is not None:
        print(insecure, file=sys.stderr)
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
    return 0


def cmd_clear(_argv: list[str]) -> int:
    insecure = refuse_if_data_dir_insecure()
    if insecure is not None:
        print(insecure, file=sys.stderr)
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
    try:
        wake_disarm(kill_loop=True)
    except OSError as exc:
        logger.debug("Disarm after arm failure: %s", exc)
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
            "skills/goal/scripts/run_goal.py.",
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
        "done": cmd_done,
        "clear": cmd_clear,
        "doctor": cmd_doctor,
        "harness-cmd": cmd_harness_cmd,
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
        "[--deny-shell] [--force]"
    )
    print("  status     Show current goal state")
    print("  doctor     Install / health diagnostics")
    print("  harness-cmd  Print resolved run_goal.py / wake loop invocation")
    print("  pause      Pause auto-continuation")
    print("  resume     Resume a paused goal")
    print("  done       Mark goal as achieved (requires YES-bound eval signal)")
    print("  clear      Remove goal entirely")
    return 0
