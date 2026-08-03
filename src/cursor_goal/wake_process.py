"""PID-file ownership and process-kill helpers for the wake watchdog.

Tracks which OS process owns the wake loop via ``wake.pid`` (pid + generation
token) and provides best-effort cross-platform verification that a PID still
belongs to a cursor-goal / wake harness process before signaling it, so a
stale or reused PID is never killed blindly. Kept separate from
:mod:`cursor_goal.wake` (arming, ticking, loop orchestration) so the
process-control surface is easy to audit on its own.
"""

# Ownership probes and kill paths intentionally branch per-OS.
# pylint: disable=too-many-return-statements,too-many-branches

from __future__ import annotations

import json
import os
import secrets
import signal
import subprocess  # nosec B404 — taskkill / ownership checks only
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from cursor_goal.logging_config import get_logger
from cursor_goal.state import atomic_write_text, data_dir, goal_lock

logger = get_logger("cursor_goal.wake_process")

WAKE_PID_NAME = "wake.pid"
WAKE_ORPHAN_NAME = "wake.orphan"


def wake_pid_path() -> Path:
    return data_dir() / WAKE_PID_NAME


def wake_orphan_path() -> Path:
    return data_dir() / WAKE_ORPHAN_NAME


def mark_orphan_wake(pid: int, reason: str) -> None:
    """Persist a doctor-visible warning about a suspected orphan wake loop."""
    payload = {
        "pid": int(pid),
        "reason": reason[:500],
        "marked_at": _now_iso(),
    }
    try:
        with goal_lock():
            atomic_write_text(
                wake_orphan_path(),
                json.dumps(payload, indent=2) + "\n",
            )
    except OSError as exc:
        logger.warning("Could not write wake orphan marker: %s", exc)


def clear_orphan_wake() -> None:
    """Remove orphan wake marker if present."""
    path = wake_orphan_path()
    try:
        if path.is_file():
            path.unlink()
    except OSError as exc:
        logger.debug("Could not clear wake orphan marker: %s", exc)


def read_orphan_wake() -> dict[str, Any] | None:
    """Return orphan wake marker payload, or None."""
    path = wake_orphan_path()
    if not path.is_file():
        return None
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError, TypeError, ValueError):
        return None
    if not isinstance(data, dict):
        return None
    return data


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")


def _pid_alive(pid: int) -> bool:
    if pid <= 0:
        return False
    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        return False
    except PermissionError:
        return True
    except OSError:
        return False
    return True


def _read_pid_record() -> (  # pylint: disable=too-many-return-statements
    dict[str, Any] | None
):
    """Return ``{pid, token, started_at}`` or None. Accepts legacy plain-int files."""
    path = wake_pid_path()
    if not path.is_file():
        return None
    try:
        raw = path.read_text(encoding="utf-8").strip()
    except OSError:
        return None
    if not raw:
        return None
    try:
        data = json.loads(raw)
    except json.JSONDecodeError:
        try:
            # Legacy plain-int pid files have no ownership token.
            return {
                "pid": int(raw),
                "token": str(),
                "started_at": str(),
            }
        except ValueError:
            return None
    if isinstance(data, int):
        return {
            "pid": int(data),
            "token": str(),
            "started_at": str(),
        }
    if isinstance(data, dict) and "pid" in data:
        try:
            return {
                "pid": int(data["pid"]),
                "token": str(data.get("token") or ""),
                "started_at": str(data.get("started_at") or ""),
            }
        except (TypeError, ValueError):
            return None
    return None


def _read_pid() -> int | None:
    record = _read_pid_record()
    if record is None:
        return None
    return int(record["pid"])


def _write_pid_record(pid: int, token: str) -> None:
    payload = {
        "pid": pid,
        "token": token,
        "started_at": _now_iso(),
    }
    with goal_lock():
        atomic_write_text(
            wake_pid_path(),
            json.dumps(payload, indent=2, ensure_ascii=False) + "\n",
        )
    clear_orphan_wake()


def _write_pid(pid: int, token: str | None = None) -> None:
    """Write pid ownership record (tests may omit token)."""
    _write_pid_record(pid, token if token else secrets.token_hex(8))


def _clear_pid(
    *, only_if_pid: int | None = None, only_if_token: str | None = None
) -> None:
    """Remove wake.pid; optionally only when ownership still matches."""
    path = wake_pid_path()
    if only_if_pid is not None or only_if_token is not None:
        record = _read_pid_record()
        if record is None:
            return
        if only_if_pid is not None and int(record["pid"]) != only_if_pid:
            logger.debug(
                "Skipping clear of wake.pid (pid %s != %s)",
                record["pid"],
                only_if_pid,
            )
            return
        if only_if_token is not None and str(record.get("token") or "") != (
            only_if_token
        ):
            logger.debug("Skipping clear of wake.pid (token mismatch)")
            return
    try:
        path.unlink(missing_ok=True)
    except OSError as exc:
        logger.debug("Could not remove wake pid file: %s", exc)


