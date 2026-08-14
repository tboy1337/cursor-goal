"""Run goal validation commands without shell eval when possible."""

from __future__ import annotations

import os
import re
import shlex
import subprocess  # nosec B404
import sys
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path, PosixPath

from cursor_goal.logging_config import get_logger
from cursor_goal.path_trust import path_has_symlink_or_reparse

logger = get_logger("cursor_goal.validation")

DEFAULT_TIMEOUT_SEC = 25

_SECRETISH = re.compile(
    r"(?i)(?:"
    r"(?P<kv_key>password|passwd|secret|token|api[_-]?key|authorization|access[_-]?key|"
    r"client[_-]?secret|private[_-]?key)=(?P<kv_val>\S+)"
    r"|(?P<flag>--(?:password|passwd|secret|token|api[_-]?key|access[_-]?key|"
    r"client[_-]?secret))\s+(?P<flag_val>\S+)"
    r"|(?P<bearer>Bearer)\s+(?P<bearer_val>\S+)"
    r"|(?P<aws_key>(?:AKIA|ASIA)[A-Z0-9]{16})"
    r"|(?P<basic>Authorization:\s*Basic)\s+(?P<basic_val>\S+)"
    r"|(?P<jwt>eyJ[A-Za-z0-9_-]{10,}\.[A-Za-z0-9_-]{10,}\.[A-Za-z0-9_-]{10,})"
    # GitHub personal-access / fine-grained / OAuth / app tokens.
    r"|(?P<gh_tok>gh[oprsu]_[A-Za-z0-9]{20,}|github_pat_[A-Za-z0-9_]{20,})"
    # OpenAI / Anthropic-style secret keys (sk-, sk-proj-, sk-ant-).
    r"|(?P<sk_tok>sk-(?:proj-|ant-)?[A-Za-z0-9_-]{16,})"
    # Slack bot/user/app/refresh/legacy tokens.
    r"|(?P<slack_tok>xox[baprs]-[A-Za-z0-9-]{10,})"
    # npm and GitLab personal-access tokens.
    r"|(?P<npm_tok>npm_[A-Za-z0-9]{20,})"
    r"|(?P<gitlab_tok>glpat-[A-Za-z0-9_-]{16,})"
    # scheme://user:password@host — connection strings and credentialed URLs
    # (username may be empty, e.g. redis://:password@host).
    r"|(?P<url_scheme>[a-z][a-z0-9+.-]{1,15}://)(?P<url_userinfo>[^\s:@/]*:[^\s@/]+)@"
    r")"
)

_PEM_BLOCK = re.compile(
    r"-----BEGIN((?:\s+[A-Z0-9]+)*\s+PRIVATE KEY)-----" r".*?" r"-----END\1-----",
    re.DOTALL,
)

# One formatter per named alternative in _SECRETISH, keyed by group name and
# checked in the same priority order the alternatives are written above
# (dict iteration order is insertion order). A dispatch table keeps this a
# single-branch lookup instead of a long if/elif chain.
_SecretGroups = dict[str, "str | None"]
_SECRETISH_FORMATTERS: dict[str, Callable[[_SecretGroups], str]] = {
    "kv_key": lambda g: f"{g['kv_key']}=<redacted>",
    "flag": lambda g: f"{g['flag']} <redacted>",
    "bearer": lambda g: f"{g['bearer']} <redacted>",
    "aws_key": lambda _g: "<redacted>",
    "basic": lambda g: f"{g['basic']} <redacted>",
    "jwt": lambda _g: "<redacted-jwt>",
    "gh_tok": lambda _g: "<redacted-github-token>",
    "sk_tok": lambda _g: "<redacted-api-key>",
    "slack_tok": lambda _g: "<redacted-slack-token>",
    "npm_tok": lambda _g: "<redacted-npm-token>",
    "gitlab_tok": lambda _g: "<redacted-gitlab-token>",
    "url_scheme": lambda g: f"{g['url_scheme']}<redacted>@",
}


def _redact_secretish_match(match: re.Match[str]) -> str:
    groups = match.groupdict()
    for name, formatter in _SECRETISH_FORMATTERS.items():
        if groups.get(name):
            return formatter(groups)
    return "<redacted>"  # pragma: no cover — every alternative has a formatter above


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


