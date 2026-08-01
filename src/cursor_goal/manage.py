"""Goal lifecycle: create, status, pause, resume, done, clear, doctor."""

from __future__ import annotations

import os
import shutil
import sys
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path

from cursor_goal.logging_config import get_logger
from cursor_goal.state import (
    MAX_FIELD_CHARS,
    MAX_TURN_BUDGET,
    CorruptGoalError,
    GoalLockTimeoutError,
    GoalState,
    acl_harden_failure_message,
    clamp_turn_budget,
    clamp_wake_budget,
    clear_goal_files,
    create_goal_atomic,
    data_dir,
    data_dir_is_insecure,
    default_wake_budget,
    mark_goal_achieved,
    mutate_goal,
    now_iso,
    refuse_if_acl_harden_failed,
    refuse_if_data_dir_insecure,
    snapshot_goal,
)
from cursor_goal.validation import deny_shell_enabled, redact_command, try_split_argv
from cursor_goal.wake import arm as wake_arm
from cursor_goal.wake import disarm as wake_disarm
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
    force: bool


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


# pylint: disable-next=too-many-branches
def _parse_create_argv(argv: list[str]) -> _CreateArgs:
    """Parse create CLI flags into a typed args object."""
    condition = ""
    test_cmd = ""
    budget = 20
    wake_budget: int | None = None
    shell_ok = True
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
        elif arg == "--deny-shell":
            shell_ok = False
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
    insecure = refuse_if_data_dir_insecure()
    if insecure is not None:
        return insecure
    acl_fail = refuse_if_acl_harden_failed()
    if acl_fail is not None:
        return acl_fail
    return None


def _wake_loop_shell_hint() -> str:
    """OS-appropriate wake loop command for doctor / create hints."""
    if os.name == "nt":
        return (
            'py -3 -u "$env:USERPROFILE\\.cursor\\skills\\goal\\'
            'scripts\\run_goal.py" wake loop'
        )
    return "python3 -u ~/.cursor/skills/goal/scripts/run_goal.py wake loop"


def _validation_mode(state: GoalState) -> str:
    """Return argv|shell|none|denied for status/doctor."""
    cmd = (state.validation_command or "").strip()
    if not cmd:
        return "none"
    if deny_shell_enabled() or not state.shell_ok:
        if try_split_argv(cmd) is None:
            return "denied"
        return "argv"
    if try_split_argv(cmd) is None:
        return "shell"
    return "argv"


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

    turn_budget = clamp_turn_budget(args.budget)
    wake_budget = (
        clamp_wake_budget(args.wake_budget)
        if args.wake_budget is not None
        else default_wake_budget(turn_budget)
    )
    state = GoalState(
        active=True,
        condition=args.condition,
        validation_command=args.test_cmd,
        created_at=now_iso(),
        turn_budget=turn_budget,
        turns_used=0,
        wake_ticks=0,
        wake_budget=wake_budget,
        shell_ok=args.shell_ok,
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
        "Created goal condition=%r budget=%s wake_budget=%s shell_ok=%s validation=%r",
        args.condition,
        turn_budget,
        wake_budget,
        args.shell_ok,
        redact_command(args.test_cmd) if args.test_cmd else "",
    )

    print("[goal] Goal created:")
    print(f"  Condition: {args.condition}")
    if args.test_cmd:
        print(f"  Validation: {redact_command(args.test_cmd)}")
        mode = _validation_mode(state)
        print(f"  Validation mode: {mode}")
        if mode == "shell":
            print(
                "  Warning: shell-mode validation (trusted-user goal.json). "
                "Prefer argv-safe commands or pass --deny-shell."
            )
    print(f"  Budget: {turn_budget} turns")
    print(f"  Wake budget: {wake_budget} ticks")
    print(f"  Shell ok: {str(args.shell_ok).lower()}")
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
    wake_info = wake_status_info()
    print("[goal] Status Report")
    print(f"  Active: {str(display_active).lower()}")
    print(f"  Status: {state.status}")
    print(f"  Condition: {state.condition}")
    print(f"  Progress: {state.turns_used} / {state.turn_budget} turns")
    print(f"  Wake ticks: {state.wake_ticks} / {state.wake_budget}")
    print(f"  Schema: {state.schema_version}")
    print(f"  Shell ok: {str(state.shell_ok).lower()}")
    mode = _validation_mode(state)
    print(f"  Validation mode: {mode}")
    if state.validation_command:
        print(f"  Validation: {redact_command(state.validation_command)}")
        if state.last_validation_exit_code is not None:
            print(f"  Last validation exit: {state.last_validation_exit_code}")
    if wake_info.get("armed"):
        alive = "yes" if wake_info.get("pid_alive") else "no"
        print(
            f"  Wake service: armed gen={wake_info.get('token_prefix', '?')} "
            f"alive={alive} interval_s={wake_info.get('interval_s')}"
        )
    else:
        print("  Wake service: not armed")
    if state.last_reason:
        print(f"  Last evaluation: {state.last_reason}")
    if state.last_eval_verdict:
        print(f"  Last verdict: {state.last_eval_verdict}")
    print(f"  Created: {state.created_at}")
    return 0


