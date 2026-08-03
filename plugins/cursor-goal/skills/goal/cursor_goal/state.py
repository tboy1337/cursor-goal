"""Goal state schema, locks, and atomic JSON I/O.

Path-trust / data-dir security helpers (symlink/reparse detection, ACL
hardening, workdir jail) live in :mod:`cursor_goal.path_trust` and are
re-exported here so existing ``from cursor_goal.state import ...`` call
sites keep working unchanged.
"""

from __future__ import annotations

import hashlib
import json
import os
import secrets
from collections.abc import Callable
from contextlib import contextmanager
from dataclasses import asdict, dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterator

from cursor_goal.fs_lock import GoalLockTimeoutError
from cursor_goal.fs_lock import lock_acquire as _fs_lock_acquire
from cursor_goal.fs_lock import lock_release as _fs_lock_release
from cursor_goal.logging_config import get_logger
from cursor_goal.path_trust import _absolute_without_resolve
from cursor_goal.path_trust import _chmod_dir_private
from cursor_goal.path_trust import _warn_if_world_writable
from cursor_goal.path_trust import _windows_path_is_reparse_point
from cursor_goal.path_trust import (
    acl_harden_failure_message,
    allow_any_workdir,
    assert_workdir_usable,
    configured_data_dir_path,
    data_dir,
    data_dir_is_insecure,
    normalize_workdir,
    path_has_symlink_or_reparse,
    refuse_if_acl_harden_failed,
    refuse_if_data_dir_insecure,
)
from cursor_goal.win_acl import ACL_HARDEN_FAILURES as _ACL_HARDEN_FAILURES
from cursor_goal.win_acl import HARDENED_PATHS as _HARDENED_PATHS
from cursor_goal.win_acl import harden_windows_acl as _harden_windows_acl

logger = get_logger("cursor_goal.state")

# Explicit re-exports for importers / type checkers.
__all__ = (
    "CorruptGoalError",
    "GoalLockTimeoutError",
    "GoalState",
    "LOCK_TIMEOUT_SEC",
    "MAX_FIELD_CHARS",
    "MAX_TURN_BUDGET",
    "SCHEMA_VERSION",
    "_ACL_HARDEN_FAILURES",
    "_HARDENED_PATHS",
    "_harden_windows_acl",
    "acl_harden_failure_message",
    "allow_any_workdir",
    "assert_workdir_usable",
    "configured_data_dir_path",
    "data_dir",
    "data_dir_is_insecure",
    "normalize_workdir",
    "path_has_symlink_or_reparse",
    "refuse_if_acl_harden_failed",
    "refuse_if_data_dir_insecure",
)

EVAL_FLAG_NAME = "goal-eval-done"
GOAL_FILE_NAME = "goal.json"
LOCK_FILE_NAME = "goal.lock"
SCHEMA_VERSION = 1
SUPPORTED_SCHEMA_VERSIONS = frozenset({1})
MAX_TURN_BUDGET = 500
MAX_FIELD_CHARS = 4000
LOCK_TIMEOUT_SEC = 10.0
WAKE_BUDGET_MULTIPLIER = 10
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
        "wake_ticks",
        "wake_budget",
        "shell_ok",
        "workdir",
        "status",
        "last_reason",
        "last_validation_output",
        "last_validation_exit_code",
        "last_eval_verdict",
    }
)
LAST_STOP_RESPONSE_NAME = "last-stop-response.json"


class CorruptGoalError(ValueError):
    """Raised when goal.json exists but cannot be loaded as a valid GoalState."""


def atomic_write_text(path: Path, text: str) -> None:
    """Write *text* via temp file + replace; prefer private mode bits."""
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_name(f"{path.name}.{os.getpid()}.{secrets.token_hex(4)}.tmp")
    try:
        if os.name != "nt":
            flags = os.O_WRONLY | os.O_CREAT | os.O_TRUNC
            fd = os.open(tmp, flags, 0o600)
            with os.fdopen(fd, "w", encoding="utf-8") as handle:
                handle.write(text)
        else:
            tmp.write_text(text, encoding="utf-8")
        tmp.replace(path)
    except Exception:
        if tmp.exists():
            try:
                tmp.unlink()
            except OSError:
                pass
        raise
    _chmod_private(path)


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


def default_wake_budget(turn_budget: int) -> int:
    """Default wake_budget = clamp(turn_budget * 10, 10, MAX_TURN_BUDGET)."""
    raw = int(turn_budget) * WAKE_BUDGET_MULTIPLIER
    return max(10, min(MAX_TURN_BUDGET, raw))


def clamp_wake_budget(value: int) -> int:
    """Clamp wake budget to [1, MAX_TURN_BUDGET]."""
    if value < 1:
        raise ValueError(f"Wake budget must be a positive integer, got {value}")
    if value > MAX_TURN_BUDGET:
        logger.warning(
            "wake_budget %s exceeds max %s; clamping", value, MAX_TURN_BUDGET
        )
        return MAX_TURN_BUDGET
    return value


