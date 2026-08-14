"""Evaluator prompt generation, signaling, validation, and YES/NO parsing."""

from __future__ import annotations

import json
import re
import sys
from collections.abc import Callable
from pathlib import Path

from cursor_goal.logging_config import get_logger
from cursor_goal.models import audit_spawn_config_dict, spawn_config_dict
from cursor_goal.state import (
    CorruptGoalError,
    GoalLockTimeoutError,
    GoalState,
    assert_workdir_usable,
    data_dir,
    has_audit_signal,
    has_eval_signal,
    record_parse_audit,
    record_parse_result,
    refuse_if_acl_harden_failed,
    refuse_if_data_dir_insecure,
    set_eval_signal,
    snapshot_goal,
    update_goal_fields,
)
from cursor_goal.validation import (
    redact_command,
    redact_secrets,
    resolve_validation_timeout_sec,
    run_validation,
)
from cursor_goal.wake import refuse_if_wake_dead, wake_dead_warning

logger = get_logger("cursor_goal.eval")

_VERDICT_LINE = re.compile(r"^(YES|NO):\s*(.*)$", re.IGNORECASE)
_AUDIT_LINE = re.compile(r"^(CLEAR|REMAINING):\s*(.*)$", re.IGNORECASE)
MAX_PARSE_RESULT_BYTES = 2 * 1024 * 1024
MISSING_VALIDATION_EVIDENCE = (
    "MISSING EVIDENCE: a validation command is configured but has not been "
    "run this cycle. You MUST answer NO. Do not infer success from the work "
    "summary."
)
MISSING_AUDIT_CLEAR = (
    "Remaining-work audit: not CLEAR this cycle. If the goal condition is "
    "broader than the validation command (or no validation command is set), "
    "you MUST answer NO until a CLEAR remaining-work audit exists for this "
    "cycle. Work summary is not a substitute."
)


def _refuse_if_data_dir_unsafe() -> str | None:
    """Combined insecure-dir / Windows-ACL-harden-failure gate.

    Every ``eval`` entry point needs both checks before touching the data
    dir; keep the two-step preamble in one place instead of repeating it
    at each call site.
    """
    insecure = refuse_if_data_dir_insecure()
    if insecure is not None:
        return insecure
    return refuse_if_acl_harden_failed()


def _check_wake() -> int | None:
    """Print wake-dead diagnostics; return an exit code only in strict mode.

    Default (non-strict) behavior prints a loud warning and returns None so
    the caller continues — wake is a best-effort watchdog, not a requirement.
    Set ``CURSOR_GOAL_REQUIRE_WAKE=1`` to make this block with exit 1.
    """
    hard = refuse_if_wake_dead()
    if hard is not None:
        print(hard.replace("[goal]", "[goal-eval]"), file=sys.stderr)
        return 1
    warning = wake_dead_warning()
    if warning is not None:
        print(warning.replace("[goal]", "[goal-eval]"), file=sys.stderr)
    return None


def _extract_work_summary(argv: list[str]) -> str:
    """Pull ``--work-summary <text>`` out of *argv*, redacted and truncated."""
    i = 0
    while i < len(argv):
        if argv[i] == "--work-summary" and i + 1 < len(argv):
            return redact_secrets(argv[i + 1], max_chars=4000)
        i += 1
    return ""


def validation_evidence_missing(state: GoalState) -> bool:
    """True when a validation command exists but has never been run.

    ``last_validation_exit_code is None`` is the signal — empty output with
    a recorded exit code still counts as a run (for example a silent
    ``exit 0``).
    """
    has_command = bool(state.validation_command.strip())
    return has_command and state.last_validation_exit_code is None


def _build_validation_section(state: GoalState) -> str:
    """Render the validation-command evidence block for the eval prompt."""
    safe_cmd = (
        redact_command(state.validation_command) if state.validation_command else ""
    )
    if state.last_validation_exit_code is not None:
        passed = state.last_validation_exit_code == 0
        exit_note = (
            f"\nExit code: {state.last_validation_exit_code} "
            f"({'passed' if passed else 'failed'})"
        )
        safe_output = redact_secrets(state.last_validation_output, max_chars=4000)
        logger.info(
            "eval prompt validation evidence present exit=%s output_len=%s",
            state.last_validation_exit_code,
            len(state.last_validation_output or ""),
        )
        return f"Validation command: {safe_cmd}{exit_note}\n" f"Output:\n{safe_output}"
    if validation_evidence_missing(state):
        logger.info(
            "eval prompt missing validation evidence cmd=%s",
            safe_cmd,
        )
        return (
            f"Validation command ({safe_cmd}) has not been run yet.\n"
            f"{MISSING_VALIDATION_EVIDENCE}"
        )
    logger.debug("eval prompt: no validation command configured")
    return "No validation command configured."


