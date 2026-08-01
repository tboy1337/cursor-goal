"""Run goal validation commands without shell eval when possible."""

from __future__ import annotations

import os
import re
import shlex
import subprocess  # nosec B404
from dataclasses import dataclass

from cursor_goal.logging_config import get_logger

logger = get_logger("cursor_goal.validation")

DEFAULT_TIMEOUT_SEC = 25

_SECRETISH = re.compile(
    r"(?i)(?:"
    r"(password|passwd|secret|token|api[_-]?key|authorization|access[_-]?key|"
    r"client[_-]?secret|private[_-]?key)=(\S+)"
    r"|(--(?:password|passwd|secret|token|api[_-]?key|access[_-]?key|"
    r"client[_-]?secret))\s+(\S+)"
    r"|(Bearer)\s+(\S+)"
    r"|((?:AKIA|ASIA)[A-Z0-9]{16})"
    r")"
)


@dataclass(frozen=True)
class ValidationResult:
    exit_code: int
    output: str
    timed_out: bool = False


def _stream_to_text(stream: str | bytes | None) -> str:
    """Normalize subprocess stream output to text (TimeoutExpired may be either)."""
    if stream is None:
        return ""
    if isinstance(stream, bytes):
        return stream.decode("utf-8", errors="replace")
    return stream


def redact_command(command: str) -> str:
    """Redact likely secrets for logs / prompts / status; truncate long commands."""

    def _sub(match: re.Match[str]) -> str:
        if match.group(1):
            return f"{match.group(1)}=<redacted>"
        if match.group(3):
            return f"{match.group(3)} <redacted>"
        if match.group(5):
            return f"{match.group(5)} <redacted>"
        if match.group(6):
            return "<redacted>"
        return "<redacted>"

    redacted = _SECRETISH.sub(_sub, command)
    if len(redacted) > 200:
        return redacted[:200] + "…"
    return redacted


# Back-compat alias for older call sites / tests.
_redact_command = redact_command


def deny_shell_enabled() -> bool:
    """Return True when CURSOR_GOAL_DENY_SHELL requests argv-only validation."""
    raw = os.environ.get("CURSOR_GOAL_DENY_SHELL", "").strip().lower()
    return raw in {"1", "true", "yes", "on"}


def try_split_argv(command: str) -> list[str] | None:
    """Best-effort split for simple commands without shell metacharacters."""
    meta = ("|", "&", ";", ">", "<", "`", "\n", "$(", "${")
    if any(ch in command for ch in meta):
        return None
    if os.name == "nt" and any(ch in command for ch in ("%", "^")):
        return None
    try:
        # posix=False on Windows preserves backslash paths but keeps quote chars
        # on tokens; strip matching surrounding quotes afterward.
        parts = shlex.split(command, posix=os.name != "nt")
    except ValueError:
        return None
    if not parts:
        return None
    if os.name == "nt":
        cleaned: list[str] = []
        for part in parts:
            if len(part) >= 2 and part[0] == part[-1] and part[0] in "'\"":
                cleaned.append(part[1:-1])
            else:
                cleaned.append(part)
        parts = cleaned
    return parts


def run_validation(
    command: str,
    *,
    timeout_sec: float = DEFAULT_TIMEOUT_SEC,
    cwd: str | None = None,
) -> ValidationResult:
    """Run a validation command via subprocess.

    Prefers ``shell=False`` with argv from :func:`try_split_argv`. Falls back to
    ``shell=True`` for user/agent shell snippets (e.g. ``npm test && npm run lint``)
    unless ``CURSOR_GOAL_DENY_SHELL`` is set. On Windows, shell mode uses
    ``COMSPEC`` (cmd.exe), not PowerShell.

    Shell mode is intentional but risky if an attacker controls goal.json.
    """
    stripped = command.strip()
    if not stripped:
        logger.warning("Validation refused: empty/whitespace command")
        return ValidationResult(
            exit_code=1,
            output="[goal-eval] Error: empty validation command",
        )

    argv = try_split_argv(stripped)
    env = os.environ.copy()
    if argv is not None:
        mode = "argv"
        run_args: str | list[str] = argv
        use_shell = False
    else:
        if deny_shell_enabled():
            logger.warning(
                "Validation refused: shell metacharacters with CURSOR_GOAL_DENY_SHELL"
            )
            return ValidationResult(
                exit_code=1,
                output=(
                    "[goal-eval] Error: validation command requires a shell, but "
                    "CURSOR_GOAL_DENY_SHELL is set. Use a simple argv command or "
                    "unset CURSOR_GOAL_DENY_SHELL."
                ),
            )
        mode = "shell"
        run_args = stripped
        use_shell = True
        logger.warning(
            "Validation using shell=True via COMSPEC/sh "
            "(metacharacters or unsplittable; trusted-user goal.json only) "
            "len=%s cmd=%r",
            len(stripped),
            redact_command(stripped),
        )
    command = stripped

    logger.info(
        "Running validation mode=%s timeout=%s len=%s cmd=%r",
        mode,
        timeout_sec,
        len(command),
        redact_command(command),
    )
    if os.environ.get("CURSOR_GOAL_LOG_SECRETS", "").strip().lower() in {
        "1",
        "true",
        "yes",
        "on",
    }:
        logger.debug("Validation full command (secrets enabled): %r", command)

    try:
        completed = subprocess.run(
            run_args,
            shell=use_shell,  # nosec B602
            capture_output=True,
            text=True,
            timeout=timeout_sec,
            cwd=cwd,
            env=env,
            check=False,
        )
    except subprocess.TimeoutExpired as exc:
        out = _stream_to_text(exc.stdout) + _stream_to_text(exc.stderr)
        logger.warning("Validation timed out after %ss", timeout_sec)
        return ValidationResult(exit_code=124, output=out[-4000:], timed_out=True)
    except OSError as exc:
        logger.error("Validation OSError: %s", exc)
        return ValidationResult(
            exit_code=127,
            output=f"[goal-eval] Error: could not run validation command: {exc}",
        )

    combined = (completed.stdout or "") + (completed.stderr or "")
    logger.info(
        "Validation exit=%s bytes=%s mode=%s", completed.returncode, len(combined), mode
    )
    return ValidationResult(exit_code=completed.returncode, output=combined[-4000:])
