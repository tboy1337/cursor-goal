"""Resolve installed skill / harness paths (classic + marketplace)."""

from __future__ import annotations

import os
import shlex
import sys
from pathlib import Path

from cursor_goal.logging_config import get_logger

logger = get_logger("cursor_goal.paths")

_ENV_HOME = "CURSOR_GOAL_HOME"
_ENV_PLUGIN_ROOT = "CURSOR_PLUGIN_ROOT"


def _package_dir() -> Path:
    """Directory containing this package (…/cursor_goal)."""
    return Path(__file__).resolve().parent


def skill_root() -> Path:
    """Return the skill root that contains ``scripts/run_goal.py``.

    Resolution order:
    1. ``CURSOR_GOAL_HOME`` (must be absolute) when set
    2. Parent of this package when layout is ``<skill>/cursor_goal/``
    3. ``$CURSOR_PLUGIN_ROOT/skills/goal`` when the env var is set
    4. Classic ``~/.cursor/skills/goal``
    """
    override = (os.environ.get(_ENV_HOME) or "").strip()
    if override:
        path = Path(override).expanduser()
        if not path.is_absolute():
            raise ValueError(f"{_ENV_HOME} must be an absolute path (got {override!r})")
        logger.debug("skill_root from %s=%s", _ENV_HOME, path)
        return path

    pkg = _package_dir()
    parent = pkg.parent
    if (parent / "scripts" / "run_goal.py").is_file():
        logger.debug("skill_root from package parent=%s", parent)
        return parent

    plugin_root = (os.environ.get(_ENV_PLUGIN_ROOT) or "").strip()
    if plugin_root:
        candidate = Path(plugin_root).expanduser() / "skills" / "goal"
        if (candidate / "scripts" / "run_goal.py").is_file():
            logger.debug("skill_root from plugin=%s", candidate)
            return candidate

    classic = Path.home() / ".cursor" / "skills" / "goal"
    logger.debug("skill_root classic fallback=%s", classic)
    return classic


def run_goal_script() -> Path:
    """Absolute path to ``scripts/run_goal.py`` under the resolved skill root."""
    return skill_root() / "scripts" / "run_goal.py"


def python_invocation() -> list[str]:
    """Preferred interpreter argv prefix for Shell commands."""
    if os.name == "nt":
        return ["py", "-3", "-u"]
    return ["python3", "-u"]


def quote_for_shell(path: Path) -> str:
    """Quote *path* for the current platform's typical agent shell."""
    text = str(path)
    if os.name == "nt":
        # PowerShell-friendly double quotes; escape embedded quotes.
        escaped = text.replace("'", "''")
        return f"'{escaped}'"
    return shlex.quote(text)


def run_goal_invocation(*args: str) -> str:
    """Shell command string to invoke the harness with optional subcommand args."""
    script = run_goal_script()
    py = python_invocation()
    quoted_script = quote_for_shell(script)
    parts = [*py, quoted_script, *args]
    if os.name == "nt":
        # py/python tokens stay unquoted; paths quoted above.
        return " ".join(parts)
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