def _build_audit_section(state: GoalState) -> str:
    """Render remaining-work audit evidence for the evaluator prompt."""
    if has_audit_signal():
        last = state.last_audit_verdict or "CLEAR"
        logger.info("eval prompt remaining-work audit CLEAR last=%s", last)
        return "Remaining-work audit: CLEAR this cycle.\n" f"Last audit verdict: {last}"
    last = state.last_audit_verdict or "none"
    logger.info("eval prompt remaining-work audit not CLEAR last=%s", last)
    return (
        f"Remaining-work audit: not CLEAR this cycle "
        f"(last_audit_verdict={last}).\n"
        f"{MISSING_AUDIT_CLEAR}"
    )


def cmd_prompt(argv: list[str]) -> int:
    wake_code = _check_wake()
    if wake_code is not None:
        return wake_code

    try:
        state = snapshot_goal(raise_corrupt=True)
    except CorruptGoalError as exc:
        print(f"[goal-eval] Error: {exc}", file=sys.stderr)
        return 1
    if state is None:
        print(
            "[goal-eval] Error: No active goal. Run cursor-goal manage create first.",
            file=sys.stderr,
        )
        return 1

    work_summary = _extract_work_summary(argv)
    validation_section = _build_validation_section(state)
    audit_section = _build_audit_section(state)

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
        "on the evidence provided below — validation output, remaining-work "
        "audit, and work summary. Prefer inspecting the workspace over "
        "trusting the work summary.\n"
        "\n"
        f"Goal condition: {redact_secrets(state.condition, max_chars=None)}\n"
        "\n"
        f"{validation_section}\n"
        "\n"
        f"{audit_section}\n"
        "\n"
        f"{work_section}\n"
        "\n"
        "Rules:\n"
        "1. Answer ONLY with 'YES: <reason>' or 'NO: <reason>' as the final line\n"
        "2. Be conservative — only YES when there is clear evidence\n"
        "3. Validation command exit 0 is necessary evidence that the command "
        "passed, but is NOT sufficient when the goal condition is broader "
        "than that command. Treat the worker summary as a claim, not proof.\n"
        "4. Keep reason to 1-2 sentences\n"
        "5. For NO, explain what specific work remains\n"
        "6. Prefer the evidence in this prompt; do not invent unstated results\n"
        "7. If a validation command is configured but has not been run "
        "(MISSING EVIDENCE / has not been run yet), you MUST answer NO. "
        "Work summary is not a substitute for a validation run.\n"
        "8. If the remaining-work audit is not CLEAR this cycle and the "
        "condition is broader than the validation command (or no validation "
        "command is set), you MUST answer NO.\n"
    )
    _emit_prompt(prompt)
    return 0


def cmd_spawn_config(_argv: list[str]) -> int:
    """Print JSON Task parameters for the readonly goal evaluator."""
    wake_code = _check_wake()
    if wake_code is not None:
        return wake_code
    config = spawn_config_dict()
    logger.info(
        "spawn-config subagent_type=%s model=%s readonly=%s",
        config["subagent_type"],
        config["model"],
        config["readonly"],
    )
    print(json.dumps(config, separators=(",", ":")))
    return 0


def cmd_audit_spawn_config(_argv: list[str]) -> int:
    """Print JSON Task parameters for the readonly remaining-work auditor."""
    wake_code = _check_wake()
    if wake_code is not None:
        return wake_code
    config = audit_spawn_config_dict()
    logger.info(
        "audit-spawn-config subagent_type=%s model=%s readonly=%s",
        config["subagent_type"],
        config["model"],
        config["readonly"],
    )
    print(json.dumps(config, separators=(",", ":")))
    return 0


