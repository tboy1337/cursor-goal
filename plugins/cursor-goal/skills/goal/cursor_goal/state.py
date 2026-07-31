"""Goal state file paths and atomic JSON I/O."""

from __future__ import annotations

import hashlib
import json
import os
import secrets
import shutil
import stat
import subprocess  # nosec B404 — Windows ACL via icacls only
import sys
import time
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
MAX_TURN_BUDGET = 500
MAX_FIELD_CHARS = 4000
LOCK_TIMEOUT_SEC = 10.0
_HARDENED_PATHS: set[str] = set()
ALLOWED_STATUSES = frozenset(
    {
        "pursuing",
        "paused",
        "achieved",
        "budget-limited",
        "unknown",
    }
)
_UPDATABLE_FIELDS = frozenset(
    {
        "active",
        "condition",
        "validation_command",
        "created_at",
        "turn_budget",
        "turns_used",
        "status",
        "last_reason",
        "last_validation_output",
        "last_validation_exit_code",
        "last_eval_verdict",
    }
)


class CorruptGoalError(ValueError):
    """Raised when goal.json exists but cannot be loaded as a valid GoalState."""


class GoalLockTimeoutError(OSError):
    """Raised when the exclusive goal.lock cannot be acquired in time."""


def data_dir(*, check_writable: bool = True) -> Path:
    """Resolve the goal data directory (CURSOR_GOAL_DATA or ~/.cursor-goal/data)."""
    override = os.environ.get("CURSOR_GOAL_DATA")
    if override:
        path = Path(override).expanduser().resolve()
        logger.debug("Using CURSOR_GOAL_DATA override path=%s", path)
    else:
        path = (Path.home() / ".cursor-goal" / "data").resolve()
    path.mkdir(parents=True, exist_ok=True, mode=0o700)
    _chmod_dir_private(path)
    _harden_windows_acl(path)
    if check_writable:
        _warn_if_world_writable(path)
    return path


def data_dir_is_insecure(path: Path | None = None) -> bool:
    """Return True when the data dir is group/world-writable (Unix only)."""
    if os.name == "nt":
        return False
    target = path if path is not None else data_dir(check_writable=False)
    try:
        mode = target.stat().st_mode
    except OSError as exc:
        logger.debug("Could not stat data dir %s: %s", target, exc)
        return False
    return bool(mode & (stat.S_IWOTH | stat.S_IWGRP))


def refuse_if_data_dir_insecure() -> str | None:
    """Return an error message if the data dir is insecure, else None."""
    if not data_dir_is_insecure():
        return None
    path = data_dir(check_writable=False)
    return (
        f"[goal] Error: data directory is group/world-writable ({path}). "
        "Restrict permissions (e.g. chmod 700) or set CURSOR_GOAL_DATA to a "
        "private directory before create/validate."
    )


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


def goal_path() -> Path:
    return data_dir() / GOAL_FILE_NAME


def eval_flag_path() -> Path:
    return data_dir() / EVAL_FLAG_NAME


def lock_path() -> Path:
    return data_dir() / LOCK_FILE_NAME


def now_iso() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def clamp_turn_budget(value: int) -> int:
    """Clamp turn budget to [1, MAX_TURN_BUDGET]."""
    if value < 1:
        raise ValueError(f"Budget must be a positive integer, got {value}")
    if value > MAX_TURN_BUDGET:
        logger.warning(
            "turn_budget %s exceeds max %s; clamping", value, MAX_TURN_BUDGET
        )
        return MAX_TURN_BUDGET
    return value


def _chmod_private(path: Path) -> None:
    """Best-effort restrictive permissions (0600) on Unix."""
    if os.name == "nt":
        return
    try:
        os.chmod(path, 0o600)
    except OSError as exc:
        logger.debug("Could not chmod %s: %s", path, exc)


def _chmod_dir_private(path: Path) -> None:
    """Best-effort restrictive directory permissions (0700) on Unix."""
    if os.name == "nt":
        return
    try:
        os.chmod(path, 0o700)
    except OSError as exc:
        logger.debug("Could not chmod data dir %s: %s", path, exc)


