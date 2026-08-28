"""Create-command parsing helpers for the manage CLI."""

# pylint: disable=import-outside-toplevel

from __future__ import annotations

import sys
from dataclasses import dataclass

from cursor_goal.doctor import _validation_mode
from cursor_goal.logging_config import get_logger
from cursor_goal.state import (
    MAX_FIELD_CHARS,
    MAX_TURN_BUDGET,
    CorruptGoalError,
    GoalLockTimeoutError,
    GoalState,
    normalize_workdir,
)
from cursor_goal.validation import (
    deny_shell_enabled,
    redact_command,
    redact_secrets,
    try_split_argv,
)

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
    native: bool


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
    native = False

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
        elif arg == "--native":
            native = True
            i += 1
        elif arg == "--no-native":
            native = False
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
        native=native,
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
    from cursor_goal.manage import (  # isort: skip
        _refuse_if_data_dir_unsafe,
    )

    return _refuse_if_data_dir_unsafe()


def _refuse_shell_metachar_validation(args: _CreateArgs) -> str | None:
    """Refuse a shell-metachar --test command when shell_ok is false (fail closed)."""
    if not args.test_cmd or args.shell_ok or try_split_argv(args.test_cmd) is not None:
        return None
    if deny_shell_enabled():
        return (
            "[goal] Error: validation requires shell metacharacters but "
            "CURSOR_GOAL_DENY_SHELL is set. Use an argv-safe --test."
        )
    return (
        "[goal] Error: validation requires shell metacharacters but "
        "shell_ok=false. Pass --allow-shell or use an argv-safe --test."
    )


def _create_goal_or_error(
    state: GoalState, *, force: bool
) -> tuple[GoalState | None, int | None]:
    """Create *state* atomically, surfacing corrupt/existing-goal/lock errors.

    Returns ``(created, exit_code)``; when ``exit_code`` is not ``None`` the
    caller should return it immediately (the error has already been printed).
    """
    try:
        from cursor_goal import manage as manage_mod  # isort: skip

        # Surface quarantine before overwrite/create.
        try:
            manage_mod.snapshot_goal(raise_corrupt=True)
        except CorruptGoalError as exc:
            print(f"[goal] Error: {exc}", file=sys.stderr)
            print(
                "[goal] Fix or remove the quarantined goal.json, then retry "
                "(use --force only after clearing corrupt state).",
                file=sys.stderr,
            )
            return None, 1
        created, status = manage_mod.create_goal_atomic(state, force=force)
    except GoalLockTimeoutError as exc:
        print(f"[goal] Error: {exc}", file=sys.stderr)
        return None, 1
    if status == "exists" and created is not None:
        print(
            "[goal] Error: a goal already exists. "
            "Use --force to overwrite, or clear first.",
            file=sys.stderr,
        )
        safe_existing = redact_secrets(created.condition, max_chars=None)
        print(f"[goal] Existing condition: {safe_existing}", file=sys.stderr)
        print(f"[goal] Existing status: {created.status}", file=sys.stderr)
        return None, 1
    return created, None


def _print_created_goal_summary(
    args: _CreateArgs, state: GoalState, *, turn_budget: int, wake_budget: int
) -> None:
    """Print the ``[goal] Goal created:`` summary block for ``cmd_create``."""
    safe_condition = redact_secrets(args.condition, max_chars=None)
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
    if state.workdir:
        print(f"  Workdir: {state.workdir}")
    print(f"  Budget: {turn_budget} turns")
    print(f"  Wake budget: {wake_budget} ticks")
    print(f"  Shell ok: {str(args.shell_ok).lower()}")
    if state.native_continuation:
        print("  Native continuation: true")