def cmd_audit_prompt(_argv: list[str]) -> int:
    """Generate a remaining-work auditor prompt (condition only, no summary)."""
    wake_code = _check_wake()
    if wake_code is not None:
        return wake_code

    try:
        state = snapshot_goal(raise_corrupt=True)
    except CorruptGoalError as exc:
        print(f"[goal-eval] Error: {exc}", file=sys.stderr)
        return 1
    if state is None:
        print(
            "[goal-eval] Error: No active goal. Run cursor-goal manage create first.",
            file=sys.stderr,
        )
        return 1

    prompt = (
        "You are a remaining-work auditor (fresh chat), not the worker and "
        "not the YES/NO evaluator. Inspect the workspace as a new plan-mode "
        "session would against the original goal condition. Do not trust any "
        "worker claim that this is done. Use readonly tools to inspect the "
        "repository. Do not edit files.\n"
        "\n"
        f"Goal condition: {redact_secrets(state.condition, max_chars=None)}\n"
        "\n"
        "Rules:\n"
        "1. Answer ONLY with 'CLEAR: <reason>' or 'REMAINING: <items>' as "
        "the final line\n"
        "2. CLEAR only when a new plan-mode chat would not produce in-scope "
        "remaining work required by the condition\n"
        "3. On REMAINING, list concrete file + issue items; no style nits, "
        "extra features, or a second quality bar the condition does not ask "
        "for\n"
        "4. If the condition is equivalent to a test/validation command and "
        "that command meets it, answer CLEAR\n"
        "5. Validation passing is not enough when the condition is broader "
        "than the test command\n"
        "6. There is no work summary — inspect the tree yourself\n"
    )
    _emit_prompt(prompt)
    return 0


def _emit_prompt(prompt: str) -> None:
    """Write evaluator prompt to stdout, ensuring a trailing newline."""
    sys.stdout.write(prompt)
    if not prompt.endswith("\n"):
        sys.stdout.write("\n")
    sys.stdout.flush()


def _load_validate_state() -> GoalState | None:
    """Load the active goal and confirm it has a validation command.

    Prints a ``[goal-eval] Error: ...`` diagnostic and returns ``None`` for
    every failure mode; callers only need to check for ``None``.
    """
    try:
        state = snapshot_goal(raise_corrupt=True)
    except (CorruptGoalError, GoalLockTimeoutError) as exc:
        print(f"[goal-eval] Error: {exc}", file=sys.stderr)
        return None
    if state is None:
        print(
            "[goal-eval] Error: No active goal. Run cursor-goal manage create first.",
            file=sys.stderr,
        )
        return None
    if not state.validation_command.strip():
        print(
            "[goal-eval] Error: No validation command configured for this goal.",
            file=sys.stderr,
        )
        return None
    return state


def _resolve_validate_cwd(state: GoalState) -> tuple[str | None, bool]:
    """Resolve the validation working directory.

    Returns ``(cwd, ok)``; on failure ``ok`` is ``False`` and an error has
    already been printed to stderr.
    """
    if not state.workdir.strip():
        return None, True
    try:
        cwd = assert_workdir_usable(state.workdir)
        logger.info("eval validate workdir=%s", cwd)
        return cwd, True
    except ValueError as exc:
        print(f"[goal-eval] Error: {exc}", file=sys.stderr)
        return None, False


def cmd_validate(_argv: list[str]) -> int:
    """Run the goal's validation command and persist output for eval prompts."""
    unsafe = _refuse_if_data_dir_unsafe()
    if unsafe is not None:
        print(unsafe.replace("[goal]", "[goal-eval]"), file=sys.stderr)
        return 1
    wake_code = _check_wake()
    if wake_code is not None:
        return wake_code

    state = _load_validate_state()
    if state is None:
        return 1

    cmd = state.validation_command.strip()
    logger.info("eval validate cmd=%r", redact_command(cmd))
    logger.warning(
        "Running trusted-user validation_command from goal.json "
        "(~/.cursor-goal/data is shell-equivalent trust)"
    )
    cwd, cwd_ok = _resolve_validate_cwd(state)
    if not cwd_ok:
        return 1
    result = run_validation(
        cmd,
        shell_ok=bool(state.shell_ok),
        cwd=cwd,
        timeout_sec=resolve_validation_timeout_sec(),
    )
    output = result.output
    if result.timed_out:
        output = f"[timed out]\n{output}".strip()
    # Persist and print a redacted copy so agent/terminal context does not
    # leak secrets (same scrubbing as stored output).
    stored_output = redact_secrets(output, max_chars=4000)

    try:
        updated = update_goal_fields(
            last_validation_output=stored_output,
            last_validation_exit_code=result.exit_code,
        )
    except GoalLockTimeoutError as exc:
        print(f"[goal-eval] Error: {exc}", file=sys.stderr)
        return 1
    if updated is None:
        print(
            "[goal-eval] Error: Failed to persist validation output.", file=sys.stderr
        )
        return 1

    print(f"[goal-eval] Validation exit={result.exit_code}")
    if result.timed_out:
        print("[goal-eval] Validation timed out.")
    if stored_output:
        print(stored_output)
    return 0 if result.exit_code == 0 and not result.timed_out else 1