def _windows_username() -> str | None:
    """Best-effort current Windows username for icacls grants."""
    for key in ("USERNAME", "USER"):
        value = os.environ.get(key, "").strip()
        if value:
            return value
    try:
        return os.getlogin()
    except OSError:
        return None


def _acl_harden_disabled() -> bool:
    """Return True when ACL harden is skipped (tests / explicit opt-out)."""
    raw = os.environ.get("CURSOR_GOAL_SKIP_ACL", "").strip().lower()
    return raw in {"1", "true", "yes", "on"}


def _harden_windows_acl(path: Path) -> None:
    """Best-effort grant current user full control via icacls (no hard deps).

    Does not strip inheritance first (that can leave paths inaccessible if the
    subsequent grant fails). Skip with ``CURSOR_GOAL_SKIP_ACL=1``.
    """
    if os.name != "nt" or _acl_harden_disabled():
        return
    key = str(path)
    if key in _HARDENED_PATHS:
        return
    user = _windows_username()
    if not user:
        logger.warning(
            "Could not determine Windows username for ACL harden on %s", path
        )
        return
    icacls = shutil.which("icacls")
    if not icacls:
        logger.warning("icacls not found on PATH; skip Windows ACL harden for %s", path)
        return
    grant = f"{user}:(OI)(CI)F" if path.is_dir() else f"{user}:F"
    try:
        completed = subprocess.run(  # nosec B603
            [icacls, str(path), "/grant:r", grant],
            capture_output=True,
            text=True,
            timeout=15,
            check=False,
        )
    except (OSError, subprocess.TimeoutExpired) as exc:
        logger.warning("Windows ACL harden failed for %s: %s", path, exc)
        return
    if completed.returncode != 0:
        err = (completed.stderr or completed.stdout or "").strip()
        logger.warning(
            "Windows ACL harden icacls exit=%s for %s: %s",
            completed.returncode,
            path,
            err[:200] or "(no output)",
        )
        return
    _HARDENED_PATHS.add(key)
    logger.debug("Windows ACL hardened path=%s user=%s", path, user)


