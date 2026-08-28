"""Logging setup for the cursor-goal harness."""

from __future__ import annotations

import logging
import os
import sys
from pathlib import Path
from typing import TextIO

from cursor_goal.native_path import native_path

_CONFIGURED = False
_LOG_FILE_HANDLE: TextIO | None = None


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
        return native_path(override) / "cursor-goal.log"
    return native_path(
        os.path.join(os.path.expanduser("~"), ".cursor-goal", "data")
    ) / ("cursor-goal.log")


def _maybe_chmod_log_file(path: Path) -> None:
    """Best-effort private mode for durable log files (Unix)."""
    if os.name == "nt":
        return
    try:
        os.chmod(path, 0o600)
    except OSError:
        pass


def _open_log_file() -> TextIO | None:
    """Open optional durable log file under data dir or absolute path.

    Refuses symlink/reparse parents (same trust boundary as CURSOR_GOAL_DATA).
    Uses a deferred import of path_trust to avoid an import cycle
    (path_trust → logging_config).
    """
    raw = os.environ.get("CURSOR_GOAL_LOG_FILE", "").strip()
    if not raw:
        return None
    try:
        # Circular: path_trust imports get_logger from this module.
        from cursor_goal.path_trust import (  # isort: skip  # pylint: disable=import-outside-toplevel
            data_dir_is_insecure,
            path_has_symlink_or_reparse,
        )

        if raw.lower() in {".", "1", "true", "yes", "on"}:
            if data_dir_is_insecure():
                sys.stderr.write(
                    "[cursor_goal] CURSOR_GOAL_LOG_FILE disabled: data "
                    "directory is insecure (symlink/reparse/permissions)\n"
                )
                return None
            path = _default_log_path()
        else:
            expanded = Path(raw).expanduser()
            # Match CURSOR_GOAL_DATA: custom paths must be absolute.
            if not expanded.is_absolute():
                sys.stderr.write(
                    "[cursor_goal] CURSOR_GOAL_LOG_FILE must be an absolute "
                    f"path (got {raw!r}); durable file logging disabled\n"
                )
                return None
            if path_has_symlink_or_reparse(expanded) or path_has_symlink_or_reparse(
                expanded.parent
            ):
                sys.stderr.write(
                    "[cursor_goal] CURSOR_GOAL_LOG_FILE disabled: path or "
                    "parent is a symlink/reparse point\n"
                )
                return None
            path = expanded
        path.parent.mkdir(parents=True, exist_ok=True)
        # Keep handle open for the process lifetime (logger owns it).
        # pylint: disable-next=consider-using-with
        handle = open(path, "a", encoding="utf-8")
        _maybe_chmod_log_file(path)
        return handle
    except OSError as exc:
        sys.stderr.write(
            f"[cursor_goal] Could not open CURSOR_GOAL_LOG_FILE={raw!r}: {exc}\n"
        )
        return None
    except ValueError as exc:
        sys.stderr.write(f"[cursor_goal] CURSOR_GOAL_LOG_FILE disabled: {exc}\n")
        return None


def _configure_root() -> None:
    """Configure the parent ``cursor_goal`` logger once; children propagate."""
    # Module-level idempotent-init guard + owned file handle; a class
    # singleton would just move the same mutable state one level up.
    global _CONFIGURED, _LOG_FILE_HANDLE  # pylint: disable=global-statement
    if _CONFIGURED:
        return

    root = logging.getLogger("cursor_goal")
    raw_level = os.environ.get("CURSOR_GOAL_LOG", "").strip()
    if raw_level:
        level_name = raw_level.upper()
    elif os.environ.get("CURSOR_GOAL_LOG_FILE", "").strip():
        level_name = "INFO"
    else:
        level_name = "WARNING"
    level = _resolve_level(level_name)
    root.setLevel(level)

    formatter = logging.Formatter(
        "[%(name)s] %(levelname)s: %(message)s [pid=%(process)d]"
    )

    if not any(isinstance(h, logging.StreamHandler) for h in root.handlers):
        stderr_handler = logging.StreamHandler(sys.stderr)
        stderr_handler.setFormatter(formatter)
        root.addHandler(stderr_handler)

    if _LOG_FILE_HANDLE is None:
        _LOG_FILE_HANDLE = _open_log_file()
        if _LOG_FILE_HANDLE is not None:
            file_handler = logging.StreamHandler(_LOG_FILE_HANDLE)
            file_handler.setFormatter(formatter)
            root.addHandler(file_handler)

    root.propagate = False
    _CONFIGURED = True


def get_logger(name: str = "cursor_goal") -> logging.Logger:
    """Return a module logger; handlers live on the parent ``cursor_goal`` logger."""
    _configure_root()
    logger = logging.getLogger(name)
    # Children inherit level/handlers via propagate; do not attach duplicates.
    if name != "cursor_goal":
        logger.setLevel(logging.NOTSET)
        logger.propagate = True
        # Clear any legacy per-module handlers from older installs / reloads.
        if logger.handlers:
            logger.handlers.clear()
    return logger


def _reset_for_tests() -> None:
    """Clear configuration state (test helper only)."""
    # Mirrors _configure_root's module-level state; test-only reset hook.
    global _CONFIGURED, _LOG_FILE_HANDLE  # pylint: disable=global-statement
    root = logging.getLogger("cursor_goal")
    for handler in list(root.handlers):
        root.removeHandler(handler)
        try:
            handler.close()
        except OSError:
            pass
    if _LOG_FILE_HANDLE is not None:
        try:
            _LOG_FILE_HANDLE.close()
        except OSError:
            pass
    _LOG_FILE_HANDLE = None
    _CONFIGURED = False
    root.setLevel(logging.NOTSET)