def redact_secrets(text: str, *, max_chars: int | None = 4000) -> str:
    """Redact likely secrets in commands or validation output for logs/prompts.

    ``max_chars`` truncates after redaction (``None`` keeps full length).
    """

    redacted = _PEM_BLOCK.sub("<redacted-private-key-block>", text)
    redacted = _SECRETISH.sub(_redact_secretish_match, redacted)
    # Also redact common JSON-ish "apiKey":"…" / 'token':'…' forms.
    redacted = re.sub(
        r"(?i)([\"']?(?:api[_-]?key|token|password|secret|"
        r"client[_-]?secret|private[_-]?key|access[_-]?key)"
        r"[\"']?\s*[:=]\s*[\"'])([^\"']+)",
        r"\1<redacted>",
        redacted,
    )
    if max_chars is not None and len(redacted) > max_chars:
        return redacted[:max_chars] + "…"
    return redacted


def redact_command(command: str) -> str:
    """Redact likely secrets for logs / prompts / status; truncate long commands."""
    return redact_secrets(command, max_chars=200)


_ENV_ALLOWLIST_EXACT = frozenset(
    {
        "PATH",
        "PATHEXT",
        "HOME",
        "USERPROFILE",
        "USERNAME",
        "USER",
        "LOGNAME",
        "TMP",
        "TEMP",
        "TMPDIR",
        "SystemRoot",
        "SYSTEMROOT",
        "windir",
        "COMSPEC",
        "ComSpec",
        "LANG",
        "LC_ALL",
        "LC_CTYPE",
        "LANGUAGE",
        "TERM",
        "TZ",
        # Intentionally omit PYTHONPATH / PYTHONHOME — ambient import hijack risk.
        "PYTHONUTF8",
        "PYTHONIOENCODING",
        "PYTHONUNBUFFERED",
        "VIRTUAL_ENV",
        "NUMBER_OF_PROCESSORS",
        "PROCESSOR_ARCHITECTURE",
        "OS",
        "HOMEBREW_PREFIX",
        # Windows profile / Program Files (npm, yarn, and many CLIs need these).
        "APPDATA",
        "LOCALAPPDATA",
        "ProgramData",
        "ProgramFiles",
        "ProgramFiles(x86)",
        "CommonProgramFiles",
        "CommonProgramFiles(x86)",
        # Common toolchain homes (non-secret path hints).
        "CARGO_HOME",
        "RUSTUP_HOME",
        "GOPATH",
        "GOROOT",
        "JAVA_HOME",
        "JDK_HOME",
        # Intentionally omit NODE_PATH / NPM_CONFIG_USERCONFIG / MAVEN_OPTS /
        # SBT_OPTS — ambient module/config/JVM-agent hijack risk (same class
        # as PYTHONPATH).
        "NPM_CONFIG_CACHE",
        "npm_config_cache",
        "BUN_INSTALL",
        "PNPM_HOME",
        "GRADLE_USER_HOME",
        "ANDROID_HOME",
        "ANDROID_SDK_ROOT",
        "DOTNET_ROOT",
        "NuGetPackageRoot",
    }
)

_ENV_ALLOWLIST_PREFIXES = (
    "CURSOR_GOAL_",
    "LC_",
)

# Privilege / safety toggles must not reach validation children (nested harness).
_CURSOR_GOAL_CHILD_DROP = frozenset(
    {
        "CURSOR_GOAL_LOG_SECRETS",
        "CURSOR_GOAL_SKIP_ACL",
        "CURSOR_GOAL_ALLOW_ANY_WORKDIR",
        "CURSOR_GOAL_ALLOW_DEAD_WAKE",
    }
)


