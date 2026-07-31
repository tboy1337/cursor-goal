"""Goal state file paths and atomic JSON I/O."""

from __future__ import annotations

import hashlib
import json
import os
import secrets
import stat
import sys
from collections.abc import Callable
from contextlib import contextmanager
from dataclasses import asdict, dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterator

from cursor_goal.logging_config import get_logger

logger = get_logger("cursor_goal.state")

EVAL_FLAG_NAME = "goal-eval-done"
GOAL_FILE_NAME = "goal.json"
LOCK_FILE_NAME = "goal.lock"
SCHEMA_VERSION = 2
SUPPORTED_SCHEMA_VERSIONS = frozenset({1, 2})


def data_dir() -> Path:
    """Resolve the goal data directory (CURSOR_GOAL_DATA or ~/.cursor-goal/data)."""
    override = os.environ.get("CURSOR_GOAL_DATA")
    if override:
        path = Path(override).expanduser().resolve()
        logger.debug("Using CURSOR_GOAL_DATA override path=%s", path)
    else:
        path = (Path.home() / ".cursor-goal" / "data").resolve()
    path.mkdir(parents=True, exist_ok=True)
    _warn_if_world_writable(path)
    return path


def _warn_if_world_writable(path: Path) -> None:
    """Log a loud warning when the data dir is world-writable (Unix)."""
    if os.name == "nt":
        return
    try:
        mode = path.stat().st_mode
    except OSError as exc:
        logger.debug("Could not stat data dir %s: %s", path, exc)
        return
    if mode & stat.S_IWOTH:
        logger.warning(
            "Goal data directory is world-writable (%s); "
            "treat goal.json as trusted-user state and restrict permissions",
            path,
        )


def goal_path() -> Path:
    return data_dir() / GOAL_FILE_NAME


def eval_flag_path() -> Path:
    return data_dir() / EVAL_FLAG_NAME


def lock_path() -> Path:
    return data_dir() / LOCK_FILE_NAME


def now_iso() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def _chmod_private(path: Path) -> None:
    """Best-effort restrictive permissions (0600) on Unix."""
    if os.name == "nt":
        return
    try:
        os.chmod(path, 0o600)
    except OSError as exc:
        logger.debug("Could not chmod %s: %s", path, exc)


def _lock_acquire(handle: Any) -> None:
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
        msvcrt.locking(handle.fileno(), msvcrt.LK_LOCK, 1)
    else:
        import fcntl  # isort: skip  # pylint: disable=import-outside-toplevel

        fcntl.flock(handle.fileno(), fcntl.LOCK_EX)


def _lock_release(handle: Any) -> None:
    # See _lock_acquire: platform-gated inline import is intentional.
    if sys.platform == "win32":
        import msvcrt  # isort: skip  # pylint: disable=import-outside-toplevel

        try:
            handle.seek(0)
            msvcrt.locking(handle.fileno(), msvcrt.LK_UNLCK, 1)
        except OSError:
            pass
    else:
        import fcntl  # isort: skip  # pylint: disable=import-outside-toplevel

        fcntl.flock(handle.fileno(), fcntl.LOCK_UN)


@contextmanager
def goal_lock() -> Iterator[None]:
    """Exclusive cross-process lock for goal.json / eval-signal mutations."""
    path = lock_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    handle = open(path, "a+b")
    try:
        _lock_acquire(handle)
        yield
    finally:
        try:
            _lock_release(handle)
        finally:
            handle.close()


@dataclass
class GoalState:
    active: bool = True
    condition: str = ""
    validation_command: str = ""
    created_at: str = ""
    turn_budget: int = 20
    turns_used: int = 0
    status: str = "pursuing"
    last_reason: str = ""
    last_validation_output: str = ""
    last_validation_exit_code: int | None = None
    last_eval_verdict: str = ""
    schema_version: int = SCHEMA_VERSION

    def to_dict(self) -> dict[str, Any]:
        data = asdict(self)
        data["schema_version"] = SCHEMA_VERSION
        return data

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> GoalState:
        try:
            turn_budget = int(data.get("turn_budget", 20))
            turns_used = int(data.get("turns_used", 0))
            schema_version = int(data.get("schema_version", 1))
        except (TypeError, ValueError) as exc:
            raise ValueError(f"invalid goal.json numeric fields: {exc}") from exc
        if schema_version not in SUPPORTED_SCHEMA_VERSIONS:
            raise ValueError(
                f"unsupported schema_version={schema_version}; "
                f"supported={sorted(SUPPORTED_SCHEMA_VERSIONS)}"
            )
        exit_raw = data.get("last_validation_exit_code", None)
        exit_code: int | None
        if exit_raw is None or exit_raw == "":
            exit_code = None
        else:
            try:
                exit_code = int(exit_raw)
            except (TypeError, ValueError) as exc:
                raise ValueError(f"invalid last_validation_exit_code: {exc}") from exc
        return cls(
            active=bool(data.get("active", False)),
            condition=str(data.get("condition", "")),
            validation_command=str(data.get("validation_command") or ""),
            created_at=str(data.get("created_at", "")),
            turn_budget=turn_budget,
            turns_used=turns_used,
            status=str(data.get("status", "unknown")),
            last_reason=str(data.get("last_reason") or ""),
            last_validation_output=str(data.get("last_validation_output") or ""),
            last_validation_exit_code=exit_code,
            last_eval_verdict=str(data.get("last_eval_verdict") or ""),
            schema_version=schema_version,
        )

    def content_hash(self) -> str:
        """Stable hash binding eval signals to this goal identity."""
        payload = (
            f"{self.condition}\0{self.created_at}\0{self.validation_command}"
        ).encode("utf-8")
        return hashlib.sha256(payload).hexdigest()[:32]