def budgets_exhausted(
    turns_used: int,
    turn_budget: int,
    wake_ticks: int,
    wake_budget: int,
) -> bool:
    """True when turn or wake budget is exhausted (independent counters)."""
    return int(turns_used) >= int(turn_budget) or int(wake_ticks) >= int(wake_budget)


def _parse_shell_ok(value: Any) -> bool:
    """Parse shell_ok from JSON / CLI-ish values."""
    if isinstance(value, bool):
        return value
    if isinstance(value, str):
        lowered = value.strip().lower()
        if lowered in {"1", "true", "yes", "on"}:
            return True
        if lowered in {"0", "false", "no", "off"}:
            return False
    if isinstance(value, (int, float)) and value in (0, 1):
        return bool(value)
    raise ValueError(f"shell_ok must be a boolean, got {value!r}")


def _chmod_private(path: Path) -> None:
    """Best-effort restrictive permissions (0600) on Unix."""
    if os.name == "nt":
        return
    try:
        os.chmod(path, 0o600)
    except OSError as exc:
        logger.debug("Could not chmod %s: %s", path, exc)


def _lock_acquire(handle: Any) -> None:
    _fs_lock_acquire(handle, LOCK_TIMEOUT_SEC)


def _lock_release(handle: Any) -> None:
    _fs_lock_release(handle)


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


def _require_field_chars(name: str, value: str) -> str:
    if len(value) > MAX_FIELD_CHARS:
        raise ValueError(
            f"{name} exceeds {MAX_FIELD_CHARS} character limit ({len(value)} chars)"
        )
    return value


def _clamp_field_chars(name: str, value: str) -> str:
    """Truncate oversized fields on load (corrupt/malicious goal.json recovery)."""
    if len(value) <= MAX_FIELD_CHARS:
        return value
    logger.warning(
        "%s exceeds %s chars on load (%s); truncating",
        name,
        MAX_FIELD_CHARS,
        len(value),
    )
    return value[:MAX_FIELD_CHARS]


def _set_condition(state: GoalState, value: Any) -> None:
    state.condition = _require_field_chars("condition", str(value))


def _set_validation_command(state: GoalState, value: Any) -> None:
    state.validation_command = _require_field_chars(
        "validation_command", str(value or "")
    )


def _set_created_at(state: GoalState, value: Any) -> None:
    state.created_at = _require_field_chars("created_at", str(value))


def _set_turn_budget(state: GoalState, value: Any) -> None:
    state.turn_budget = clamp_turn_budget(int(value))


def _set_turns_used(state: GoalState, value: Any) -> None:
    turns = int(value)
    if turns < 0:
        raise ValueError(f"turns_used must be >= 0, got {turns}")
    state.turns_used = turns


def _set_wake_ticks(state: GoalState, value: Any) -> None:
    ticks = int(value)
    if ticks < 0:
        raise ValueError(f"wake_ticks must be >= 0, got {ticks}")
    state.wake_ticks = ticks


def _set_wake_budget(state: GoalState, value: Any) -> None:
    state.wake_budget = clamp_wake_budget(int(value))


def _set_shell_ok(state: GoalState, value: Any) -> None:
    state.shell_ok = _parse_shell_ok(value)


def _parse_workdir(value: Any) -> str:
    """Parse optional workdir; empty string means unset."""
    if value is None:
        return ""
    text = str(value).strip()
    if not text:
        return ""
    return _require_field_chars("workdir", text)


def _set_workdir(state: GoalState, value: Any) -> None:
    state.workdir = _parse_workdir(value)


def _set_status(state: GoalState, value: Any) -> None:
    state.status = _parse_status(value)


def _set_last_reason(state: GoalState, value: Any) -> None:
    state.last_reason = _require_field_chars("last_reason", str(value or ""))


def _set_last_validation_output(state: GoalState, value: Any) -> None:
    state.last_validation_output = _require_field_chars(
        "last_validation_output", str(value or "")
    )


def _set_last_validation_exit_code(state: GoalState, value: Any) -> None:
    if value is None or value == "":
        state.last_validation_exit_code = None
    else:
        state.last_validation_exit_code = int(value)


def _set_last_eval_verdict(state: GoalState, value: Any) -> None:
    state.last_eval_verdict = _require_field_chars(
        "last_eval_verdict", str(value or "")
    )


