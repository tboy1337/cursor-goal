"""Windows ACL harden helpers for the goal data directory."""

from __future__ import annotations

import os
import re
import shutil
import subprocess  # nosec B404 — Windows ACL via icacls only
from pathlib import Path

from cursor_goal.logging_config import get_logger

logger = get_logger("cursor_goal.win_acl")

# Paths successfully hardened this process.
HARDENED_PATHS: set[str] = set()
# Paths where Windows ACL harden failed in a way that should fail doctor.
ACL_HARDEN_FAILURES: dict[str, str] = {}
# Windows DOMAIN\user or local user for icacls — reject metacharacters.
_WINDOWS_USERNAME_RE = re.compile(r"^[A-Za-z0-9._$\\-]+$")


def windows_username() -> str | None:
    """Best-effort current Windows username for icacls grants.

    Rejects values with characters that could alter icacls grant syntax.
    """
    candidates: list[str] = []
    for key in ("USERNAME", "USER"):
        value = os.environ.get(key, "").strip()
        if value:
            candidates.append(value)
    try:
        login = os.getlogin()
        if login:
            candidates.append(login.strip())
    except OSError:
        pass
    for user in candidates:
        if _WINDOWS_USERNAME_RE.fullmatch(user):
            return user
        logger.warning(
            "Ignoring unsafe Windows username for ACL harden: %r",
            user[:64],
        )
    return None


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


def harden_windows_acl(path: Path) -> None:
    """Best-effort private ACL via icacls (no hard deps).

    Tries ``/inheritance:r`` then grants the current user full control. If
    inheritance strip fails, records a doctor hard-fail and returns without
    marking the path hardened. If the grant fails after inheritance was
    stripped, restores inheritance (``/inheritance:e``), records a doctor
    hard-fail, and logs loudly. ``CURSOR_GOAL_SKIP_ACL=1`` is
    test/emergency-only.
    """
    if os.name != "nt" or acl_harden_disabled():
        return
    key = str(path)
    if key in HARDENED_PATHS:
        return
    user = windows_username()
    if not user:
        record_acl_failure(path, "could not determine a safe Windows username")
        return
    icacls = shutil.which("icacls")
    if not icacls:
        record_acl_failure(path, "icacls not found on PATH")
        return
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
            return
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
        return
    if completed.returncode != 0:
        err = (completed.stderr or completed.stdout or "").strip()
        detail = err[:200] or f"exit={completed.returncode}"
        restore_windows_acl_inheritance(icacls, path)
        record_acl_failure(
            path,
            f"inheritance stripped but grant failed ({detail})",
        )
        return
    ACL_HARDEN_FAILURES.pop(key, None)
    HARDENED_PATHS.add(key)
    logger.debug(
        "Windows ACL hardened path=%s user=%s inheritance_stripped=%s",
        path,
        user,
        inheritance_stripped,
    )


def failure_reason(path: Path) -> str | None:
    """Return recorded ACL harden failure reason for *path*, if any."""
    return ACL_HARDEN_FAILURES.get(str(path))