def cmd_signal(argv: list[str]) -> int:
    """Record YES-bound signal.

    Prefer parse-result auto-signal; use --force for recovery.
    """
    unsafe = _refuse_if_data_dir_unsafe()
    if unsafe is not None:
        print(unsafe.replace("[goal]", "[goal-eval]"), file=sys.stderr)
        return 1

    try:
        state = snapshot_goal(raise_corrupt=True)
    except CorruptGoalError as exc:
        print(f"[goal-eval] Error: {exc}", file=sys.stderr)
        return 1
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
            "Run: cursor-goal eval parse-result --stdin "
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
        print(
            "[goal-eval] Warning: --force bypasses maker≠checker protocol "
            "(not cryptographic attestation).",
            file=sys.stderr,
        )
    try:
        set_eval_signal(verdict="YES", reason=reason)
    except GoalLockTimeoutError as exc:
        print(f"[goal-eval] Error: {exc}", file=sys.stderr)
        return 1
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


def parse_audit_text(result: str) -> tuple[str, str]:
    """Return (verdict, reason). Verdict is CLEAR, REMAINING, or UNCLEAR.

    Only the last non-empty line matching CLEAR:/REMAINING: counts.
    """
    last_match: re.Match[str] | None = None
    for line in result.splitlines():
        stripped = line.strip()
        if not stripped:
            continue
        match = _AUDIT_LINE.match(stripped)
        if match:
            last_match = match
    if last_match is None:
        return (
            "UNCLEAR",
            "Could not parse auditor response. Treat as REMAINING and continue.",
        )
    verdict = last_match.group(1).upper()
    reason = last_match.group(2).strip()
    return verdict, reason


def _usage_parse_result() -> None:
    _usage_parse_input("parse-result")


def _usage_parse_input(command: str) -> None:
    print(
        "[goal-eval] Error: Usage: "
        f'cursor-goal eval {command} "<output>" | --stdin | @file '
        "[--allow-cwd]",
        file=sys.stderr,
    )


def _path_is_under(path: Path, root: Path) -> bool:
    """Return True when *path* is *root* or a descendant (after resolve)."""
    try:
        path.relative_to(root)
        return True
    except ValueError:
        return False


def _resolve_parse_result_path(raw: str, *, allow_cwd: bool = False) -> Path | None:
    """Resolve @file path; allow only under data dir unless --allow-cwd."""
    try:
        path = Path(raw).expanduser().resolve()
    except OSError as exc:
        print(f"[goal-eval] Error: could not resolve path: {exc}", file=sys.stderr)
        return None
    allowed: list[Path] = [data_dir(check_writable=False).resolve()]
    if allow_cwd:
        allowed.append(Path.cwd().resolve())
    if any(_path_is_under(path, root) for root in allowed):
        return path
    where = "the goal data directory"
    if allow_cwd:
        where = "the goal data directory or the current working directory"
    print(
        f"[goal-eval] Error: @file path must be under {where}.",
        file=sys.stderr,
    )
    return None


def _read_bytes_capped(path: Path) -> str | None:
    try:
        data = path.read_bytes()
    except OSError as exc:
        print(f"[goal-eval] Error: could not read {path}: {exc}", file=sys.stderr)
        return None
    if len(data) > MAX_PARSE_RESULT_BYTES:
        print(
            f"[goal-eval] Error: file exceeds {MAX_PARSE_RESULT_BYTES} bytes",
            file=sys.stderr,
        )
        return None
    return data.decode("utf-8", errors="replace")


def _read_stdin_capped() -> str | None:
    try:
        raw = sys.stdin.read(MAX_PARSE_RESULT_BYTES + 1)
    except OSError as exc:
        print(f"[goal-eval] Error: failed to read stdin: {exc}", file=sys.stderr)
        return None
    if len(raw) > MAX_PARSE_RESULT_BYTES:
        print(
            f"[goal-eval] Error: stdin exceeds {MAX_PARSE_RESULT_BYTES} bytes",
            file=sys.stderr,
        )
        return None
    return raw


