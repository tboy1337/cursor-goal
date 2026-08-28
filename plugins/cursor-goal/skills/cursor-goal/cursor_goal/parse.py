"""Parse natural-language /cursor-goal (or leftover /goal) input into JSON."""

from __future__ import annotations

import json
import re
import sys
from typing import Any

from cursor_goal.logging_config import get_logger
from cursor_goal.state import MAX_TURN_BUDGET, clamp_turn_budget, clamp_wake_budget
from cursor_goal.validation import redact_command, weak_condition_warning

logger = get_logger("cursor_goal.parse")

SUBCOMMANDS = frozenset(
    {"status", "pause", "resume", "clear", "stop", "off", "reset", "cancel"}
)
CLEAR_ALIASES = frozenset({"stop", "off", "reset", "cancel"})

KNOWN_RUNNERS = (
    "npm",
    "yarn",
    "pnpm",
    "make",
    "cargo",
    "go",
    "python",
    "pytest",
    "jest",
    "vitest",
    "rspec",
    "mix",
    "dotnet",
    "gradle",
    "mvn",
)

_BUDGET_NL = re.compile(
    r",?\s*(?:stop|limit|max)\s+(?:after\s+|to\s+)?(\d+)\s*"
    r"(?:turns?|iterations?|cycles?|rounds?)",
    re.IGNORECASE,
)
_TEST_FLAG_QUOTED = re.compile(r'--test\s+"([^"]+)"')
_TEST_FLAG_BARE = re.compile(r"--test\s+(\S+)")
_BUDGET_FLAG = re.compile(r"--budget\s+(\d+)")
_WAKE_BUDGET_FLAG = re.compile(r"--wake-budget\s+(\d+)")
_WORKDIR_QUOTED = re.compile(r'--workdir\s+"([^"]+)"')
_WORKDIR_BARE = re.compile(r"--workdir\s+(\S+)")
_ALLOW_SHELL_FLAG = re.compile(r"--allow-shell\b")
_DENY_SHELL_FLAG = re.compile(r"--deny-shell\b")
_FORCE_FLAG = re.compile(r"--force\b")
_VALIDATION_HINTS = [
    re.compile(
        rf",?\s*(?:verified\s+by|run|check\s+with|using|via)\s+"
        rf"[`\"]?({runner}\b[^`\",]*)[`\"]?",
        re.IGNORECASE,
    )
    for runner in KNOWN_RUNNERS
]


def _extract_test_flag(condition: str) -> tuple[str, str]:
    """Pull --test from condition text; return (test_cmd, remaining)."""
    match = _TEST_FLAG_QUOTED.search(condition)
    if match:
        return match.group(1), _TEST_FLAG_QUOTED.sub("", condition)
    match = _TEST_FLAG_BARE.search(condition)
    if match:
        return match.group(1), _TEST_FLAG_BARE.sub("", condition)
    return "", condition


def _extract_budget(condition: str, default: int = 20) -> tuple[int, str]:
    """Pull --budget / natural-language budget; return (budget, remaining).

    Explicit ``--budget N`` wins over NL phrases like ``stop after M turns``.
    """
    budget = default
    flag_set = False
    match = _BUDGET_FLAG.search(condition)
    if match:
        budget = int(match.group(1))
        condition = _BUDGET_FLAG.sub("", condition)
        flag_set = True
    match = _BUDGET_NL.search(condition)
    if match:
        if not flag_set:
            budget = int(match.group(1))
        condition = _BUDGET_NL.sub("", condition)
    return budget, condition


def _extract_wake_budget(condition: str) -> tuple[int | None, str]:
    """Pull ``--wake-budget N``; return (value or None, remaining)."""
    match = _WAKE_BUDGET_FLAG.search(condition)
    if not match:
        return None, condition
    return int(match.group(1)), _WAKE_BUDGET_FLAG.sub("", condition)


def _extract_workdir(condition: str) -> tuple[str | None, str]:
    """Pull ``--workdir`` (quoted or bare); return (path or None, remaining)."""
    match = _WORKDIR_QUOTED.search(condition)
    if match:
        return match.group(1), _WORKDIR_QUOTED.sub("", condition)
    match = _WORKDIR_BARE.search(condition)
    if match:
        return match.group(1), _WORKDIR_BARE.sub("", condition)
    return None, condition


def _extract_bool_flags(condition: str) -> tuple[bool | None, bool, str]:
    """Pull shell/force flags.

    Returns ``(allow_shell, force, remaining)`` where *allow_shell* is
    ``True``/``False`` when a shell flag was present, else ``None``.
    When both ``--allow-shell`` and ``--deny-shell`` appear, the last one wins.
    """
    allow_shell: bool | None = None
    force = False
    # Scan left-to-right so later flags override earlier ones.
    for match in re.finditer(r"--(?:allow-shell|deny-shell|force)\b", condition):
        token = match.group(0)
        if token == "--allow-shell":  # nosec B105 — CLI flag, not a password
            allow_shell = True
        elif token == "--deny-shell":  # nosec B105 — CLI flag, not a password
            allow_shell = False
        elif token == "--force":  # nosec B105 — CLI flag, not a password
            force = True
    remaining = _ALLOW_SHELL_FLAG.sub("", condition)
    remaining = _DENY_SHELL_FLAG.sub("", remaining)
    remaining = _FORCE_FLAG.sub("", remaining)
    return allow_shell, force, remaining


def _truncate_shell_chain(candidate: str) -> str:
    """Keep only the first segment before common shell chain operators."""
    for sep in ("&&", "||", ";", "|"):
        if sep in candidate:
            return candidate.split(sep, 1)[0].strip()
    return candidate


