"""Cross-process exclusive lock helpers for goal.json mutations."""

from __future__ import annotations

import os
import sys
import time
from typing import IO


class GoalLockTimeoutError(OSError):
    """Raised when the exclusive goal.lock cannot be acquired in time."""


def lock_timeout_message(timeout_sec: float) -> str:
    approx = int(timeout_sec) if timeout_sec == int(timeout_sec) else timeout_sec
    return (
        f"Could not acquire goal.lock within ~{approx}s; another process may "
        "be holding it. Retry, or check for a stuck cursor-goal process."
    )


def lock_acquire(handle: IO[bytes], timeout_sec: float) -> None:
    """Acquire an exclusive lock on *handle* within *timeout_sec* seconds."""
    # Platform-gated imports: msvcrt/fcntl are OS-specific and must not be
    # imported at module import time on the wrong platform (ImportError).
    # Documented exception to the no-inline-imports workspace rule.
    if sys.platform == "win32":
        import msvcrt  # isort: skip  # pylint: disable=import-outside-toplevel

        # Ensure the lock region exists without reading byte 0 (a concurrent
        # holder of msvcrt.locking makes read() raise PermissionError).
        handle.seek(0, os.SEEK_END)
        if handle.tell() == 0:
            handle.write(b"\0")
            handle.flush()
        handle.seek(0)
        deadline = time.monotonic() + timeout_sec
        while True:
            try:
                msvcrt.locking(handle.fileno(), msvcrt.LK_NBLCK, 1)
                return
            except OSError as exc:
                if time.monotonic() >= deadline:
                    raise GoalLockTimeoutError(
                        lock_timeout_message(timeout_sec)
                    ) from exc
                time.sleep(0.05)
    else:
        import fcntl  # isort: skip  # pylint: disable=import-outside-toplevel

        deadline = time.monotonic() + timeout_sec
        while True:
            try:
                fcntl.flock(handle.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
                return
            except BlockingIOError as exc:
                if time.monotonic() >= deadline:
                    raise GoalLockTimeoutError(
                        lock_timeout_message(timeout_sec)
                    ) from exc
                time.sleep(0.05)


def lock_release(handle: IO[bytes]) -> None:
    """Release a lock previously acquired with :func:`lock_acquire`."""
    # See lock_acquire: platform-gated inline import is intentional.
    if sys.platform == "win32":
        import msvcrt  # isort: skip  # pylint: disable=import-outside-toplevel

        try:
            handle.seek(0)
            msvcrt.locking(handle.fileno(), msvcrt.LK_UNLCK, 1)
        except OSError as exc:
            sys.stderr.write(
                f"[cursor_goal.fs_lock] debug: Windows unlock failed: {exc}\n"
            )
    else:
        import fcntl  # isort: skip  # pylint: disable=import-outside-toplevel

        fcntl.flock(handle.fileno(), fcntl.LOCK_UN)
