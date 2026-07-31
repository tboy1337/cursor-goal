"""Goal lifecycle: create, status, pause, resume, done, clear."""

from __future__ import annotations

import sys
from collections.abc import Callable
from dataclasses import dataclass

from cursor_goal.logging_config import get_logger
from cursor_goal.state import (
    GoalState,
    clear_eval_signal,
    clear_goal_files,
    load_goal,
    mark_goal_achieved,
    mutate_goal,
    now_iso,
    save_goal,
)
from cursor_goal.validation import redact_command

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
    if len(args.condition) > 4000:
        return (
            f"[goal] Error: condition exceeds 4000 character limit "
            f"({len(args.condition)} chars)"
        )
    existing = load_goal()
    if (
        existing is not None
        and existing.active
        and existing.status == "pursuing"
        and not args.force
    ):
        return (
            "[goal] Error: an active pursuing goal already exists. "
            "Use --force to overwrite, or clear/pause first.\n"
            f"[goal] Existing condition: {existing.condition}"
        )
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
        turn_budget=args.budget,
        turns_used=0,
        status="pursuing",
        last_reason="",
        last_validation_output="",
        last_validation_exit_code=None,
        last_eval_verdict="",
    )
    save_goal(state)
    clear_eval_signal()
    logger.info(
        "Created goal condition=%r budget=%s validation=%r",
        args.condition,
        args.budget,
        redact_command(args.test_cmd) if args.test_cmd else "",
    )

    print("[goal] Goal created:")
    print(f"  Condition: {args.condition}")
    if args.test_cmd:
        print(f"  Validation: {args.test_cmd}")
    print(f"  Budget: {args.budget} turns")
    print("  Status: pursuing")
    return 0


def cmd_status(_argv: list[str]) -> int:
    state = load_goal()
    if state is None:
        print("[goal] No active goal.")
        return 0

    print("[goal] Status Report")
    print(f"  Active: {str(state.active).lower()}")
    print(f"  Status: {state.status}")
    print(f"  Condition: {state.condition}")
    print(f"  Progress: {state.turns_used} / {state.turn_budget} turns")
    if state.validation_command:
        print(f"  Validation: {state.validation_command}")
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

    try:
        result = mutate_goal(mutator)
    except ValueError as exc:
        print(f"[goal] {exc}")
        return 1
    if result is None:
        print("[goal] No active goal to pause.")
        return 1
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
    if result is None:
        print("[goal] No goal to resume.")
        return 1
    print(f"[goal] Goal resumed. Continuing toward: {result.condition}")
    return 0


def cmd_done(argv: list[str]) -> int:
    force = "--force" in argv
    state, status = mark_goal_achieved(require_signal=not force)
    if status == "missing":
        print("[goal] No active goal to mark done.")
        return 1
    if status == "rejected":
        print(
            "[goal] REJECTED: No YES-bound evaluator signal for this cycle.",
            file=sys.stderr,
        )
        print(
            '[goal] Run: cursor-goal eval parse-result "YES: <reason>" '
            "(after spawning an evaluator subagent)",
            file=sys.stderr,
        )
        print("[goal] Then retry: cursor-goal manage done", file=sys.stderr)
        return 1
    if status == "forced":
        print(
            "[goal] --force flag set, proceeding anyway "
            "(protocol violation logged).",
            file=sys.stderr,
        )
        logger.warning("done --force without evaluator signal")
    if state is None:
        print("[goal] No active goal to mark done.")
        return 1
    print(f"[goal] Goal achieved in {state.turns_used} turns: {state.condition}")
    return 0


def cmd_clear(_argv: list[str]) -> int:
    existed = clear_goal_files()
    if existed:
        print("[goal] Goal cleared.")
    else:
        print("[goal] No active goal.")
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
    }
    handler = dispatch.get(command)
    if handler is None:
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