def _lock_timeout_message() -> str:
    return (
        "Could not acquire goal.lock within ~10s; another process may "
        "be holding it. Retry, or check for a stuck cursor-goal process."
    )


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
        try:
            msvcrt.locking(handle.fileno(), msvcrt.LK_LOCK, 1)
        except OSError as exc:
            raise GoalLockTimeoutError(_lock_timeout_message()) from exc
    else:
        import fcntl  # isort: skip  # pylint: disable=import-outside-toplevel

        deadline = time.monotonic() + LOCK_TIMEOUT_SEC
        while True:
            try:
                fcntl.flock(handle.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
                return
            except BlockingIOError as exc:
                if time.monotonic() >= deadline:
                    raise GoalLockTimeoutError(_lock_timeout_message()) from exc
                time.sleep(0.05)


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
    path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
    _chmod_dir_private(path.parent)
    handle = open(path, "a+b")
    try:
        _lock_acquire(handle)
        yield
    finally:
        try:
            _lock_release(handle)
        finally:
            handle.close()


def snapshot_goal(*, raise_corrupt: bool = False) -> GoalState | None:
    """Load goal.json under the exclusive lock (consistent observer snapshot)."""
    with goal_lock():
        return load_goal(raise_corrupt=raise_corrupt)


def _parse_active(value: Any) -> bool:
    if isinstance(value, bool):
        return value
    raise ValueError(
        f"active must be a JSON boolean, got {type(value).__name__}: {value!r}"
    )


def _parse_status(value: Any) -> str:
    status = str(value if value is not None else "unknown")
    if status not in ALLOWED_STATUSES:
        raise ValueError(
            f"invalid status={status!r}; allowed={sorted(ALLOWED_STATUSES)}"
        )
    return status


def _set_active(state: GoalState, value: Any) -> None:
    state.active = _parse_active(value)


def _set_condition(state: GoalState, value: Any) -> None:
    state.condition = str(value)


def _set_validation_command(state: GoalState, value: Any) -> None:
    state.validation_command = str(value or "")


def _set_created_at(state: GoalState, value: Any) -> None:
    state.created_at = str(value)


def _set_turn_budget(state: GoalState, value: Any) -> None:
    state.turn_budget = clamp_turn_budget(int(value))


def _set_turns_used(state: GoalState, value: Any) -> None:
    turns = int(value)
    if turns < 0:
        raise ValueError(f"turns_used must be >= 0, got {turns}")
    state.turns_used = turns


def _set_status(state: GoalState, value: Any) -> None:
    state.status = _parse_status(value)


def _set_last_reason(state: GoalState, value: Any) -> None:
    state.last_reason = str(value or "")


def _set_last_validation_output(state: GoalState, value: Any) -> None:
    state.last_validation_output = str(value or "")


def _set_last_validation_exit_code(state: GoalState, value: Any) -> None:
    if value is None or value == "":
        state.last_validation_exit_code = None
    else:
        state.last_validation_exit_code = int(value)


def _set_last_eval_verdict(state: GoalState, value: Any) -> None:
    state.last_eval_verdict = str(value or "")


_FIELD_SETTERS: dict[str, Callable[[GoalState, Any], None]] = {
    "active": _set_active,
    "condition": _set_condition,
    "validation_command": _set_validation_command,
    "created_at": _set_created_at,
    "turn_budget": _set_turn_budget,
    "turns_used": _set_turns_used,
    "status": _set_status,
    "last_reason": _set_last_reason,
    "last_validation_output": _set_last_validation_output,
    "last_validation_exit_code": _set_last_validation_exit_code,
    "last_eval_verdict": _set_last_eval_verdict,
}


def _apply_field(state: GoalState, key: str, value: Any) -> None:
    """Validate and assign a single updatable field."""
    setter = _FIELD_SETTERS.get(key)
    if setter is None:
        raise ValueError(f"unknown goal field: {key}")
    setter(state, value)


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
            turn_budget = clamp_turn_budget(int(data.get("turn_budget", 20)))
            turns_used = int(data.get("turns_used", 0))
            schema_version = int(data.get("schema_version", 1))
        except (TypeError, ValueError) as exc:
            raise ValueError(f"invalid goal.json numeric fields: {exc}") from exc
        if turns_used < 0:
            raise ValueError(f"turns_used must be >= 0, got {turns_used}")
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
        active_raw = data.get("active", False)
        try:
            active = _parse_active(active_raw)
        except ValueError as exc:
            raise ValueError(str(exc)) from exc
        status = _parse_status(data.get("status", "unknown"))
        return cls(
            active=active,
            condition=str(data.get("condition", "")),
            validation_command=str(data.get("validation_command") or ""),
            created_at=str(data.get("created_at", "")),
            turn_budget=turn_budget,
            turns_used=turns_used,
            status=status,
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


def _quarantine_corrupt_goal(reason: str) -> Path | None:
    """Rename corrupt goal.json aside for recovery/support. Returns quarantine path."""
    path = goal_path()
    if not path.is_file():
        return None
    stamp = now_iso().replace(":", "").replace("-", "")
    dest = path.with_name(f"{GOAL_FILE_NAME}.corrupt.{stamp}")
    # Avoid clobbering an existing quarantine file from the same second.
    if dest.exists():
        dest = path.with_name(
            f"{GOAL_FILE_NAME}.corrupt.{stamp}.{secrets.token_hex(3)}"
        )
    try:
        path.replace(dest)
    except OSError as exc:
        logger.error("Failed to quarantine corrupt goal.json: %s", exc)
        return None
    logger.error("Quarantined corrupt goal.json to %s (%s)", dest, reason)
    return dest


def load_goal(*, raise_corrupt: bool = False) -> GoalState | None:
    """Load goal.json.

    Returns None when the file is missing. When *raise_corrupt* is True,
    corrupt/unsupported content raises :class:`CorruptGoalError`; otherwise
    logs and returns None (legacy callers / stop-hook fail-open). Corrupt
    files are quarantined to ``goal.json.corrupt.<UTC>`` before returning.
    """
    path = goal_path()
    if not path.is_file():
        return None
    try:
        raw = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        logger.error("Failed to read goal.json: %s", exc)
        quarantine = _quarantine_corrupt_goal(f"unreadable: {exc}")
        detail = f"goal.json unreadable: {exc}"
        if quarantine is not None:
            detail = f"{detail} (quarantined to {quarantine.name})"
        if raise_corrupt:
            raise CorruptGoalError(detail) from exc
        return None
    if not isinstance(raw, dict):
        logger.error("goal.json is not an object")
        quarantine = _quarantine_corrupt_goal("not an object")
        detail = "goal.json is not an object"
        if quarantine is not None:
            detail = f"{detail} (quarantined to {quarantine.name})"
        if raise_corrupt:
            raise CorruptGoalError(detail)
        return None
    try:
        return GoalState.from_dict(raw)
    except ValueError as exc:
        logger.error("Corrupt goal.json fields: %s", exc)
        quarantine = _quarantine_corrupt_goal(f"corrupt fields: {exc}")
        detail = f"goal.json corrupt: {exc}"
        if quarantine is not None:
            detail = f"{detail} (quarantined to {quarantine.name})"
        if raise_corrupt:
            raise CorruptGoalError(detail) from exc
        return None


def save_goal(state: GoalState) -> None:
    with goal_lock():
        _save_goal_unlocked(state)


def _save_goal_unlocked(state: GoalState) -> None:
    path = goal_path()
    tmp = path.with_name(f"goal.{os.getpid()}.{secrets.token_hex(4)}.tmp")
    payload = json.dumps(state.to_dict(), indent=2, ensure_ascii=False) + "\n"
    try:
        tmp.write_text(payload, encoding="utf-8")
        tmp.replace(path)
    except Exception:
        if tmp.exists():
            try:
                tmp.unlink()
            except OSError:
                pass
        raise
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


def _write_eval_signal_unlocked(state: GoalState, *, reason: str) -> None:
    """Atomically write YES-bound eval signal for *state* (caller holds lock)."""
    data_dir()
    flag = eval_flag_path()
    payload = {
        "condition_hash": state.content_hash(),
        "created_at": now_iso(),
        "verdict": "YES",
        "reason": reason,
    }
    tmp = flag.with_name(f"{EVAL_FLAG_NAME}.{os.getpid()}.{secrets.token_hex(4)}.tmp")
    try:
        tmp.write_text(
            json.dumps(payload, indent=2, ensure_ascii=False) + "\n",
            encoding="utf-8",
        )
        tmp.replace(flag)
    except Exception:
        if tmp.exists():
            try:
                tmp.unlink()
            except OSError:
                pass
        raise
    _chmod_private(flag)
    logger.info("Recorded evaluator signal hash=%s", payload["condition_hash"])


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
        state.last_eval_verdict = "YES"
        _write_eval_signal_unlocked(state, reason=reason)
        _save_goal_unlocked(state)


def has_eval_signal() -> bool:
    """Return True when a YES-bound eval signal matches the current goal."""
    with goal_lock():
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
            if key not in _UPDATABLE_FIELDS:
                logger.debug("Ignoring unknown goal field update: %s", key)
                continue
            _apply_field(state, key, value)
        _save_goal_unlocked(state)
        return state


def create_goal_atomic(
    state: GoalState,
    *,
    force: bool = False,
) -> tuple[GoalState | None, str]:
    """Create (or overwrite) a goal and clear eval signal under one lock.

    Returns ``(state, status)`` where status is ``ok`` or ``exists``.
    """
    with goal_lock():
        existing = load_goal()
        if (
            existing is not None
            and existing.active
            and existing.status == "pursuing"
            and not force
        ):
            return existing, "exists"
        _clear_eval_signal_unlocked()
        _save_goal_unlocked(state)
        return state, "ok"


def record_parse_result(verdict: str, reason: str) -> GoalState | None:
    """Persist eval verdict/reason and YES signal under one lock.

    Returns the updated goal, or None if no goal was present.
    """
    with goal_lock():
        state = load_goal()
        if state is None:
            return None
        state.last_reason = reason
        state.last_eval_verdict = verdict
        if verdict == "YES":
            _write_eval_signal_unlocked(state, reason=reason)
        else:
            _clear_eval_signal_unlocked()
        _save_goal_unlocked(state)
        return state