def _pinned_windows_comspec(env_in: dict[str, str]) -> str | None:
    """Prefer ``%SystemRoot%\\System32\\cmd.exe`` over ambient COMSPEC."""
    system_root = (
        env_in.get("SystemRoot")
        or env_in.get("SYSTEMROOT")
        or os.environ.get("SystemRoot")
        or os.environ.get("SYSTEMROOT")
        or ""
    ).strip()
    if system_root:
        if sys.platform == "win32":
            candidate: Path = Path(system_root) / "System32" / "cmd.exe"
        else:
            # Tests may patch os.name to "nt" on Unix; plain Path() would pick
            # WindowsPath and fail to instantiate outside Windows.
            candidate = PosixPath(system_root) / "System32" / "cmd.exe"
        try:
            if candidate.is_file():
                return str(candidate)
        except OSError:
            pass
    ambient = (env_in.get("COMSPEC") or env_in.get("ComSpec") or "").strip()
    return ambient or None


def scrubbed_validation_env(
    source: dict[str, str] | None = None,
) -> dict[str, str]:
    """Return a reduced environment for validation subprocesses.

    Keeps PATH/home/locale/shell basics, ``VIRTUAL_ENV``, and ``CURSOR_GOAL_*``
    (except log-secret and privilege toggles). Drops ambient API tokens,
    ``PYTHONPATH``, ``PYTHONHOME``, ``NODE_PATH``, ``NPM_CONFIG_USERCONFIG``,
    ``MAVEN_OPTS``, ``SBT_OPTS``, and unrelated secrets from the parent.

    On Windows, pins ``COMSPEC`` to ``%SystemRoot%\\System32\\cmd.exe`` when that
    file exists so a poisoned ambient ``COMSPEC`` cannot redirect shell mode.
    """
    env_in = os.environ if source is None else source
    out: dict[str, str] = {}
    for key, value in env_in.items():
        if key in _ENV_ALLOWLIST_EXACT:
            # Skip ambient COMSPEC — pin below on Windows.
            if key.upper() == "COMSPEC":
                continue
            out[key] = value
            continue
        if key.startswith(_ENV_ALLOWLIST_PREFIXES):
            if key.upper() in _CURSOR_GOAL_CHILD_DROP:
                continue
            out[key] = value
    if os.name == "nt":
        pinned = _pinned_windows_comspec(dict(env_in))
        if pinned:
            out["COMSPEC"] = pinned
    return out


def deny_shell_enabled() -> bool:
    """Return True when CURSOR_GOAL_DENY_SHELL requests argv-only validation."""
    raw = os.environ.get("CURSOR_GOAL_DENY_SHELL", "").strip().lower()
    return raw in {"1", "true", "yes", "on"}


def shell_allowed_for_goal(*, shell_ok: bool = False) -> bool:
    """Return True when shell-mode validation may run for this goal."""
    if deny_shell_enabled():
        return False
    return bool(shell_ok)


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
    shell_ok: bool = False,
) -> ValidationResult:
    """Run a validation command via subprocess.

    Prefers ``shell=False`` with argv from :func:`try_split_argv`. Falls back to
    ``shell=True`` for user/agent shell snippets (e.g. ``npm test && npm run lint``)
    only when ``shell_ok`` is True and ``CURSOR_GOAL_DENY_SHELL`` is unset.
    Defaults to ``shell_ok=False`` (fail closed; matches create defaults). On
    Windows, shell mode uses pinned ``COMSPEC`` (cmd.exe), not PowerShell.

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
    env = scrubbed_validation_env()
    if argv is not None:
        mode = "argv"
        run_args: str | list[str] = argv
        use_shell = False
    else:
        if not shell_allowed_for_goal(shell_ok=shell_ok):
            reason = (
                "CURSOR_GOAL_DENY_SHELL is set"
                if deny_shell_enabled()
                else "goal shell_ok=false (--deny-shell)"
            )
            logger.warning("Validation refused: shell metacharacters with %s", reason)
            return ValidationResult(
                exit_code=1,
                output=(
                    "[goal-eval] Error: validation command requires a shell, but "
                    f"{reason}. Use a simple argv command or allow shell mode."
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

    if cwd:
        cwd_path = Path(cwd)
        if path_has_symlink_or_reparse(cwd_path):
            logger.warning(
                "Validation refused: cwd is a symlink/junction/reparse: %s",
                cwd,
            )
            return ValidationResult(
                exit_code=1,
                output=(
                    "[goal-eval] Error: validation cwd must not be a symlink, "
                    f"junction, or reparse point: {cwd}"
                ),
            )

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
