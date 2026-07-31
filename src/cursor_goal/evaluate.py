"""Evaluator prompt generation, signaling, validation, and YES/NO parsing."""

from __future__ import annotations

import json
import re
import sys
from collections.abc import Callable

from cursor_goal.logging_config import get_logger
from cursor_goal.models import spawn_config_dict
from cursor_goal.state import (
    clear_eval_signal,
    has_eval_signal,
    load_goal,
    set_eval_signal,
    update_goal_fields,
)
from cursor_goal.validation import redact_command, run_validation

logger = get_logger("cursor_goal.eval")

_VERDICT_LINE = re.compile(r"^(YES|NO):\s*(.*)$", re.IGNORECASE)


def cmd_prompt(argv: list[str]) -> int:
    state = load_goal()
    if state is None:
        print(
            "[goal-eval] Error: No active goal. Run cursor-goal manage create first.",
            file=sys.stderr,
        )
        return 1

    work_summary = ""
    i = 0
    while i < len(argv):
        if argv[i] == "--work-summary" and i + 1 < len(argv):
            work_summary = argv[i + 1]
            i += 2
        else:
            i += 1

    if state.last_validation_output:
        exit_note = ""
        if state.last_validation_exit_code is not None:
            passed = state.last_validation_exit_code == 0
            exit_note = (
                f"\nExit code: {state.last_validation_exit_code} "
                f"({'passed' if passed else 'failed'})"
            )
        validation_section = (
            f"Validation command: {state.validation_command}{exit_note}\n"
            f"Output:\n{state.last_validation_output}"
        )
    elif state.validation_command:
        validation_section = (
            f"Validation command ({state.validation_command}) has not been run yet."
        )
    else:
        validation_section = "No validation command configured."

    if work_summary:
        work_section = f"Recent work summary:\n{work_summary}"
    else:
        work_section = (
            "(No work summary provided — evaluate based on validation output "
            "and any available evidence.)"
        )

    prompt = (
        "You are a goal completion evaluator (checker), not the worker "
        "(maker). Judge whether the goal condition has been achieved based "
        "on the evidence provided below — validation output and work summary.\n"
        "\n"
        f"Goal condition: {state.condition}\n"
        "\n"
        f"{validation_section}\n"
        "\n"
        f"{work_section}\n"
        "\n"
        "Rules:\n"
        "1. Answer ONLY with 'YES: <reason>' or 'NO: <reason>' as the final line\n"
        "2. Be conservative — only YES when there is clear evidence\n"
        "3. If validation command passed (exit 0), that is strong evidence\n"
        "4. Keep reason to 1-2 sentences\n"
        "5. For NO, explain what specific work remains\n"
        "6. Prefer the evidence in this prompt; do not invent unstated results\n"
    )
    _emit_prompt(prompt)
    return 0


def cmd_spawn_config(_argv: list[str]) -> int:
    """Print JSON Task parameters for the readonly goal evaluator."""
    config = spawn_config_dict()
    logger.info(
        "spawn-config subagent_type=%s model=%s readonly=%s",
        config["subagent_type"],
        config["model"],
        config["readonly"],
    )
    print(json.dumps(config, separators=(",", ":")))
    return 0


def _emit_prompt(prompt: str) -> None:
    """Write evaluator prompt to stdout, ensuring a trailing newline."""
    sys.stdout.write(prompt)
    if not prompt.endswith("\n"):
        sys.stdout.write("\n")
    sys.stdout.flush()


def cmd_validate(_argv: list[str]) -> int:
    """Run the goal's validation command and persist output for eval prompts."""
    state = load_goal()
    if state is None:
        print(
            "[goal-eval] Error: No active goal. Run cursor-goal manage create first.",
            file=sys.stderr,
        )
        return 1
    if not state.validation_command.strip():
        print(
            "[goal-eval] Error: No validation command configured for this goal.",
            file=sys.stderr,
        )
        return 1

    cmd = state.validation_command.strip()
    logger.info("eval validate cmd=%r", redact_command(cmd))
    logger.warning(
        "Running trusted-user validation_command from goal.json "
        "(~/.cursor-goal/data is shell-equivalent trust)"
    )
    result = run_validation(cmd)
    output = result.output
    if result.timed_out:
        output = f"[timed out]\n{output}".strip()

    updated = update_goal_fields(
        last_validation_output=output,
        last_validation_exit_code=result.exit_code,
    )
    if updated is None:
        print(
            "[goal-eval] Error: Failed to persist validation output.", file=sys.stderr
        )
        return 1

    print(f"[goal-eval] Validation exit={result.exit_code}")
    if result.timed_out:
        print("[goal-eval] Validation timed out.")
    if output:
        print(output)
    return 0 if result.exit_code == 0 and not result.timed_out else 1


