"""Resolve installed skill / harness paths (classic + marketplace)."""

from __future__ import annotations

import os
import shlex
import sys
from pathlib import Path

from cursor_goal.logging_config import get_logger
from cursor_goal.native_path import native_path, path_str_is_absolute

logger = get_logger("cursor_goal.paths")

_ENV_HOME = "CURSOR_GOAL_HOME"
_ENV_PLUGIN_ROOT = "CURSOR_PLUGIN_ROOT"


def _package_dir() -> Path:
    """Directory containing this package (…/cursor_goal)."""
    return native_path(__file__).resolve().parent


def skill_root() -> Path:
    """Return the skill root that contains ``scripts/run_goal.py``.

    Resolution order:
    1. ``CURSOR_GOAL_HOME`` (must be absolute) when set
    2. Parent of this package when layout is ``<skill>/cursor_goal/``
    3. ``$CURSOR_PLUGIN_ROOT/skills/cursor-goal`` when the env var is set
    4. Classic ``~/.cursor/skills/cursor-goal``
    """
    override = (os.environ.get(_ENV_HOME) or "").strip()
    if override:
        if not path_str_is_absolute(override):
            raise ValueError(f"{_ENV_HOME} must be an absolute path (got {override!r})")
        path = native_path(override)
        logger.debug("skill_root from %s=%s", _ENV_HOME, path)
        return path

    pkg = _package_dir()
    parent = pkg.parent
    if (parent / "scripts" / "run_goal.py").is_file():
        logger.debug("skill_root from package parent=%s", parent)
        return parent

    plugin_root = (os.environ.get(_ENV_PLUGIN_ROOT) or "").strip()
    if plugin_root:
        candidate = native_path(plugin_root) / "skills" / "cursor-goal"
        if (candidate / "scripts" / "run_goal.py").is_file():
            logger.debug("skill_root from plugin=%s", candidate)
            return candidate

    classic = native_path(
        os.path.join(os.path.expanduser("~"), ".cursor", "skills", "cursor-goal")
    )
    logger.debug("skill_root classic fallback=%s", classic)
    return classic


def run_goal_script() -> Path:
    """Absolute path to ``scripts/run_goal.py`` under the resolved skill root."""
    return skill_root() / "scripts" / "run_goal.py"


def python_invocation() -> list[str]:
    """Preferred interpreter argv prefix for Shell commands.

    Prefers the running interpreter (``sys.executable -u``) when it reports
    Python 3.12+, so classic installs match the baked stop/wake launcher.
    Falls back to ``py -3`` (Windows) or ``python3`` (Unix).
    """
    exe = (sys.executable or "").strip()
    if exe and sys.version_info >= (3, 12):
        return [exe, "-u"]
    if os.name == "nt":
        return ["py", "-3", "-u"]
    return ["python3", "-u"]


def quote_for_shell(path: Path) -> str:
    """Quote *path* for the current platform's typical agent shell."""
    text = str(path)
    if os.name == "nt":
        # PowerShell-friendly single quotes; escape embedded quotes.
        escaped = text.replace("'", "''")
        return f"'{escaped}'"
    return shlex.quote(text)


def run_goal_invocation(*args: str) -> str:
    """Shell command string to invoke the harness with optional subcommand args."""
    script = run_goal_script()
    py = python_invocation()
    quoted_script = quote_for_shell(script)
    # Quote absolute interpreter paths; leave bare launcher names unquoted.
    quoted_py: list[str] = []
    unbuffered = "-u"  # nosec B105 — Python -u flag, not a password
    for token in py:
        if token == unbuffered or token in {"py", "-3", "python3", "python"}:
            quoted_py.append(token)
        elif os.name == "nt" and (":" in token or "\\" in token or "/" in token):
            quoted_py.append(quote_for_shell(Path(token)))
        elif token.startswith("/") or token.startswith("~"):
            quoted_py.append(quote_for_shell(Path(token)))
        else:
            quoted_py.append(token)
    parts = [*quoted_py, quoted_script, *args]
    return " ".join(parts)


def wake_loop_invocation() -> str:
    """Shell command to start the wake loop."""
    return run_goal_invocation("wake", "loop")


def harness_cmd_report() -> dict[str, str]:
    """Machine-readable harness path info for ``manage harness-cmd``."""
    root = skill_root()
    script = run_goal_script()
    return {
        "skill_root": str(root),
        "run_goal": str(script),
        "exists": str(script.is_file()).lower(),
        "invocation": run_goal_invocation("<command>", "..."),
        "wake_loop": wake_loop_invocation(),
        "python": sys.executable,
        "cursor_goal_home": (os.environ.get(_ENV_HOME) or "").strip(),
        "cursor_plugin_root": (os.environ.get(_ENV_PLUGIN_ROOT) or "").strip(),
    }