def _cmdline_looks_owned(cmdline: str) -> bool:
    """Return True when *cmdline* looks like a cursor-goal / wake harness process."""
    lowered = cmdline.strip().lower()
    if not lowered:
        return False
    # Require cursor-goal identity — never match bare "wake" (false-positive kill).
    if "cursor_goal" in lowered or "cursor-goal" in lowered:
        return True
    if "run_goal.py" in lowered:
        return True
    return False


def _windows_pid_looks_owned(pid: int) -> bool:
    """Best-effort: confirm PID exists and command line mentions wake/goal."""
    try:
        # Cast to Any so tests can monkeypatch run → None without mypy
        # treating the None guard as unreachable.
        completed: Any = subprocess.run(  # nosec B603 B607 — fixed powershell args
            [
                "powershell",
                "-NoProfile",
                "-Command",
                (
                    f'(Get-CimInstance Win32_Process -Filter "ProcessId={int(pid)}")'
                    f".CommandLine"
                ),
            ],
            capture_output=True,
            text=True,
            timeout=5,
            check=False,
        )
    except (OSError, subprocess.TimeoutExpired) as exc:
        logger.debug("Windows ownership probe failed for pid=%s: %s", pid, exc)
        return False
    if completed is None:
        return False
    return _cmdline_looks_owned(getattr(completed, "stdout", None) or "")


def _unix_pid_looks_owned(pid: int) -> bool:
    """Best-effort: confirm PID cmdline mentions wake/goal (Linux /proc or ps)."""
    proc_cmdline = Path(f"/proc/{int(pid)}/cmdline")
    if proc_cmdline.is_file():
        try:
            raw = proc_cmdline.read_bytes().replace(b"\x00", b" ")
            return _cmdline_looks_owned(raw.decode("utf-8", errors="replace"))
        except OSError as exc:
            logger.debug("Unix /proc ownership probe failed for pid=%s: %s", pid, exc)
            return False
    # macOS / other Unix without /proc: fall back to ps.
    try:
        completed: Any = subprocess.run(  # nosec B603 B607 — fixed ps argv
            ["ps", "-p", str(int(pid)), "-o", "command="],
            capture_output=True,
            text=True,
            timeout=5,
            check=False,
        )
    except (OSError, subprocess.TimeoutExpired) as exc:
        logger.debug("Unix ps ownership probe failed for pid=%s: %s", pid, exc)
        return False
    if completed is None or getattr(completed, "returncode", None) != 0:
        return False
    return _cmdline_looks_owned(getattr(completed, "stdout", None) or "")


def _pid_looks_owned(pid: int) -> bool:
    """Best-effort: confirm *pid* still looks like a cursor-goal wake process."""
    if pid <= 0:
        return False
    if os.name == "nt":
        return _windows_pid_looks_owned(pid)
    return _unix_pid_looks_owned(pid)  # pragma: no cover — Unix CI


def _kill_pid(pid: int, *, token: str | None = None) -> None:
    """Signal a wake loop process. Verify ownership before kill (PID reuse)."""
    if pid <= 0:
        return
    if pid == os.getpid():
        logger.debug("Skipping kill of current process pid=%s", pid)
        return
    if not _pid_alive(pid):
        return

    # Legacy plain-int wake.pid has no ownership token — only kill when the
    # cmdline ownership probe confirms this still looks like a wake loop.
    if not token:
        if not _pid_looks_owned(pid):
            logger.warning(
                "Refusing to kill pid=%s: missing wake ownership token and "
                "ownership check failed (legacy wake.pid or unverified). "
                "Clear wake.pid manually or re-arm wake after disarm.",
                pid,
            )
            return
        logger.warning(
            "Killing legacy tokenless wake pid=%s after ownership probe OK",
            pid,
        )
    else:
        record = _read_pid_record()
        if record is not None:
            stored = str(record.get("token") or "")
            if stored and stored != token:
                logger.warning("Refusing to kill pid=%s: wake token mismatch", pid)
                return
            if int(record.get("pid", -1)) != pid:
                logger.warning(
                    "Refusing to kill pid=%s: wake.pid points elsewhere",
                    pid,
                )
                return

    if os.name == "nt":
        if not _windows_pid_looks_owned(pid):
            logger.warning(
                "Refusing Windows kill of pid=%s: ownership check failed "
                "(possible PID reuse)",
                pid,
            )
            return
        try:
            subprocess.run(  # nosec B603 B607 — taskkill with integer PID only
                ["taskkill", "/PID", str(int(pid)), "/T", "/F"],
                capture_output=True,
                text=True,
                timeout=10,
                check=False,
            )
        except (OSError, subprocess.TimeoutExpired) as exc:
            logger.debug("taskkill failed for pid %s: %s", pid, exc)
        return

    if not _unix_pid_looks_owned(pid):  # pragma: no cover — Unix CI
        logger.warning(
            "Refusing Unix kill of pid=%s: ownership check failed "
            "(possible PID reuse)",
            pid,
        )
        return
    try:  # pragma: no cover — SIGTERM path covered on Unix CI
        os.kill(pid, signal.SIGTERM)
    except OSError as exc:  # pragma: no cover
        logger.debug("Could not signal wake pid %s: %s", pid, exc)
