"""Windows ACL harden helpers for the goal data directory."""

from __future__ import annotations

import ctypes
import os
import re
import subprocess  # nosec B404 — Windows ACL via icacls only
import sys
from pathlib import Path

from cursor_goal.logging_config import get_logger
from cursor_goal.native_path import windows_system_root_file

logger = get_logger("cursor_goal.win_acl")

# Paths successfully hardened this process.
HARDENED_PATHS: set[str] = set()
# Paths where Windows ACL harden failed in a way that should fail doctor.
ACL_HARDEN_FAILURES: dict[str, str] = {}
# Windows DOMAIN\user or local user for icacls — reject metacharacters.
_WINDOWS_USERNAME_RE = re.compile(r"^[A-Za-z0-9._$\\-]+$")


def _safe_username(value: str | None) -> str | None:
    """Return *value* when it is a safe icacls grant identity, else None."""
    if not value:
        return None
    text = value.strip()
    if not text:
        return None
    if _WINDOWS_USERNAME_RE.fullmatch(text):
        return text
    logger.warning(
        "Ignoring unsafe Windows username for ACL harden: %r",
        text[:64],
    )
    return None


def _is_win32() -> bool:
    """True on native Windows.

    A function (not an inline ``sys.platform`` test) so mypy with
    ``warn_unreachable`` does not treat the opposite platform's body as dead.
    """
    return sys.platform == "win32"


def _windows_logon_name() -> str | None:
    """Current Windows logon name via GetUserNameW (not spoofable env)."""
    if not _is_win32():
        return None
    windll = getattr(ctypes, "windll", None)
    if windll is None:
        return None
    try:
        get_user_name = windll.advapi32.GetUserNameW
    except AttributeError:
        return None
    size = ctypes.c_ulong(256)
    buf = ctypes.create_unicode_buffer(size.value)
    try:
        if not get_user_name(buf, ctypes.byref(size)):
            if size.value <= 1:
                return None
            buf = ctypes.create_unicode_buffer(size.value)
            if not get_user_name(buf, ctypes.byref(size)):
                return None
    except (OSError, ValueError, TypeError) as exc:
        logger.debug("GetUserNameW failed: %s", exc)
        return None
    return _safe_username(buf.value)


def _env_usernames() -> list[str]:
    found: list[str] = []
    for key in ("USERNAME", "USER"):
        value = os.environ.get(key, "").strip()
        if value:
            found.append(value)
    return found


def _warn_env_username_mismatch(os_name: str) -> None:
    for env_user in _env_usernames():
        if env_user != os_name:
            logger.warning(
                "Ignoring spoofable USERNAME/USER %r; using OS logon %r",
                env_user[:64],
                os_name[:64],
            )


def _windows_os_identity() -> str | None:
    """OS logon name on real Windows, or None when unavailable / not Windows."""
    if not _is_win32():
        return None
    os_name = _windows_logon_name()
    if os_name is not None:
        return os_name
    try:
        return _safe_username(os.getlogin())
    except OSError:
        return None


def windows_username() -> str | None:
    """Best-effort current Windows username for icacls grants.

    On real Windows, prefers ``GetUserNameW`` then ``os.getlogin()`` over
    spoofable ``USERNAME`` / ``USER`` environment variables. Env is a
    last-resort fallback (and the path Linux tests use when ``os.name`` is
    patched to ``nt``). Rejects values with characters that could alter
    icacls grant syntax.
    """
    os_name = _windows_os_identity()
    if os_name is not None:
        _warn_env_username_mismatch(os_name)
        return os_name

    for user in _env_usernames():
        safe = _safe_username(user)
        if safe is not None:
            return safe
    try:
        return _safe_username(os.getlogin())
    except OSError:
        return None


def _pinned_icacls() -> str | None:
    """Absolute System32 icacls.exe, or None (never PATH)."""
    pinned = windows_system_root_file("System32", "icacls.exe")
    return str(pinned) if pinned is not None else None


def acl_harden_disabled() -> bool:
    """Return True when ACL harden is skipped (tests / explicit opt-out)."""
    raw = os.environ.get("CURSOR_GOAL_SKIP_ACL", "").strip().lower()
    return raw in {"1", "true", "yes", "on"}