_FIELD_SETTERS: dict[str, Callable[[GoalState, Any], None]] = {
    "active": _set_active,
    "condition": _set_condition,
    "validation_command": _set_validation_command,
    "created_at": _set_created_at,
    "turn_budget": _set_turn_budget,
    "turns_used": _set_turns_used,
    "wake_ticks": _set_wake_ticks,
    "wake_budget": _set_wake_budget,
    "shell_ok": _set_shell_ok,
    "workdir": _set_workdir,
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
class GoalState:  # pylint: disable=too-many-instance-attributes
    active: bool = True
    condition: str = ""
    validation_command: str = ""
    created_at: str = ""
    turn_budget: int = 20
    turns_used: int = 0
    wake_ticks: int = 0
    wake_budget: int = 200
    shell_ok: bool = False
    workdir: str = ""
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
    def from_dict(  # pylint: disable=too-many-branches,too-many-statements
        cls, data: dict[str, Any]
    ) -> GoalState:
        try:
            turn_budget = clamp_turn_budget(int(data.get("turn_budget", 20)))
            turns_used = int(data.get("turns_used", 0))
            wake_ticks = int(data.get("wake_ticks", 0))
            schema_version = int(data.get("schema_version", SCHEMA_VERSION))
        except (TypeError, ValueError) as exc:
            raise ValueError(f"invalid goal.json numeric fields: {exc}") from exc
        if turns_used < 0:
            raise ValueError(f"turns_used must be >= 0, got {turns_used}")
        if wake_ticks < 0:
            raise ValueError(f"wake_ticks must be >= 0, got {wake_ticks}")
        if schema_version not in SUPPORTED_SCHEMA_VERSIONS:
            raise ValueError(
                f"unsupported schema_version={schema_version}; "
                f"supported={sorted(SUPPORTED_SCHEMA_VERSIONS)} "
                "(clear ~/.cursor-goal/data/goal.json or recreate the goal)"
            )

        wake_budget_raw = data.get("wake_budget")
        if wake_budget_raw is None or wake_budget_raw == "":
            wake_budget = default_wake_budget(turn_budget)
        else:
            try:
                wake_budget = clamp_wake_budget(int(wake_budget_raw))
            except (TypeError, ValueError) as exc:
                raise ValueError(f"invalid wake_budget: {exc}") from exc

        if "shell_ok" not in data:
            shell_ok = False
        else:
            try:
                shell_ok = _parse_shell_ok(data.get("shell_ok"))
            except ValueError as exc:
                raise ValueError(f"invalid shell_ok: {exc}") from exc

        workdir_raw = data.get("workdir", "")
        try:
            workdir = _clamp_field_chars("workdir", str(workdir_raw or "").strip())
        except (TypeError, ValueError) as exc:
            raise ValueError(f"invalid workdir: {exc}") from exc

        # Clamp counters that exceed their budgets (corrupt / race).
        if turns_used > turn_budget:
            logger.warning(
                "turns_used %s > turn_budget %s; clamping",
                turns_used,
                turn_budget,
            )
            turns_used = turn_budget
        if wake_ticks > wake_budget:
            logger.warning(
                "wake_ticks %s > wake_budget %s; clamping",
                wake_ticks,
                wake_budget,
            )
            wake_ticks = wake_budget

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
        # Clamp oversized strings on load so stop fail-open is not tripped by
        # length alone; updates still reject via _require_field_chars setters.
        condition = _clamp_field_chars("condition", str(data.get("condition", "")))
        validation_command = _clamp_field_chars(
            "validation_command", str(data.get("validation_command") or "")
        )
        if (
            status == "pursuing"
            and active
            and budgets_exhausted(turns_used, turn_budget, wake_ticks, wake_budget)
        ):
            status = "budget-limited"
            active = False
        return cls(
            active=active,
            condition=condition,
            validation_command=validation_command,
            created_at=_clamp_field_chars(
                "created_at", str(data.get("created_at", ""))
            ),
            turn_budget=turn_budget,
            turns_used=turns_used,
            wake_ticks=wake_ticks,
            wake_budget=wake_budget,
            shell_ok=shell_ok,
            workdir=workdir,
            status=status,
            last_reason=_clamp_field_chars(
                "last_reason", str(data.get("last_reason") or "")
            ),
            last_validation_output=_clamp_field_chars(
                "last_validation_output",
                str(data.get("last_validation_output") or ""),
            ),
            last_validation_exit_code=exit_code,
            last_eval_verdict=_clamp_field_chars(
                "last_eval_verdict", str(data.get("last_eval_verdict") or "")
            ),
            schema_version=SCHEMA_VERSION,
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
    payload = json.dumps(state.to_dict(), indent=2, ensure_ascii=False) + "\n"
    atomic_write_text(path, payload)
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


def clear_last_stop_response() -> None:
    """Best-effort remove last-stop-response.json diagnostic file."""
    path = data_dir(check_writable=False) / LAST_STOP_RESPONSE_NAME
    try:
        path.unlink(missing_ok=True)
    except OSError as exc:
        logger.debug("Could not remove %s: %s", LAST_STOP_RESPONSE_NAME, exc)


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
        clear_last_stop_response()
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
    atomic_write_text(
        flag,
        json.dumps(payload, indent=2, ensure_ascii=False) + "\n",
    )
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
        state.last_reason = _clamp_field_chars("last_reason", str(reason or ""))
        state.last_eval_verdict = _clamp_field_chars(
            "last_eval_verdict", str(verdict or "")
        )
        if verdict == "YES":
            _write_eval_signal_unlocked(state, reason=reason)
        else:
            _clear_eval_signal_unlocked()
        _save_goal_unlocked(state)
        return state
