"""Path-trust security boundary for the goal data directory and workdir jail.

Centralizes symlink/reparse-point detection, the configured data directory
resolution, and the Windows ACL hardening checks so callers cannot bypass the
trust boundary by resolving through a leaf symlink/junction. Kept separate
from :mod:`cursor_goal.state` (schema, locks, atomic IO) so the security
surface is easy to audit on its own.
"""

from __future__ import annotations

import ctypes
import os
import stat
from pathlib import Path

from cursor_goal.logging_config import get_logger
from cursor_goal.native_path import native_path, path_str_is_absolute
from cursor_goal.win_acl import ACL_HARDEN_FAILURES as _ACL_HARDEN_FAILURES
from cursor_goal.win_acl import HARDENED_PATHS as _HARDENED_PATHS
from cursor_goal.win_acl import failure_reason as _acl_failure_reason
from cursor_goal.win_acl import harden_windows_acl as _harden_windows_acl

logger = get_logger("cursor_goal.path_trust")

_FILE_ATTRIBUTE_REPARSE_POINT = 0x400
_INVALID_FILE_ATTRIBUTES = 0xFFFFFFFF


def configured_data_dir_path() -> Path:
    """Return the configured data dir path without resolving symlinks.

    ``CURSOR_GOAL_DATA`` must be absolute when set (same policy as
    ``CURSOR_GOAL_HOME``).
    """
    override = os.environ.get("CURSOR_GOAL_DATA")
    if override:
        if not path_str_is_absolute(override):
            raise ValueError(
                f"CURSOR_GOAL_DATA must be an absolute path (got {override!r})"
            )
        # Use host-native Path class — Path() dispatches on live os.name (3.13+).
        return native_path(override)
    return native_path(os.path.join(os.path.expanduser("~"), ".cursor-goal", "data"))


def _absolute_without_resolve(path: Path) -> Path:
    """Make *path* absolute without following symlinks."""
    expanded = native_path(path)
    if path_str_is_absolute(str(expanded)):
        return expanded
    return native_path(os.path.join(os.getcwd(), str(expanded)))


def _windows_path_is_reparse_point(path: Path) -> bool:
    """Return True when *path* is a symlink, junction, or other reparse point."""
    try:
        if path.is_symlink():
            return True
    except OSError as exc:
        logger.debug("Could not check symlink for %s: %s", path, exc)
    try:
        # FILE_ATTRIBUTE_REPARSE_POINT via Win32 (junctions may not be is_symlink).
        kernel32 = getattr(ctypes, "windll", None)
        if kernel32 is None:
            return False
        get_attrs = kernel32.kernel32.GetFileAttributesW
        get_attrs.restype = ctypes.c_uint32
        attrs = int(get_attrs(str(path)))
    except (AttributeError, OSError, ValueError, TypeError) as exc:
        logger.debug("GetFileAttributesW failed for %s: %s", path, exc)
        return False
    if attrs == _INVALID_FILE_ATTRIBUTES:
        return False
    return bool(attrs & _FILE_ATTRIBUTE_REPARSE_POINT)


def path_has_symlink_or_reparse(  # pylint: disable=too-many-nested-blocks
    path: Path,
) -> bool:
    """Return True if *path* or any existing ancestor is a symlink/reparse.

    Checks the unresolved path chain so ``Path.resolve()`` cannot bypass the
    trust boundary by following a leaf symlink/junction to a normal directory.
    """
    try:
        current = _absolute_without_resolve(path)
    except OSError as exc:
        logger.debug("Could not absolutize path %s: %s", path, exc)
        current = path
    chain: list[Path] = []
    cursor = current
    while True:
        chain.append(cursor)
        parent = cursor.parent
        if parent == cursor:
            break
        cursor = parent
    for candidate in chain:
        try:
            if os.name == "nt":
                if _windows_path_is_reparse_point(candidate):
                    return True
            elif candidate.is_symlink():
                return True
        except OSError as exc:  # pragma: no cover — rare FS races
            logger.debug("Link check failed for %s: %s", candidate, exc)
    return False