def record_acl_failure(path: Path, reason: str) -> None:
    key = str(path)
    ACL_HARDEN_FAILURES[key] = reason
    logger.error("Windows ACL harden failure for %s: %s", path, reason)


def restore_windows_acl_inheritance(icacls: str, path: Path) -> None:
    """Best-effort restore of ACL inheritance after a failed grant."""
    try:
        restore = subprocess.run(  # nosec B603
            [icacls, str(path), "/inheritance:e"],
            capture_output=True,
            text=True,
            timeout=15,
            check=False,
        )
    except (OSError, subprocess.TimeoutExpired) as exc:
        logger.error(
            "Failed to restore ACL inheritance for %s after grant failure: %s",
            path,
            exc,
        )
        return
    if restore.returncode != 0:
        err = (restore.stderr or restore.stdout or "").strip()
        logger.error(
            "ACL inheritance restore failed for %s: %s",
            path,
            err[:200] or f"exit={restore.returncode}",
        )
        return
    logger.warning(
        "Restored ACL inheritance for %s after failed grant "
        "(directory may still need manual lockdown)",
        path,
    )


def harden_windows_acl(path: Path, *, force: bool = False) -> bool:
    """Best-effort private ACL via icacls (no hard deps).

    Tries ``/inheritance:r`` then grants the current user full control. If
    inheritance strip fails, records a doctor hard-fail and returns ``False``
    without marking the path hardened. If the grant fails after inheritance was
    stripped, restores inheritance (``/inheritance:e``), records a doctor
    hard-fail, and returns ``False``. ``CURSOR_GOAL_SKIP_ACL=1`` is
    test/emergency-only.

    Returns ``True`` when harden succeeded, was skipped (non-Windows / SKIP_ACL),
    or was already hardened this process (unless *force*). Returns ``False`` when
    harden was attempted and failed.
    """
    if os.name != "nt" or acl_harden_disabled():
        return True
    key = str(path)
    if force:
        HARDENED_PATHS.discard(key)
    if key in HARDENED_PATHS:
        return True
    user = windows_username()
    if not user:
        record_acl_failure(path, "could not determine a safe Windows username")
        return False
    icacls = _pinned_icacls()
    if not icacls:
        record_acl_failure(
            path,
            "icacls.exe not found under %SystemRoot%\\System32 (PATH is not used)",
        )
        return False
    grant = f"{user}:(OI)(CI)F" if path.is_dir() else f"{user}:F"
    inheritance_stripped = False
    try:
        strip = subprocess.run(  # nosec B603
            [icacls, str(path), "/inheritance:r"],
            capture_output=True,
            text=True,
            timeout=15,
            check=False,
        )
        if strip.returncode != 0:
            err = (strip.stderr or strip.stdout or "").strip()
            detail = err[:200] or f"exit={strip.returncode}"
            record_acl_failure(
                path,
                f"inheritance strip failed ({detail})",
            )
            return False
        inheritance_stripped = True
        completed = subprocess.run(  # nosec B603
            [icacls, str(path), "/grant:r", grant],
            capture_output=True,
            text=True,
            timeout=15,
            check=False,
        )
    except (OSError, subprocess.TimeoutExpired) as exc:
        if inheritance_stripped:
            restore_windows_acl_inheritance(icacls, path)
            record_acl_failure(
                path,
                f"inheritance stripped but grant raised: {exc}",
            )
        else:
            record_acl_failure(path, f"icacls failed: {exc}")
        return False
    if completed.returncode != 0:
        err = (completed.stderr or completed.stdout or "").strip()
        detail = err[:200] or f"exit={completed.returncode}"
        restore_windows_acl_inheritance(icacls, path)
        record_acl_failure(
            path,
            f"inheritance stripped but grant failed ({detail})",
        )
        return False
    ACL_HARDEN_FAILURES.pop(key, None)
    HARDENED_PATHS.add(key)
    logger.debug(
        "Windows ACL hardened path=%s user=%s inheritance_stripped=%s",
        path,
        user,
        inheritance_stripped,
    )
    return True


def failure_reason(path: Path) -> str | None:
    """Return recorded ACL harden failure reason for *path*, if any."""
    return ACL_HARDEN_FAILURES.get(str(path))