def load_goal() -> GoalState | None:
    path = goal_path()
    if not path.is_file():
        return None
    try:
        raw = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        logger.error("Failed to read goal.json: %s", exc)
        return None
    if not isinstance(raw, dict):
        logger.error("goal.json is not an object")
        return None
    try:
        return GoalState.from_dict(raw)
    except ValueError as exc:
        logger.error("Corrupt goal.json fields: %s", exc)
        return None


def save_goal(state: GoalState) -> None:
    with goal_lock():
        _save_goal_unlocked(state)


def _save_goal_unlocked(state: GoalState) -> None:
    path = goal_path()
    tmp = path.with_name(f"goal.{os.getpid()}.{secrets.token_hex(4)}.tmp")
    payload = json.dumps(state.to_dict(), indent=2, ensure_ascii=False) + "\n"
    tmp.write_text(payload, encoding="utf-8")
    tmp.replace(path)
    _chmod_private(path)
    logger.info("Saved goal state status=%s turns=%s", state.status, state.turns_used)


def mutate_goal(mutator: Callable[[GoalState], None]) -> GoalState | None:
    """Load, mutate, and save goal state under the exclusive lock.

    The mutator may raise ``ValueError`` to abort without saving.
    """
    with goal_lock():
        state = load_goal()
        if state is None:
            return None
        mutator(state)
        _save_goal_unlocked(state)
        return state


def clear_goal_files() -> bool:
    """Remove goal.json and eval signal under lock. Returns True if goal existed."""
    with goal_lock():
        path = goal_path()
        existed = path.is_file()
        if existed:
            path.unlink()
        flag = eval_flag_path()
        if flag.exists():
            flag.unlink()
        if existed:
            logger.info("Cleared goal and evaluator signal")
        else:
            logger.info("Cleared evaluator signal (no goal file)")
        return existed


def clear_eval_signal() -> None:
    with goal_lock():
        _clear_eval_signal_unlocked()


def _clear_eval_signal_unlocked() -> None:
    flag = eval_flag_path()
    if flag.exists():
        flag.unlink()
        logger.info("Cleared evaluator signal")


def mark_goal_achieved(*, require_signal: bool = True) -> tuple[GoalState | None, str]:
    """Mark goal achieved under lock.

    Returns ``(state, status)`` where status is ``ok``, ``missing``,
    ``rejected``, or ``forced``.
    """
    with goal_lock():
        state = load_goal()
        if state is None:
            return None, "missing"
        signaled = _has_eval_signal_unlocked(state)
        if not signaled:
            if require_signal:
                return state, "rejected"
            status = "forced"
        else:
            _clear_eval_signal_unlocked()
            status = "ok"
        state.status = "achieved"
        state.active = False
        _save_goal_unlocked(state)
        return state, status


def set_eval_signal(*, verdict: str = "YES", reason: str = "") -> None:
    """Record a YES-bound evaluator signal for the active goal."""
    with goal_lock():
        state = load_goal()
        if state is None:
            logger.warning("set_eval_signal with no active goal")
            return
        if verdict.upper() != "YES":
            logger.warning("Refusing eval signal with non-YES verdict=%s", verdict)
            return
        data_dir()
        flag = eval_flag_path()
        payload = {
            "condition_hash": state.content_hash(),
            "created_at": now_iso(),
            "verdict": "YES",
            "reason": reason,
        }
        flag.write_text(
            json.dumps(payload, indent=2, ensure_ascii=False) + "\n",
            encoding="utf-8",
        )
        _chmod_private(flag)
        state.last_eval_verdict = "YES"
        _save_goal_unlocked(state)
        logger.info("Recorded evaluator signal hash=%s", payload["condition_hash"])


def has_eval_signal() -> bool:
    state = load_goal()
    if state is None:
        return False
    return _has_eval_signal_unlocked(state)


def _has_eval_signal_unlocked(state: GoalState) -> bool:
    flag = eval_flag_path()
    if not flag.is_file():
        return False
    try:
        raw = json.loads(flag.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        # Legacy empty touch-file or corrupt signal — not valid for bound check.
        try:
            if flag.stat().st_size == 0:
                logger.warning("Legacy empty eval signal ignored; re-run eval signal")
        except OSError:
            pass
        return False
    if not isinstance(raw, dict):
        return False
    expected = state.content_hash()
    actual = str(raw.get("condition_hash", ""))
    if actual != expected:
        logger.warning(
            "Eval signal hash mismatch (stale/cross-goal); expected=%s got=%s",
            expected,
            actual,
        )
        return False
    verdict = str(raw.get("verdict", "")).upper()
    if verdict != "YES":
        logger.warning("Eval signal missing YES verdict (got %r)", verdict)
        return False
    return True


def update_goal_fields(**fields: Any) -> GoalState | None:
    with goal_lock():
        state = load_goal()
        if state is None:
            return None
        for key, value in fields.items():
            if hasattr(state, key):
                setattr(state, key, value)
        _save_goal_unlocked(state)
        return state