def _shell_chain_in(candidate: str) -> bool:
    """Return True when *candidate* contains shell chain operators."""
    return any(sep in candidate for sep in ("&&", "||", ";", "|"))


def _extract_validation_hint(condition: str) -> tuple[str, str, str | None]:
    """Pull a known-runner validation hint.

    Returns ``(test_cmd, remaining, warning)``. When the matched hint contains
    shell chain operators, *test_cmd* is left empty and *warning* explains that
    an explicit ``--test "..."`` is required (no silent truncation).
    """
    for pattern in _VALIDATION_HINTS:
        match = pattern.search(condition)
        if not match:
            continue
        raw = match.group(1).strip().strip("`'\"")
        remaining = pattern.sub("", condition, count=1)
        if _shell_chain_in(raw):
            warning = (
                "NL validation hint contains shell chain operators "
                "(&&, ||, ;, |); refusing silent truncation — pass "
                'explicit --test "..." for compound commands'
            )
            logger.warning("%s (hint=%r)", warning, raw)
            return "", remaining, warning
        return _truncate_shell_chain(raw), remaining, None
    return "", condition, None


def _normalize_condition(condition: str) -> str:
    """Collapse whitespace and strip a single wrapping quote pair if present."""
    cleaned = condition.strip()
    if len(cleaned) >= 2 and cleaned[0] == cleaned[-1] and cleaned[0] in "\"'":
        cleaned = cleaned[1:-1].strip()
    else:
        # Leftover unmatched quotes from partial stripping (legacy agents).
        cleaned = cleaned.strip('",').strip()
    return re.sub(r"\s+", " ", cleaned)


def parse_raw(raw: str) -> dict[str, Any]:  # pylint: disable=too-many-branches
    """Parse /cursor-goal (or leftover /goal) input into a JSON-serializable dict."""
    text = raw.strip()
    if not text:
        raise ValueError('Usage: cursor-goal parse "<raw /cursor-goal input>"')

    text = re.sub(r"^/(?:cursor-)?goal\s*", "", text, count=1)

    blocked_parts = text.split(None, 1)
    if blocked_parts and blocked_parts[0].lower() == "blocked":
        reason = blocked_parts[1].strip() if len(blocked_parts) > 1 else ""
        if not reason:
            raise ValueError('Usage: /cursor-goal blocked "<reason>"')
        blocked: dict[str, Any] = {
            "subcommand": "blocked",
            "action": "blocked",
            "condition": None,
            "test_cmd": None,
            "budget": None,
            "reason": reason,
        }
        logger.info("Parsed subcommand=blocked reason=%r", reason)
        return blocked

    if text in SUBCOMMANDS:
        action = "clear" if text in CLEAR_ALIASES else text
        result: dict[str, Any] = {
            "subcommand": text,
            "action": action,
            "condition": None,
            "test_cmd": None,
            "budget": None,
        }
        logger.info("Parsed subcommand=%s action=%s", text, action)
        return result

    test_cmd, condition = _extract_test_flag(text)
    budget, condition = _extract_budget(condition)
    wake_budget, condition = _extract_wake_budget(condition)
    workdir, condition = _extract_workdir(condition)
    allow_shell, force, condition = _extract_bool_flags(condition)
    warning: str | None = None
    if not test_cmd:
        test_cmd, condition, warning = _extract_validation_hint(condition)
    condition = _normalize_condition(condition)

    if not condition:
        raise ValueError(f"Could not extract a condition from: {raw}")
    if budget < 1:
        raise ValueError(f"Budget must be a positive integer, got {budget}")
    if budget > MAX_TURN_BUDGET:
        raise ValueError(f"Budget must be <= {MAX_TURN_BUDGET}, got {budget}")
    budget = clamp_turn_budget(budget)
    if wake_budget is not None:
        if wake_budget < 1:
            raise ValueError(
                f"Wake budget must be a positive integer, got {wake_budget}"
            )
        if wake_budget > MAX_TURN_BUDGET:
            raise ValueError(
                f"Wake budget must be <= {MAX_TURN_BUDGET}, got {wake_budget}"
            )
        wake_budget = clamp_wake_budget(wake_budget)

    weak = weak_condition_warning(condition)
    if weak:
        warning = f"{warning}; {weak}" if warning else weak

    result = {
        "subcommand": None,
        "action": "create",
        "condition": condition,
        "test_cmd": test_cmd or None,
        "budget": budget,
    }
    if allow_shell is not None:
        result["allow_shell"] = allow_shell
    if force:
        result["force"] = True
    if wake_budget is not None:
        result["wake_budget"] = wake_budget
    if workdir is not None:
        result["workdir"] = workdir
    if warning:
        result["warning"] = warning
    logger.info(
        "Parsed create condition=%r test=%r budget=%s allow_shell=%s "
        "force=%s wake_budget=%s workdir=%r warning=%s",
        condition,
        redact_command(test_cmd) if test_cmd else "",
        budget,
        allow_shell,
        force,
        wake_budget,
        workdir or "",
        warning or "",
    )
    return result


def cmd_parse(argv: list[str]) -> int:
    if not argv:
        print('[goal-parse] Error: Usage: cursor-goal parse "<raw>"', file=sys.stderr)
        return 1
    # Join unquoted argv words so agents that forget shell quoting still work.
    raw = " ".join(argv)
    try:
        payload = parse_raw(raw)
    except ValueError as exc:
        print(f"[goal-parse] Error: {exc}", file=sys.stderr)
        return 1
    sys.stdout.write(json.dumps(payload, ensure_ascii=False) + "\n")
    sys.stdout.flush()
    return 0
