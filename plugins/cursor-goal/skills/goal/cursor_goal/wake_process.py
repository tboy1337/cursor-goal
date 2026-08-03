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

import ctypes
import json
import os
import signal
import subprocess  # nosec B404 — taskkill / ownership checks only
import time
from pathlib import Path
from typing import Any

from cursor_goal.logging_config import get_logger
from cursor_goal.state import atomic_write_text, data_dir, goal_lock
from cursor_goal.state import now_iso as _now_iso

logger = get_logger("cursor_goal.wake_process")

WAKE_PID_NAME = "wake.pid"
WAKE_ORPHAN_NAME = "wake.orphan"
# Bounded grace period after SIGTERM before escalating to SIGKILL (Unix).
_SIGTERM_GRACE_S = 2.0
_SIGTERM_POLL_S = 0.1


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
        data = json.loads(path.read_text(encoding="utf-8-sig"))
    except (OSError, json.JSONDecodeError, TypeError, ValueError):
        return None
    if not isinstance(data, dict):
        return None
    return data


# Windows-only OpenProcess/GetExitCodeProcess constants for _windows_pid_alive.
_PROCESS_QUERY_LIMITED_INFORMATION = 0x1000
_STILL_ACTIVE = 259


def _windows_pid_alive(pid: int) -> bool:
    """Windows liveness probe via OpenProcess + GetExitCodeProcess.

    ``os.kill(pid, 0)`` is NOT a safe liveness probe on Windows: CPython's
    Windows shim only special-cases ``CTRL_C_EVENT``/``CTRL_BREAK_EVENT``,
    and for any other signal value (including ``0``) it calls
    ``TerminateProcess(pid, sig)`` — i.e. ``os.kill(pid, 0)`` unconditionally
    **kills** the target process (with exit code 0) instead of merely
    checking whether it exists. Never call ``os.kill(..., 0)`` on Windows.
    """
    kernel32 = getattr(ctypes, "windll", None)
    if kernel32 is None:
        # Host has no Win32 APIs (e.g. posix test with os.name mocked).
        return False
    handle = None
    try:
        handle = kernel32.kernel32.OpenProcess(
            _PROCESS_QUERY_LIMITED_INFORMATION, False, int(pid)
        )
        if not handle:
            return False
        exit_code = ctypes.c_ulong(0)
        ok = kernel32.kernel32.GetExitCodeProcess(handle, ctypes.byref(exit_code))
        if not ok:
            return False
        return exit_code.value == _STILL_ACTIVE
    except (AttributeError, OSError, ValueError, TypeError) as exc:
        logger.debug("Windows liveness probe failed for pid=%s: %s", pid, exc)
        return False
    finally:
        if handle:
            kernel32.kernel32.CloseHandle(handle)


def _pid_alive(pid: int) -> bool:
    if pid <= 0:
        return False
    if os.name == "nt":
        return _windows_pid_alive(pid)
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
    """Return ``{pid, token, started_at}`` or None.

    Tokened JSON records only. Plain-int / tokenless files are cleared and
    treated as absent (never used for kill).
    """
    path = wake_pid_path()
    if not path.is_file():
        return None
    try:
        # utf-8-sig tolerates a BOM (e.g. from a Windows editor).
        raw = path.read_text(encoding="utf-8-sig").strip()
    except OSError:
        return None
    if not raw:
        return None
    try:
        data = json.loads(raw)
    except json.JSONDecodeError:
        logger.warning("Clearing unreadable wake.pid (expected tokened JSON)")
        try:
            path.unlink(missing_ok=True)
        except OSError as exc:
            logger.debug("Could not remove bad wake.pid: %s", exc)
        return None
    if isinstance(data, int):
        logger.warning("Clearing tokenless plain-int wake.pid=%s", data)
        if _pid_alive(int(data)):
            mark_orphan_wake(
                int(data),
                "tokenless plain-int wake.pid; kill refused (token required)",
            )
        try:
            path.unlink(missing_ok=True)
        except OSError as exc:
            logger.debug("Could not remove tokenless wake.pid: %s", exc)
        return None
    if isinstance(data, dict) and "pid" in data:
        try:
            token = str(data.get("token") or "")
            pid = int(data["pid"])
        except (TypeError, ValueError):
            return None
        if not token:
            logger.warning("Clearing wake.pid pid=%s with empty ownership token", pid)
            if _pid_alive(pid):
                mark_orphan_wake(
                    pid,
                    "wake.pid missing ownership token; kill refused",
                )
            try:
                path.unlink(missing_ok=True)
            except OSError as exc:
                logger.debug("Could not remove tokenless wake.pid: %s", exc)
            return None
        return {
            "pid": pid,
            "token": token,
            "started_at": str(data.get("started_at") or ""),
        }
    return None


def _read_pid() -> int | None:
    record = _read_pid_record()
    if record is None:
        return None
    return int(record["pid"])


def _write_pid_record(pid: int, token: str) -> None:
    if not token:
        raise ValueError("wake.pid ownership token is required")
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
    """Return True when *cmdline* looks like a cursor-goal wake-loop process.

    Require wake-loop identity — never match bare ``cursor-goal`` /
    ``run_goal.py`` substrings alone (pytest/IDE paths under a clone would
    false-positive and risk killing the wrong PID on reuse).
    """
    lowered = cmdline.strip().lower()
    if not lowered:
        return False
    # Classic / marketplace launcher scripts for the wake loop.
    if "wake_loop.cmd" in lowered or "wake_loop.sh" in lowered:
        return True
    padded = f" {lowered} "
    has_wake_token = " wake " in padded
    if not has_wake_token:
        return False
    return (
        "cursor_goal" in lowered or "cursor-goal" in lowered or "run_goal.py" in lowered
    )


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

    if not token:
        logger.warning(
            "Refusing to kill pid=%s: wake ownership token is required",
            pid,
        )
        return

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
        return
    # A hung loop (e.g. blocked in a syscall) can survive SIGTERM; escalate
    # to SIGKILL after a bounded grace period so disarm never leaves an
    # orphaned process behind.
    deadline = time.monotonic() + _SIGTERM_GRACE_S
    while time.monotonic() < deadline:  # pragma: no cover — Unix CI
        if not _pid_alive(pid):
            return
        time.sleep(_SIGTERM_POLL_S)
    if _pid_alive(pid):  # pragma: no cover — Unix CI
        logger.warning(
            "wake pid=%s still alive %.1fs after SIGTERM; escalating to SIGKILL",
            pid,
            _SIGTERM_GRACE_S,
        )
        # getattr, not signal.SIGKILL directly: this branch only runs on Unix
        # at runtime (Windows returns earlier via taskkill above), but
        # typeshed hides SIGKILL from the signal module stub entirely when
        # type-checking for win32, so a direct attribute reference is a false
        # positive on Windows dev machines / CI legs.
        sigkill = getattr(signal, "SIGKILL", None)
        if sigkill is None:  # pragma: no cover — defensive; Unix always has it
            logger.debug("SIGKILL unavailable on this platform for pid %s", pid)
            return
        try:
            os.kill(pid, sigkill)
        except OSError as exc:
            logger.debug("SIGKILL failed for wake pid %s: %s", pid, exc)
