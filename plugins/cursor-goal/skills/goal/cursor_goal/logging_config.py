"""Logging setup for the cursor-goal harness."""

from __future__ import annotations

import logging
import os
import sys
from pathlib import Path
from typing import TextIO


def _resolve_level(name: str) -> int:
    """Map level name to logging level; warn and fall back on invalid values."""
    level = getattr(logging, name, None)
    if isinstance(level, int):
        return level
    sys.stderr.write(f"[cursor_goal] Invalid CURSOR_GOAL_LOG={name!r}; using WARNING\n")
    return logging.WARNING


def _default_log_path() -> Path:
    """Resolve default log path without importing state (avoids cycles)."""
    override = os.environ.get("CURSOR_GOAL_DATA")
    if override:
        return Path(override).expanduser() / "cursor-goal.log"
    return Path.home() / ".cursor-goal" / "data" / "cursor-goal.log"


def _maybe_chmod_log_file(path: Path) -> None:
    """Best-effort private mode for durable log files (Unix)."""
    if os.name == "nt":
        return
    try:
        os.chmod(path, 0o600)
    except OSError:
        pass


def _open_log_file() -> TextIO | None:
    """Open optional durable log file under data dir or absolute path."""
    raw = os.environ.get("CURSOR_GOAL_LOG_FILE", "").strip()
    if not raw:
        return None
    try:
        if raw.lower() in {".", "1", "true", "yes", "on"}:
            path = _default_log_path()
        else:
            path = Path(raw).expanduser()
        path.parent.mkdir(parents=True, exist_ok=True)
        # Keep handle open for the process lifetime (logger owns it).
        handle = open(
            path, "a", encoding="utf-8"
        )  # pylint: disable=consider-using-with
        _maybe_chmod_log_file(path)
        return handle
    except OSError as exc:
        sys.stderr.write(
            f"[cursor_goal] Could not open CURSOR_GOAL_LOG_FILE={raw!r}: {exc}\n"
        )
        return None


def get_logger(name: str = "cursor_goal") -> logging.Logger:
    """Return a module logger configured for stderr (+ optional file) diagnostics."""
    logger = logging.getLogger(name)
    if logger.handlers:
        return logger

    raw_level = os.environ.get("CURSOR_GOAL_LOG", "").strip()
    if raw_level:
        level_name = raw_level.upper()
    elif os.environ.get("CURSOR_GOAL_LOG_FILE", "").strip():
        # Durable log file without explicit level → INFO for usable diagnostics.
        level_name = "INFO"
    else:
        level_name = "WARNING"
    level = _resolve_level(level_name)
    logger.setLevel(level)

    formatter = logging.Formatter(
        "[%(name)s] %(levelname)s: %(message)s [pid=%(process)d]"
    )

    stderr_handler = logging.StreamHandler(sys.stderr)
    stderr_handler.setFormatter(formatter)
    logger.addHandler(stderr_handler)

    log_file = _open_log_file()
    if log_file is not None:
        file_handler = logging.StreamHandler(log_file)
        file_handler.setFormatter(formatter)
        logger.addHandler(file_handler)

    logger.propagate = False
    return logger
