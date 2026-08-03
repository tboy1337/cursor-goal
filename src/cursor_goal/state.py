"""Goal state file paths and atomic JSON I/O."""

# Path-trust helpers (symlink/reparse) live here with GoalState to keep a single
# trust boundary; module size is expected for the hardening surface.
# pylint: disable=too-many-lines

from __future__ import annotations

import ctypes
import hashlib
import json
import os
import secrets
import stat
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
from cursor_goal.win_acl import ACL_HARDEN_FAILURES as _ACL_HARDEN_FAILURES
from cursor_goal.win_acl import HARDENED_PATHS as _HARDENED_PATHS
from cursor_goal.win_acl import failure_reason as _acl_failure_reason
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


def configured_data_dir_path() -> Path:
    """Return the configured data dir path without resolving symlinks."""
    override = os.environ.get("CURSOR_GOAL_DATA")
    if override:
        return Path(override).expanduser()
    return Path.home() / ".cursor-goal" / "data"


def _absolute_without_resolve(path: Path) -> Path:
    """Make *path* absolute without following symlinks."""
    expanded = path.expanduser()
    if expanded.is_absolute():
        return expanded
    return Path.cwd() / expanded


_FILE_ATTRIBUTE_REPARSE_POINT = 0x400
_INVALID_FILE_ATTRIBUTES = 0xFFFFFFFF


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
        logger.debug("Could not lstat data dir %s: %s", target, exc)
        return False
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
        "Verify only you can access the data directory, or set "
        "CURSOR_GOAL_SKIP_ACL=1 after manually locking down the path."
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


def _chmod_dir_private(path: Path) -> None:
    """Best-effort restrictive directory permissions (0700) on Unix."""
    if os.name == "nt":
        return
    try:
        os.chmod(path, 0o700)
    except OSError as exc:
        logger.debug("Could not chmod data dir %s: %s", path, exc)


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
