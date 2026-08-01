"""Goal lifecycle: create, status, pause, resume, done, clear."""

from __future__ import annotations

import sys
from collections.abc import Callable
from dataclasses import dataclass

from cursor_goal.logging_config import get_logger
from cursor_goal.state import (
    MAX_FIELD_CHARS,
    MAX_TURN_BUDGET,
    CorruptGoalError,
    GoalLockTimeoutError,
    GoalState,
    clamp_turn_budget,
    clear_goal_files,
    create_goal_atomic,
    mark_goal_achieved,
    mutate_goal,
    now_iso,
    refuse_if_data_dir_insecure,
    snapshot_goal,
)
from cursor_goal.validation import redact_command
from cursor_goal.wake import arm as wake_arm
from cursor_goal.wake import disarm as wake_disarm
from cursor_goal.wake import wake_enabled

logger = get_logger("cursor_goal.manage")


@dataclass(frozen=True)
class _CreateArgs:
    condition: str
    test_cmd: str
    budget: int
    force: bool


def _parse_budget(raw: str) -> int:
    try:
        value = int(raw)
    except ValueError as exc:
        raise ValueError(f"Budget must be a positive integer, got {raw!r}") from exc
    if value < 1:
        raise ValueError(f"Budget must be a positive integer, got {value}")
    if value > MAX_TURN_BUDGET:
        raise ValueError(f"Budget must be <= {MAX_TURN_BUDGET}, got {value}")
    return value


def _parse_create_argv(argv: list[str]) -> _CreateArgs:
    """Parse create CLI flags into a typed args object."""
    condition = ""
    test_cmd = ""
    budget = 20
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
        elif arg == "--force":
            force = True
            i += 1
        else:
            raise ValueError(f"Unknown argument: {arg}")

    return _CreateArgs(
        condition=condition, test_cmd=test_cmd, budget=budget, force=force
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
    insecure = refuse_if_data_dir_insecure()
    if insecure is not None:
        return insecure
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

    state = GoalState(
        active=True,
        condition=args.condition,
        validation_command=args.test_cmd,
        created_at=now_iso(),
        turn_budget=clamp_turn_budget(args.budget),
        turns_used=0,
        wake_ticks=0,
        status="pursuing",
        last_reason="",
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
        created, status = create_goal_atomic(state, force=args.force)
    except GoalLockTimeoutError as exc:
        print(f"[goal] Error: {exc}", file=sys.stderr)
        return 1
    if status == "exists" and created is not None:
        print(
            "[goal] Error: an active pursuing goal already exists. "
            "Use --force to overwrite, or clear/pause first.",
            file=sys.stderr,
        )
        print(f"[goal] Existing condition: {created.condition}", file=sys.stderr)
        return 1

    logger.info(
        "Created goal condition=%r budget=%s validation=%r",
        args.condition,
        args.budget,
        redact_command(args.test_cmd) if args.test_cmd else "",
    )

    print("[goal] Goal created:")
    print(f"  Condition: {args.condition}")
    if args.test_cmd:
        print(f"  Validation: {redact_command(args.test_cmd)}")
    print(f"  Budget: {args.budget} turns")
    print("  Status: pursuing")
    _maybe_arm_wake()
    return 0


def cmd_status(_argv: list[str]) -> int:
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
    print("[goal] Status Report")
    print(f"  Active: {str(display_active).lower()}")
    print(f"  Status: {state.status}")
    print(f"  Condition: {state.condition}")
    print(f"  Progress: {state.turns_used} / {state.turn_budget} turns")
    print(f"  Wake ticks: {state.wake_ticks} / {state.turn_budget}")
    if state.validation_command:
        print(f"  Validation: {redact_command(state.validation_command)}")
        if state.last_validation_exit_code is not None:
            print(f"  Last validation exit: {state.last_validation_exit_code}")
    if state.last_reason:
        print(f"  Last evaluation: {state.last_reason}")
    if state.last_eval_verdict:
        print(f"  Last verdict: {state.last_eval_verdict}")
    print(f"  Created: {state.created_at}")
    return 0


def cmd_pause(_argv: list[str]) -> int:
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
    print(f"[goal] Goal resumed. Continuing toward: {result.condition}")
    _maybe_arm_wake()
    return 0


def cmd_done(argv: list[str]) -> int:
    force = "--force" in argv
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
    if state is None:
        print("[goal] No active goal to mark done.")
        return 1
    wake_disarm(kill_loop=True)
    print(f"[goal] Goal achieved in {state.turns_used} turns: {state.condition}")
    return 0


def cmd_clear(_argv: list[str]) -> int:
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


def _maybe_arm_wake() -> None:
    """Arm wake.json after create/resume; agent must start ``wake loop``."""
    if not wake_enabled():
        return
    try:
        config = wake_arm()
    except OSError as exc:
        logger.warning("Could not arm wake watchdog: %s", exc)
        return
    if not config:
        return
    print(
        f"[goal] Wake armed (every {config['interval_s']}s). "
        "Start `wake loop` in background with notify_on_output "
        f"matching {config['notify_pattern']}."
    )


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
    }
    handler = dispatch.get(command)
    if handler is None:
        print(f"[goal] Error: unknown manage command: {command}", file=sys.stderr)
        _print_help()
        return 1
    return handler(rest)


def _print_help() -> int:
    print("Usage: cursor-goal manage <command> [args...]")
    print('  create "<condition>" [--test "<cmd>"] [--budget <N>] [--force]')
    print("  status     Show current goal state")
    print("  pause      Pause auto-continuation")
    print("  resume     Resume a paused goal")
    print("  done       Mark goal as achieved (requires YES-bound eval signal)")
    print("  clear      Remove goal entirely")
    return 0