def allow_any_workdir() -> bool:
    """Return True when workdir jail is disabled (power-user / tests)."""
    raw = os.environ.get("CURSOR_GOAL_ALLOW_ANY_WORKDIR", "").strip().lower()
    return raw in {"1", "true", "yes", "on"}


def normalize_workdir(raw: str, *, jail_root: Path | None = None) -> str:
    """Expand and validate workdir; return absolute path or raise ValueError.

    Refuses symlink/junction/reparse chains. Unless
    ``CURSOR_GOAL_ALLOW_ANY_WORKDIR=1``, requires the resolved path under
    *jail_root* (default: process cwd).
    """
    text = raw.strip()
    if not text:
        return ""
    path = Path(text).expanduser()
    if not path.is_absolute():
        path = Path.cwd() / path
    if path_has_symlink_or_reparse(path):
        raise ValueError(
            f"workdir must not be a symlink, junction, or reparse point: {path}"
        )
    try:
        resolved = path.resolve()
    except OSError as exc:
        raise ValueError(f"workdir could not be resolved: {path} ({exc})") from exc
    if not resolved.is_dir():
        raise ValueError(f"workdir is not a directory: {resolved}")
    if not allow_any_workdir():
        root = (jail_root if jail_root is not None else Path.cwd()).resolve()
        if not resolved.is_relative_to(root):
            raise ValueError(
                f"workdir must be under {root} "
                "(set CURSOR_GOAL_ALLOW_ANY_WORKDIR=1 to override)"
            )
    return str(resolved)


def assert_workdir_usable(workdir: str) -> str:
    """Re-check a stored workdir at validate time; raise ValueError if unusable.

    Does not re-apply the create-time cwd jail (cwd may change mid-goal); still
    refuses missing dirs and symlink/reparse chains.
    """
    text = workdir.strip()
    if not text:
        return ""
    path = Path(text).expanduser()
    if path_has_symlink_or_reparse(path):
        raise ValueError(
            f"workdir must not be a symlink, junction, or reparse point: {path}"
        )
    if not path.is_dir():
        raise ValueError(f"Configured workdir missing or not a directory: {path}")
    try:
        return str(path.resolve())
    except OSError as exc:
        raise ValueError(f"workdir could not be resolved: {path} ({exc})") from exc


def _chmod_dir_private(path: Path) -> None:
    """Best-effort restrictive directory permissions (0700) on Unix."""
    if os.name == "nt":
        return
    try:
        os.chmod(path, 0o700)
    except OSError as exc:
        logger.debug("Could not chmod data dir %s: %s", path, exc)


def _warn_if_world_writable(path: Path) -> None:
    """Log a loud warning when the data dir is world-writable (Unix)."""
    if os.name == "nt":
        return
    try:
        mode = path.stat().st_mode
    except OSError as exc:
        logger.debug("Could not stat data dir %s: %s", path, exc)
        return
    if mode & (stat.S_IWOTH | stat.S_IWGRP):
        logger.warning(
            "Goal data directory is group/world-writable (%s); "
            "treat goal.json as trusted-user state and restrict permissions",
            path,
        )


def data_dir(*, check_writable: bool = True) -> Path:
    """Resolve the goal data directory (CURSOR_GOAL_DATA or ~/.cursor-goal/data).

    Refuses to mkdir through a symlink/reparse configured path: returns the
    absolute unresolved path so callers can report insecurity without writing
    into the link target.
    """
    raw = configured_data_dir_path()
    if os.environ.get("CURSOR_GOAL_DATA"):
        logger.debug("Using CURSOR_GOAL_DATA override path=%s", raw)
    if path_has_symlink_or_reparse(raw):
        abs_raw = _absolute_without_resolve(raw)
        logger.warning(
            "Configured data dir contains symlink/reparse; refusing mkdir (%s)",
            abs_raw,
        )
        return abs_raw
    path = raw.resolve()
    path.mkdir(parents=True, exist_ok=True, mode=0o700)
    _chmod_dir_private(path)
    _harden_windows_acl(path)
    if check_writable:
        _warn_if_world_writable(path)
    return path