def _read_parse_result_text(
    argv: list[str], *, command: str = "parse-result"
) -> str | None:
    """Resolve parse-result input from argv, --stdin, or @file.

    Returns the text, or None after printing a usage error.
    """
    allow_cwd = "--allow-cwd" in argv
    filtered = [a for a in argv if a != "--allow-cwd"]
    if not filtered or not filtered[0]:
        _usage_parse_input(command)
        return None
    if filtered[0] == "--stdin":
        return _read_stdin_capped()
    if filtered[0].startswith("@") and len(filtered[0]) > 1:
        path = _resolve_parse_result_path(filtered[0][1:], allow_cwd=allow_cwd)
        if path is None:
            return None
        return _read_bytes_capped(path)
    return filtered[0]


def cmd_parse_result(argv: list[str]) -> int:
    unsafe = _refuse_if_data_dir_unsafe()
    if unsafe is not None:
        print(unsafe.replace("[goal]", "[goal-eval]"), file=sys.stderr)
        return 1

    result = _read_parse_result_text(argv)
    if result is None:
        return 1

    verdict, reason = parse_result_text(result)
    # Avoid logging raw evaluator reasons at INFO (may contain secrets).
    logger.info("parse-result verdict=%s reason_len=%s", verdict, len(reason))

    try:
        updated = record_parse_result(verdict, reason)
    except ValueError as exc:
        print(f"[goal-eval] Error: {exc}", file=sys.stderr)
        return 1
    except GoalLockTimeoutError as exc:
        print(f"[goal-eval] Error: {exc}", file=sys.stderr)
        return 1

    if updated is not None and verdict == "YES":
        print("[goal-eval] YES signal recorded automatically.")

    print(f"VERDICT={verdict}")
    print(f"REASON={redact_secrets(reason, max_chars=500)}")
    return 0 if verdict == "YES" else 1


def cmd_parse_audit(argv: list[str]) -> int:
    unsafe = _refuse_if_data_dir_unsafe()
    if unsafe is not None:
        print(unsafe.replace("[goal]", "[goal-eval]"), file=sys.stderr)
        return 1

    result = _read_parse_result_text(argv, command="parse-audit")
    if result is None:
        return 1

    verdict, reason = parse_audit_text(result)
    logger.info("parse-audit verdict=%s reason_len=%s", verdict, len(reason))

    try:
        updated = record_parse_audit(verdict, reason)
    except ValueError as exc:
        print(f"[goal-eval] Error: {exc}", file=sys.stderr)
        return 1
    except GoalLockTimeoutError as exc:
        print(f"[goal-eval] Error: {exc}", file=sys.stderr)
        return 1

    if updated is not None and verdict == "CLEAR":
        print("[goal-eval] CLEAR remaining-work audit signal recorded automatically.")

    print(f"VERDICT={verdict}")
    print(f"REASON={redact_secrets(reason, max_chars=500)}")
    return 0 if verdict == "CLEAR" else 1


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
        "audit-prompt": cmd_audit_prompt,
        "audit-spawn-config": cmd_audit_spawn_config,
        "signal": cmd_signal,
        "check": cmd_check,
        "parse-result": cmd_parse_result,
        "parse-audit": cmd_parse_audit,
    }
    handler = dispatch.get(command)
    if handler is None:
        print(f"[goal-eval] Error: unknown eval command: {command}", file=sys.stderr)
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
    print("  audit-prompt                      Generate remaining-work auditor prompt")
    print(
        "  audit-spawn-config                "
        "Print JSON Task params for goal-auditor (inherit/readonly)"
    )
    print(
        "  signal [--force]                  "
        "Record YES-bound signal (prefer parse-result)"
    )
    print("  check                             Verify evaluator ran (exit 0/1)")
    print(
        '  parse-result "<output>"|--stdin|@file [--allow-cwd]  '
        "Parse YES/NO; auto-signal on YES (prefer --stdin on Windows)"
    )
    print(
        '  parse-audit "<output>"|--stdin|@file [--allow-cwd]  '
        "Parse CLEAR/REMAINING; auto-signal on CLEAR"
    )
    return 0


__all__ = [
    "MISSING_AUDIT_CLEAR",
    "MISSING_VALIDATION_EVIDENCE",
    "cmd_eval",
    "cmd_prompt",
    "cmd_validate",
    "cmd_spawn_config",
    "cmd_audit_prompt",
    "cmd_audit_spawn_config",
    "cmd_signal",
    "cmd_check",
    "cmd_parse_result",
    "cmd_parse_audit",
    "parse_result_text",
    "parse_audit_text",
    "validation_evidence_missing",
]