def cmd_signal(argv: list[str]) -> int:
    """Record YES-bound signal.

    Prefer parse-result auto-signal; use --force for recovery.
    """
    state = load_goal()
    if state is None:
        print(
            "[goal-eval] Error: No active goal. Run cursor-goal manage create first.",
            file=sys.stderr,
        )
        return 1

    force = "--force" in argv
    if state.last_eval_verdict.upper() != "YES" and not force:
        print(
            "[goal-eval] Error: No YES verdict from parse-result. "
            'Run: cursor-goal eval parse-result "YES: ..." '
            "(or use eval signal --force for recovery).",
            file=sys.stderr,
        )
        return 1

    reason = state.last_reason if state.last_eval_verdict.upper() == "YES" else ""
    if force and state.last_eval_verdict.upper() != "YES":
        logger.warning(
            "eval signal --force without YES parse-result "
            "(recovery bypass — not cryptographic attestation)"
        )
    set_eval_signal(verdict="YES", reason=reason)
    print("[goal-eval] Evaluator signal recorded.")
    return 0


def cmd_check(_argv: list[str]) -> int:
    if has_eval_signal():
        print("[goal-eval] OK: Evaluator has run for this cycle.")
        return 0
    print("[goal-eval] FAIL: No evaluator signal for this cycle.")
    print("[goal-eval] You must spawn an evaluator subagent before marking done.")
    return 1


def parse_result_text(result: str) -> tuple[str, str]:
    """Return (verdict, reason). Verdict is YES, NO, or UNCLEAR.

    Only the last non-empty line matching YES:/NO: counts, so mid-response
    prose cannot false-complete the goal.
    """
    last_match: re.Match[str] | None = None
    for line in result.splitlines():
        stripped = line.strip()
        if not stripped:
            continue
        match = _VERDICT_LINE.match(stripped)
        if match:
            last_match = match
    if last_match is None:
        return (
            "UNCLEAR",
            "Could not parse evaluator response. Treat as NO and re-evaluate.",
        )
    verdict = last_match.group(1).upper()
    reason = last_match.group(2).strip()
    return verdict, reason


def cmd_parse_result(argv: list[str]) -> int:
    if not argv or not argv[0]:
        print(
            "[goal-eval] Error: Usage: "
            'cursor-goal eval parse-result "<subagent output>"',
            file=sys.stderr,
        )
        return 1

    result = argv[0]
    verdict, reason = parse_result_text(result)
    logger.info("parse-result verdict=%s reason=%r", verdict, reason)

    if load_goal() is not None:
        update_goal_fields(last_reason=reason, last_eval_verdict=verdict)
        if verdict == "YES":
            set_eval_signal(verdict="YES", reason=reason)
            print("[goal-eval] YES signal recorded automatically.")
        else:
            clear_eval_signal()

    print(f"VERDICT={verdict}")
    print(f"REASON={reason}")
    return 0 if verdict == "YES" else 1


def cmd_eval(argv: list[str]) -> int:
    if not argv:
        _print_help()
        return 1
    command = argv[0]
    rest = argv[1:]
    if command == "help":
        return _print_help()
    dispatch: dict[str, Callable[[list[str]], int]] = {
        "prompt": cmd_prompt,
        "validate": cmd_validate,
        "spawn-config": cmd_spawn_config,
        "signal": cmd_signal,
        "check": cmd_check,
        "parse-result": cmd_parse_result,
    }
    handler = dispatch.get(command)
    if handler is None:
        _print_help()
        return 1
    return handler(rest)


def _print_help() -> int:
    print("Usage: cursor-goal eval <command> [args...]")
    print('  prompt [--work-summary "..."]    Generate evaluator prompt from goal.json')
    print("  validate                          Run validation_command; persist output")
    print(
        "  spawn-config                      "
        "Print JSON Task params (subagent_type/model/readonly)"
    )
    print(
        "  signal [--force]                  "
        "Record YES-bound signal (prefer parse-result)"
    )
    print("  check                             Verify evaluator ran (exit 0/1)")
    print('  parse-result "<output>"           Parse YES/NO; auto-signal on YES')
    return 0


__all__ = [
    "cmd_eval",
    "cmd_prompt",
    "cmd_validate",
    "cmd_spawn_config",
    "cmd_signal",
    "cmd_check",
    "cmd_parse_result",
    "parse_result_text",
]