def cmd_pause(_argv: list[str]) -> int:
    insecure = refuse_if_data_dir_insecure()
    if insecure is not None:
        print(insecure, file=sys.stderr)
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
    if state is None:
        print("[goal] No active goal to mark done.")
        return 1
    wake_disarm(kill_loop=True)
    print(f"[goal] Goal achieved in {state.turns_used} turns: {state.condition}")
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


def _hooks_look_configured() -> bool | None:
    """Return True/False if detectable; None if unknown."""
    hooks = Path.home() / ".cursor" / "hooks.json"
    if hooks.is_file():
        try:
            text = hooks.read_text(encoding="utf-8")
        except OSError:
            return None
        return "stop" in text and (
            "stop_hook" in text or "cursor_goal" in text or "run_goal" in text
        )
    # Classic skill install ships scripts even if hooks merge failed.
    skill_hook = (
        Path.home() / ".cursor" / "skills" / "goal" / "scripts" / "stop_hook.py"
    )
    if skill_hook.is_file():
        return False
    return None


# pylint: disable-next=too-many-branches,too-many-statements
def cmd_doctor(_argv: list[str]) -> int:
    """Health check for install / data dir / wake / shell. Exit 1 on hard fail."""
    hard_fails: list[str] = []
    warnings: list[str] = []

    print("[goal] Doctor")
    print(f"  Python: {sys.version.split()[0]} ({sys.executable})")
    if sys.version_info < (3, 12):
        hard_fails.append("Python 3.12+ is required")

    try:
        ddir = data_dir(check_writable=False)
        print(f"  Data dir: {ddir}")
    except OSError as exc:
        hard_fails.append(f"Cannot access data dir: {exc}")
        ddir = None

    if ddir is not None and data_dir_is_insecure(ddir):
        hard_fails.append(
            f"Data directory is insecure ({ddir}). "
            "It must not be a symlink, must be owned by you, and must not be "
            "group/world-writable. Run chmod 700 or set CURSOR_GOAL_DATA."
        )

    acl_fail = acl_harden_failure_message(ddir) if ddir is not None else None
    if acl_fail is not None:
        hard_fails.append(acl_fail)

    hooks_state = _hooks_look_configured()
    if hooks_state is True:
        print("  Hooks: stop hook appears configured (~/.cursor/hooks.json)")
    elif hooks_state is False:
        hard_fails.append(
            "Goal skill scripts present but ~/.cursor/hooks.json has no stop hook. "
            "Re-run the installer."
        )
    else:
        warnings.append(
            "Could not confirm stop hook configuration "
            "(~/.cursor/hooks.json missing or unreadable)"
        )

    if os.name == "nt":
        py = shutil.which("py") or shutil.which("python") or shutil.which("python3")
        if not py:
            warnings.append(
                "No py/python/python3 on PATH — marketplace stop_hook.cmd may fail"
            )
        else:
            print(f"  PATH Python: {py}")

    try:
        state = snapshot_goal(raise_corrupt=True)
    except CorruptGoalError as exc:
        hard_fails.append(f"Corrupt goal.json: {exc}")
        state = None
    except GoalLockTimeoutError as exc:
        hard_fails.append(f"goal.lock timeout: {exc}")
        state = None

    wake_info = wake_status_info()
    if state is not None and state.active and state.status == "pursuing":
        print(f"  Goal: pursuing ({state.condition[:60]})")
        print(
            f"  Budgets: turns {state.turns_used}/{state.turn_budget}, "
            f"wake {state.wake_ticks}/{state.wake_budget}"
        )
        mode = _validation_mode(state)
        print(f"  Validation mode: {mode}")
        if mode == "shell":
            warnings.append(
                "Shell-mode validation active (trusted-user). "
                "Prefer argv or CURSOR_GOAL_DENY_SHELL=1 / --deny-shell."
            )
        if not wake_info.get("armed"):
            warnings.append(
                "Wake not armed — immediately start background Shell: "
                f"`{_wake_loop_shell_hint()}` with notify_on_output "
                "matching ^AGENT_GOAL_WAKE (required for automatic continuation)"
            )
        elif not wake_info.get("pid_alive"):
            warnings.append(
                "Wake armed but loop not alive — immediately start: "
                f"`{_wake_loop_shell_hint()}` with notify_on_output "
                "matching ^AGENT_GOAL_WAKE"
            )
    elif state is None:
        print("  Goal: none")
    else:
        print(f"  Goal: {state.status}")

    if wake_info.get("armed"):
        print(
            f"  Wake: armed interval_s={wake_info.get('interval_s')} "
            f"alive={wake_info.get('pid_alive')} "
            f"last_emit={wake_info.get('last_emit_at') or 'never'}"
        )
    else:
        print("  Wake: not armed")

    last_stop = (ddir / "last-stop-response.json") if ddir is not None else None
    if last_stop is not None and last_stop.is_file():
        print(f"  Last stop response: {last_stop}")
        print(
            "  Tip: If Hooks UI shows {{}} but this file has followup_message, "
            "Cursor dropped stdout (known race) — rely on wake."
        )
    else:
        warnings.append(
            "No last-stop-response.json yet (normal before first stop emit)"
        )

    fail_open = (ddir / "stop-failopen-continues") if ddir is not None else None
    if fail_open is not None and fail_open.is_file():
        try:
            count = int(fail_open.read_text(encoding="utf-8").strip() or "0")
        except (OSError, ValueError):
            count = -1
        if count > 0:
            warnings.append(
                f"Stop fail-open continue counter is {count} "
                "(persist failures while pursuing — wake should still continue)"
            )

    if os.environ.get("CURSOR_GOAL_LOG_SECRETS", "").strip().lower() in {
        "1",
        "true",
        "yes",
        "on",
    }:
        warnings.append("CURSOR_GOAL_LOG_SECRETS is enabled")

    log_file = os.environ.get("CURSOR_GOAL_LOG_FILE", "").strip()
    if log_file:
        print(f"  Durable log: CURSOR_GOAL_LOG_FILE={log_file}")

    for item in warnings:
        print(f"  Warning: {item}")
    for item in hard_fails:
        print(f"  FAIL: {item}", file=sys.stderr)

    if hard_fails:
        print("[goal] Doctor: FAILED", file=sys.stderr)
        return 1
    if warnings:
        print("[goal] Doctor: OK (with warnings)")
    else:
        print("[goal] Doctor: OK")
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
    hint = _wake_loop_shell_hint()
    print(
        f"[goal] Wake armed (every {config['interval_s']}s). "
        "REQUIRED next step: start `wake loop` in a background Shell with "
        f"notify_on_output matching {config['notify_pattern']}:"
    )
    print(f"  {hint}")


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
        "[--wake-budget <N>] [--deny-shell] [--force]"
    )
    print("  status     Show current goal state")
    print("  doctor     Install / health diagnostics")
    print("  pause      Pause auto-continuation")
    print("  resume     Resume a paused goal")
    print("  done       Mark goal as achieved (requires YES-bound eval signal)")
    print("  clear      Remove goal entirely")
    return 0