def data_dir_is_insecure(  # pylint: disable=too-many-return-statements,too-many-branches
    path: Path | None = None,
) -> bool:
    """Return True when the data dir is unsafe to trust.

    Unix: symlink/reparse in the configured path chain, not owned by the
    current user, or group/world-writable.
    Windows: symlink / junction / reparse point in the path chain (ACL trust
    uses ``refuse_if_acl_harden_failed`` separately).

    When *path* is None, checks the **unresolved** configured path first so
    ``resolve()`` cannot hide a leaf symlink/junction.
    """
    if path is None:
        configured = configured_data_dir_path()
        if path_has_symlink_or_reparse(configured):
            logger.warning(
                "Goal data directory path contains symlink/reparse (%s)",
                configured,
            )
            return True
        target = data_dir(check_writable=False)
        # data_dir may return unresolved path when links were detected above;
        # re-check in case of races.
        if path_has_symlink_or_reparse(configured):
            return True
    else:
        if path_has_symlink_or_reparse(path):
            logger.warning(
                "Goal data directory path contains symlink/reparse (%s)",
                path,
            )
            return True
        target = path
    if os.name == "nt":
        if _windows_path_is_reparse_point(target):
            logger.warning(
                "Goal data directory is a reparse point/symlink/junction (%s)",
                target,
            )
            return True
        return False
    try:
        if target.is_symlink():
            logger.warning("Goal data directory is a symlink (%s)", target)
            return True
        st = target.lstat()
    except OSError as exc:
        logger.warning(
            "Could not lstat data dir %s (%s); treating as insecure",
            target,
            exc,
        )
        return True
    try:
        getuid = getattr(os, "getuid", None)
        if getuid is None:  # pragma: no cover — Windows
            return bool(st.st_mode & (stat.S_IWOTH | stat.S_IWGRP))
        uid = int(getuid())  # pylint: disable=not-callable
    except OSError:  # pragma: no cover
        return bool(st.st_mode & (stat.S_IWOTH | stat.S_IWGRP))
    if uid not in (st.st_uid, 0):
        logger.warning(
            "Goal data directory not owned by current user (%s uid=%s owner=%s)",
            target,
            uid,
            st.st_uid,
        )
        return True
    return bool(st.st_mode & (stat.S_IWOTH | stat.S_IWGRP))


def refuse_if_data_dir_insecure() -> str | None:
    """Return an error message if the data dir is insecure, else None."""
    if not data_dir_is_insecure():
        return None
    path = configured_data_dir_path()
    display = str(_absolute_without_resolve(path))
    if os.name == "nt":
        return (
            f"[goal] Error: data directory is insecure ({display}). "
            "It must not be a symlink, junction, or other reparse point. "
            "Set CURSOR_GOAL_DATA to a normal private directory."
        )
    return (
        f"[goal] Error: data directory is insecure ({display}). "
        "It must not be a symlink, must be owned by you, and must not be "
        "group/world-writable. Restrict permissions (e.g. chmod 700) or set "
        "CURSOR_GOAL_DATA to a private directory."
    )


def acl_harden_failure_message(path: Path | None = None) -> str | None:
    """Return doctor FAIL text when Windows ACL harden failed for *path*."""
    if os.name != "nt":
        return None
    target = path if path is not None else data_dir(check_writable=False)
    reason = _acl_failure_reason(target)
    if not reason:
        return None
    return (
        f"Windows ACL harden failed for {target}: {reason}. "
        "Verify only you can access the data directory. "
        "CURSOR_GOAL_SKIP_ACL=1 is test/emergency-only after manually locking "
        "down the path."
    )


def refuse_if_acl_harden_failed(path: Path | None = None) -> str | None:
    """Return an error message when Windows ACL harden failed, else None.

    Ensures ``data_dir()`` has run so harden is attempted first. Skip with
    ``CURSOR_GOAL_SKIP_ACL=1`` (no failure recorded).
    """
    if os.name != "nt":
        return None
    target = path if path is not None else data_dir(check_writable=False)
    detail = acl_harden_failure_message(target)
    if detail is None:
        return None
    return f"[goal] Error: {detail}"
