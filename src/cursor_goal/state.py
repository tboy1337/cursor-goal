"""Goal state schema, locks, and atomic JSON I/O."""

from __future__ import annotations

import hashlib
import json
import os
import secrets
import shutil
import subprocess  # nosec B404
import threading
from collections.abc import Callable
from contextlib import contextmanager
from pathlib import Path
from typing import Any, Iterator

from cursor_goal.fs_lock import GoalLockTimeoutError
from cursor_goal.fs_lock import lock_acquire as _fs_lock_acquire
from cursor_goal.fs_lock import lock_release as _fs_lock_release
from cursor_goal.goal_schema import (  # noqa: F401  pylint: disable=unused-import
    _UPDATABLE_FIELDS,
    ALLOWED_STATUSES,
    MAX_FIELD_CHARS,
    MAX_TURN_BUDGET,
    SCHEMA_VERSION,
    SUPPORTED_SCHEMA_VERSIONS,
    WAKE_BUDGET_MULTIPLIER,
    GoalState,
    _apply_field,
    _clamp_field_chars,
    _require_field_chars,
    budgets_exhausted,
    clamp_turn_budget,
    clamp_wake_budget,
    default_wake_budget,
    now_iso,
)
from cursor_goal.logging_config import get_logger
from cursor_goal.path_trust import (
    _chmod_dir_private,
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
from cursor_goal.state_mutations import (
    create_goal_atomic,
    record_parse_audit,
    record_parse_result,
    update_goal_fields,
)
from cursor_goal.validation import is_broad_condition
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
    "NATIVE_CONTINUATION_ENV",
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
    "native_continuation_env_disabled",
    "path_has_symlink_or_reparse",
    "refuse_if_acl_harden_failed",
    "refuse_if_data_dir_insecure",
    "resolve_native_continuation_flag",
    "create_goal_atomic",
    "record_parse_audit",
    "record_parse_result",
    "update_goal_fields",
    "now_iso",
    "clamp_turn_budget",
    "clamp_wake_budget",
    "default_wake_budget",
    "budgets_exhausted",
)

EVAL_FLAG_NAME = "goal-eval-done"
AUDIT_FLAG_NAME = "goal-audit-clear"
AUDIT_CONFIRM_FLAG_NAME = "goal-audit-confirm"
NOGIT_TREE_FINGERPRINT = "nogit"
TREE_FINGERPRINT_TIMEOUT_SEC = 15.0
GOAL_FILE_NAME = "goal.json"
LOCK_FILE_NAME = "goal.lock"
LOCK_TIMEOUT_SEC = 10.0
BLOCK_STREAK_REQUIRED = 3
NATIVE_CONTINUATION_ENV = "CURSOR_GOAL_NATIVE"
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
        logger.exception("atomic_write_text failed for %s; removing temp %s", path, tmp)
        if tmp.exists():
            try:
                tmp.unlink()
            except OSError as unlink_exc:
                logger.debug("Could not remove temp %s: %s", tmp, unlink_exc)
        raise
    _chmod_private(path)
    if os.name == "nt":
        # Belt-and-suspenders: new files may not inherit a stripped DACL.
        _harden_windows_acl(path)


def goal_path() -> Path:
    return data_dir() / GOAL_FILE_NAME


def eval_flag_path() -> Path:
    return data_dir() / EVAL_FLAG_NAME


def audit_flag_path() -> Path:
    return data_dir() / AUDIT_FLAG_NAME


def audit_confirm_flag_path() -> Path:
    return data_dir() / AUDIT_CONFIRM_FLAG_NAME


def _audit_response_hash(response_text: str) -> str:
    """SHA-256 of the full auditor response used to bind confirm-pass."""
    digest = hashlib.sha256((response_text or "").encode("utf-8")).hexdigest()
    logger.debug(
        "audit response_hash=%s chars=%s", digest[:16], len(response_text or "")
    )
    return digest


def compute_tree_fingerprint(*, cwd: str | None = None) -> str:
    """Return a SHA-256 of ``git status --porcelain=v1 -uall``.

    When git is missing, the timeout fires, or *cwd* is not a git work tree,
    return ``NOGIT_TREE_FINGERPRINT`` (``nogit``). Drift detection then cannot
    prove a change; matching ``nogit`` values do not reject ``manage done``.
    """
    git_bin = shutil.which("git")
    if not git_bin:
        logger.info("tree fingerprint fallback nogit: git not on PATH")
        return NOGIT_TREE_FINGERPRINT
    try:
        proc = subprocess.run(  # nosec B603
            [git_bin, "status", "--porcelain=v1", "-uall"],
            cwd=cwd or None,
            capture_output=True,
            text=True,
            timeout=TREE_FINGERPRINT_TIMEOUT_SEC,
            check=False,
        )
    except (OSError, subprocess.TimeoutExpired) as exc:
        logger.info("tree fingerprint fallback nogit: %s", exc)
        return NOGIT_TREE_FINGERPRINT
    if proc.returncode != 0:
        logger.info("tree fingerprint fallback nogit: git exit %s", proc.returncode)
        return NOGIT_TREE_FINGERPRINT
    digest = hashlib.sha256(proc.stdout.encode("utf-8")).hexdigest()
    logger.debug("tree fingerprint cwd=%s digest=%s", cwd or ".", digest[:12])
    return digest


def lock_path() -> Path:
    return data_dir() / LOCK_FILE_NAME


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


# Same-thread reentry depth for goal_lock(). Cross-process exclusivity is
# unchanged: only the outermost acquire/release touches the OS lock.
_lock_nest = threading.local()


@contextmanager
def goal_lock() -> Iterator[None]:
    """Exclusive cross-process lock for goal.json / eval-signal mutations.

    Same-thread reentry is a no-op so helpers that already hold the lock
    (for example ``disarm`` → ``_read_pid_record`` → ``mark_orphan_wake``)
    cannot deadlock on Windows ``msvcrt.locking``.
    """
    depth = getattr(_lock_nest, "depth", 0)
    if depth > 0:
        _lock_nest.depth = depth + 1
        try:
            yield
        finally:
            _lock_nest.depth = depth
        return
    path = lock_path()
    path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
    _chmod_dir_private(path.parent)
    handle = open(path, "a+b")
    try:
        _lock_acquire(handle)
        _lock_nest.depth = 1
        try:
            yield
        finally:
            _lock_nest.depth = 0
    finally:
        try:
            _lock_release(handle)
        finally:
            handle.close()


def snapshot_goal(*, raise_corrupt: bool = False) -> GoalState | None:
    """Load goal.json under the exclusive lock (consistent observer snapshot)."""
    with goal_lock():
        return load_goal(raise_corrupt=raise_corrupt)


def native_continuation_env_disabled() -> bool:
    """True when ``CURSOR_GOAL_NATIVE=0`` forces hooks+wake for every goal."""
    raw = os.environ.get(NATIVE_CONTINUATION_ENV, "1").strip().lower()
    disabled = raw in {"0", "false", "no", "off"}
    logger.debug(
        "native continuation env disabled=%s raw=%r", disabled, raw or "<unset>"
    )
    return disabled


def resolve_native_continuation_flag(requested: bool) -> bool:
    """Persist native continuation only when requested and the env allows it."""
    if not requested:
        return False
    if native_continuation_env_disabled():
        logger.info(
            "Ignoring native continuation request (%s disables it)",
            NATIVE_CONTINUATION_ENV,
        )
        return False
    return True


def _fingerprint_cwd_for_state(state: GoalState) -> str | None:
    workdir = (state.workdir or "").strip()
    return workdir or None


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
        # utf-8-sig tolerates a BOM (e.g. from a Windows editor) that would
        # otherwise make a byte-for-byte valid JSON file fail to parse.
        raw = json.loads(path.read_text(encoding="utf-8-sig"))
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
    """Remove goal.json and eval/audit signals under lock.

    Returns True if a goal file existed.
    """
    with goal_lock():
        path = goal_path()
        existed = path.is_file()
        if existed:
            path.unlink()
        flag = eval_flag_path()
        if flag.exists():
            flag.unlink()
        audit_flag = audit_flag_path()
        if audit_flag.exists():
            audit_flag.unlink()
        confirm_flag = audit_confirm_flag_path()
        if confirm_flag.exists():
            confirm_flag.unlink()
        clear_last_stop_response()
        if existed:
            logger.info("Cleared goal and evaluator/audit signals")
        else:
            logger.info("Cleared evaluator/audit signals (no goal file)")
        return existed


def clear_eval_signal() -> None:
    with goal_lock():
        _clear_eval_signal_unlocked()


def clear_protocol_signals() -> None:
    """Clear YES and CLEAR signal files (validation / more-work happened)."""
    with goal_lock():
        _clear_eval_signal_unlocked()
        _clear_audit_signal_unlocked()


def normalize_block_reason(reason: str) -> str:
    """Collapse whitespace and case so the same blocker matches across turns."""
    return " ".join(reason.strip().lower().split())


def block_turn_key(state: GoalState) -> str:
    """Identify a pursuing turn for blocked-streak accounting."""
    return f"{int(state.turns_used)}:{int(state.wake_ticks)}"


def reset_block_streak(state: GoalState) -> None:
    """Start a fresh blocked audit (resume from blocked/paused)."""
    state.block_streak = 0
    state.last_block_reason = ""
    state.last_block_turn_key = ""


def take_condition_updated_pending() -> bool:
    """Consume the one-shot objective-updated flag. Returns the prior value."""
    consumed = False

    def mutator(state: GoalState) -> None:
        nonlocal consumed
        consumed = bool(state.condition_updated_pending)
        state.condition_updated_pending = False

    try:
        updated = mutate_goal(mutator)
    except (OSError, GoalLockTimeoutError) as exc:
        logger.warning("Could not consume condition_updated_pending: %s", exc)
        return False
    if updated is None:
        return False
    return consumed


def record_block_attempt(reason: str) -> tuple[GoalState | None, str]:
    """Record a same-reason blocked attempt under lock.

    Returns ``(state, status)`` where status is ``missing``, ``not_pursuing``,
    ``empty_reason``, ``recorded`` (streak still below threshold), or
    ``blocked`` (threshold reached; continuation stops).
    """
    normalized = normalize_block_reason(reason)
    if not normalized:
        return None, "empty_reason"
    with goal_lock():
        state = load_goal()
        if state is None:
            return None, "missing"
        if state.status != "pursuing" or not state.active:
            logger.info(
                "record_block_attempt refused status=%s active=%s",
                state.status,
                state.active,
            )
            return state, "not_pursuing"
        key = block_turn_key(state)
        same_turn = (
            state.last_block_turn_key == key and state.last_block_reason == normalized
        )
        if same_turn:
            logger.info(
                "record_block_attempt same turn key=%s streak=%s reason=%r",
                key,
                state.block_streak,
                normalized,
            )
            return state, "recorded"
        if state.last_block_reason == normalized:
            state.block_streak = int(state.block_streak) + 1
        else:
            state.block_streak = 1
            state.last_block_reason = _require_field_chars(
                "last_block_reason", normalized
            )
        state.last_block_turn_key = _require_field_chars("last_block_turn_key", key)
        state.last_reason = _require_field_chars("last_reason", reason.strip())
        became_blocked = state.block_streak >= BLOCK_STREAK_REQUIRED
        if became_blocked:
            state.status = "blocked"
            state.active = False
            logger.info(
                "Goal blocked after streak=%s reason=%r",
                state.block_streak,
                normalized,
            )
        else:
            logger.info(
                "Block streak=%s/%s reason=%r turn=%s",
                state.block_streak,
                BLOCK_STREAK_REQUIRED,
                normalized,
                key,
            )
        _save_goal_unlocked(state)
        return state, "blocked" if became_blocked else "recorded"


def update_goal_condition(condition: str) -> tuple[GoalState | None, str]:
    """Change the condition in place. Same ``created_at``; invalidate CLEAR+YES.

    Returns ``(state, status)`` where status is ``ok``, ``missing``,
    ``unchanged``, or ``not_updatable``.
    """
    cleaned = condition.strip()
    if not cleaned:
        return None, "empty"
    with goal_lock():
        state = load_goal()
        if state is None:
            return None, "missing"
        if state.status in {"achieved", "budget-limited"}:
            logger.info("update_goal_condition refused status=%s", state.status)
            return state, "not_updatable"
        if state.condition == cleaned:
            logger.info(
                "update_goal_condition unchanged created_at=%s", state.created_at
            )
            return state, "unchanged"
        state.condition = _require_field_chars("condition", cleaned)
        state.condition_updated_pending = True
        state.last_eval_verdict = ""
        state.last_audit_verdict = ""
        _clear_eval_signal_unlocked()
        _clear_audit_signal_unlocked()
        _save_goal_unlocked(state)
        logger.info(
            "Updated goal condition in place created_at=%s pending=true",
            state.created_at,
        )
        return state, "ok"


def _clear_eval_signal_unlocked() -> None:
    flag = eval_flag_path()
    if flag.exists():
        flag.unlink()
        logger.info("Cleared evaluator signal")


def _done_protocol_rejection(
    *,
    require_signal: bool,
    signaled: bool,
    audit_ok: bool,
    fingerprint_ok: bool,
    broad: bool,
    confirm_ok: bool,
    confirm_fp_ok: bool,
) -> str | None:
    """Return a ``manage done`` rejection status, or None if gates pass."""
    if not require_signal:
        return None
    if not signaled:
        return "rejected"
    if not audit_ok:
        return "rejected_audit"
    if not fingerprint_ok:
        return "rejected_audit_stale"
    if broad and not confirm_ok:
        return "rejected_audit_confirm"
    if broad and not confirm_fp_ok:
        return "rejected_audit_stale"
    return None


def _forced_done_status(
    *,
    signaled: bool,
    audit_ok: bool,
    fingerprint_ok: bool,
    broad: bool,
    confirm_ok: bool,
    confirm_fp_ok: bool,
) -> str:
    """Status when ``manage done --force`` bypasses protocol gates."""
    complete = signaled and audit_ok and fingerprint_ok
    if complete and broad and not (confirm_ok and confirm_fp_ok):
        return "forced"
    return "ok" if complete else "forced"


def mark_goal_achieved(*, require_signal: bool = True) -> tuple[GoalState | None, str]:
    """Mark goal achieved under lock.

    Returns ``(state, status)`` where status is ``ok``, ``missing``,
    ``rejected``, ``rejected_audit``, ``rejected_audit_stale``,
    ``rejected_audit_confirm``, ``not_pursuing``, or ``forced``.
    """
    with goal_lock():
        state = load_goal()
        if state is None:
            return None, "missing"
        if state.status != "pursuing":
            if require_signal:
                return state, "not_pursuing"
            # --force recovery may complete a non-pursuing goal.
            logger.warning(
                "mark_goal_achieved --force while status=%s (not pursuing)",
                state.status,
            )
        signaled = _has_eval_signal_unlocked(state)
        audit_ok = _has_audit_signal_unlocked(state)
        fingerprint_ok = _audit_fingerprint_matches_unlocked(state)
        confirm_ok = _has_audit_confirm_signal_unlocked(state)
        confirm_fp_ok = _audit_confirm_fingerprint_matches_unlocked(state)
        broad = is_broad_condition(state.condition)
        logger.info(
            "mark_goal_achieved require=%s yes=%s audit=%s fp=%s "
            "broad=%s confirm=%s confirm_fp=%s",
            require_signal,
            signaled,
            audit_ok,
            fingerprint_ok,
            broad,
            confirm_ok,
            confirm_fp_ok,
        )
        rejected = _done_protocol_rejection(
            require_signal=require_signal,
            signaled=signaled,
            audit_ok=audit_ok,
            fingerprint_ok=fingerprint_ok,
            broad=broad,
            confirm_ok=confirm_ok,
            confirm_fp_ok=confirm_fp_ok,
        )
        if rejected is not None:
            return state, rejected
        _clear_eval_signal_unlocked()
        _clear_audit_signal_unlocked()
        if require_signal:
            status = "ok"
        else:
            status = _forced_done_status(
                signaled=signaled,
                audit_ok=audit_ok,
                fingerprint_ok=fingerprint_ok,
                broad=broad,
                confirm_ok=confirm_ok,
                confirm_fp_ok=confirm_fp_ok,
            )
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
    return _has_bound_signal_unlocked(
        eval_flag_path(),
        state,
        expected_verdict="YES",
        label="Eval",
    )


def _clear_audit_signal_unlocked() -> None:
    flag = audit_flag_path()
    if flag.exists():
        flag.unlink()
        logger.info("Cleared remaining-work audit signal")
    _clear_audit_confirm_signal_unlocked()


def _clear_audit_confirm_signal_unlocked() -> None:
    flag = audit_confirm_flag_path()
    if flag.exists():
        flag.unlink()
        logger.info("Cleared remaining-work confirm audit signal")


def _write_audit_signal_unlocked(
    state: GoalState, *, reason: str, response_text: str = ""
) -> None:
    """Atomically write CLEAR-bound audit signal for *state* (caller holds lock)."""
    data_dir()
    flag = audit_flag_path()
    fingerprint = compute_tree_fingerprint(cwd=_fingerprint_cwd_for_state(state))
    payload = {
        "condition_hash": state.content_hash(),
        "created_at": now_iso(),
        "verdict": "CLEAR",
        "reason": reason,
        "tree_fingerprint": fingerprint,
        "response_hash": _audit_response_hash(response_text),
        "kind": "primary",
    }
    atomic_write_text(
        flag,
        json.dumps(payload, indent=2, ensure_ascii=False) + "\n",
    )
    logger.info(
        "Recorded remaining-work audit signal hash=%s fingerprint=%s",
        payload["condition_hash"],
        fingerprint[:16],
    )


def _write_audit_confirm_signal_unlocked(
    state: GoalState, *, reason: str, response_text: str
) -> None:
    """Write confirm-pass CLEAR; require a distinct primary CLEAR on this tree."""
    if not _has_audit_signal_unlocked(state):
        raise ValueError(
            "Cannot record confirm CLEAR without a primary CLEAR remaining-work "
            "audit signal"
        )
    if not _audit_fingerprint_matches_unlocked(state):
        raise ValueError(
            "Cannot record confirm CLEAR: primary remaining-work audit is stale "
            "(working tree changed)"
        )
    digest = _audit_response_hash(response_text)
    primary = _read_audit_flag_unlocked() or {}
    prior = str(primary.get("response_hash") or "")
    if prior and prior == digest:
        raise ValueError(
            "Confirm CLEAR response matches the primary CLEAR (copy-paste). "
            "Spawn a new goal-auditor with eval audit-prompt --confirm."
        )
    data_dir()
    flag = audit_confirm_flag_path()
    fingerprint = compute_tree_fingerprint(cwd=_fingerprint_cwd_for_state(state))
    payload = {
        "condition_hash": state.content_hash(),
        "created_at": now_iso(),
        "verdict": "CLEAR",
        "reason": reason,
        "tree_fingerprint": fingerprint,
        "response_hash": digest,
        "kind": "confirm",
    }
    atomic_write_text(
        flag,
        json.dumps(payload, indent=2, ensure_ascii=False) + "\n",
    )
    logger.info(
        "Recorded remaining-work confirm audit signal hash=%s fingerprint=%s",
        payload["condition_hash"],
        fingerprint[:16],
    )


def has_audit_signal() -> bool:
    """Return True when a CLEAR-bound audit signal matches the current tree."""
    with goal_lock():
        state = load_goal()
        if state is None:
            return False
        return _has_audit_signal_unlocked(
            state
        ) and _audit_fingerprint_matches_unlocked(state)


def has_audit_confirm_signal() -> bool:
    """Return True when a confirm-pass CLEAR matches the current tree."""
    with goal_lock():
        state = load_goal()
        if state is None:
            return False
        return _has_audit_confirm_signal_unlocked(
            state
        ) and _audit_confirm_fingerprint_matches_unlocked(state)


def audit_signal_tree_stale() -> bool:
    """Return True when CLEAR is bound but the working tree changed after it."""
    with goal_lock():
        state = load_goal()
        if state is None:
            return False
        if not _has_audit_signal_unlocked(state):
            return False
        return not _audit_fingerprint_matches_unlocked(state)


def audit_confirm_signal_tree_stale() -> bool:
    """Return True when confirm CLEAR is bound but the tree changed after it."""
    with goal_lock():
        state = load_goal()
        if state is None:
            return False
        if not _has_audit_confirm_signal_unlocked(state):
            return False
        return not _audit_confirm_fingerprint_matches_unlocked(state)


def _has_audit_signal_unlocked(state: GoalState) -> bool:
    return _has_bound_signal_unlocked(
        audit_flag_path(),
        state,
        expected_verdict="CLEAR",
        label="Audit",
    )


def _has_audit_confirm_signal_unlocked(state: GoalState) -> bool:
    return _has_bound_signal_unlocked(
        audit_confirm_flag_path(),
        state,
        expected_verdict="CLEAR",
        label="Audit-confirm",
    )


def _read_audit_flag_unlocked() -> dict[str, Any] | None:
    flag = audit_flag_path()
    if not flag.is_file():
        return None
    try:
        raw = json.loads(flag.read_text(encoding="utf-8-sig"))
    except (OSError, json.JSONDecodeError):
        return None
    if not isinstance(raw, dict):
        return None
    return raw


def _audit_fingerprint_matches_unlocked(state: GoalState) -> bool:
    """True when the CLEAR flag's tree fingerprint still matches *state*'s tree.

    Missing ``tree_fingerprint`` (pre-4.3.0 flags) is fail-closed (stale).
    """
    raw = _read_audit_flag_unlocked()
    if raw is None:
        return False
    stored = str(raw.get("tree_fingerprint") or "")
    if not stored:
        logger.warning("Audit CLEAR missing tree_fingerprint; treating as stale")
        return False
    current = compute_tree_fingerprint(cwd=_fingerprint_cwd_for_state(state))
    if stored != current:
        logger.info(
            "Audit tree fingerprint mismatch stored=%s current=%s",
            stored[:16],
            current[:16],
        )
        return False
    return True


def _read_audit_confirm_flag_unlocked() -> dict[str, Any] | None:
    flag = audit_confirm_flag_path()
    if not flag.is_file():
        return None
    try:
        raw = json.loads(flag.read_text(encoding="utf-8-sig"))
    except (OSError, json.JSONDecodeError):
        return None
    if not isinstance(raw, dict):
        return None
    return raw


def _audit_confirm_fingerprint_matches_unlocked(state: GoalState) -> bool:
    """True when the confirm CLEAR flag's tree fingerprint matches *state*."""
    raw = _read_audit_confirm_flag_unlocked()
    if raw is None:
        return False
    stored = str(raw.get("tree_fingerprint") or "")
    if not stored:
        logger.warning(
            "Audit confirm CLEAR missing tree_fingerprint; treating as stale"
        )
        return False
    current = compute_tree_fingerprint(cwd=_fingerprint_cwd_for_state(state))
    if stored != current:
        logger.info(
            "Audit confirm tree fingerprint mismatch stored=%s current=%s",
            stored[:16],
            current[:16],
        )
        return False
    return True


def _has_bound_signal_unlocked(
    flag: Path,
    state: GoalState,
    *,
    expected_verdict: str,
    label: str,
) -> bool:
    """True when *flag* is a hash-bound signal with *expected_verdict*."""
    if not flag.is_file():
        return False
    try:
        raw = json.loads(flag.read_text(encoding="utf-8-sig"))
    except (OSError, json.JSONDecodeError):
        try:
            if flag.stat().st_size == 0:
                logger.warning(
                    "Legacy empty %s signal ignored; re-run parse", label.lower()
                )
        except OSError:
            pass
        return False
    if not isinstance(raw, dict):
        return False
    expected = state.content_hash()
    actual = str(raw.get("condition_hash", ""))
    if actual != expected:
        logger.warning(
            "%s signal hash mismatch (stale/cross-goal); expected=%s got=%s",
            label,
            expected,
            actual,
        )
        return False
    verdict = str(raw.get("verdict", "")).upper()
    if verdict != expected_verdict:
        logger.warning(
            "%s signal missing %s verdict (got %r)",
            label,
            expected_verdict,
            verdict,
        )
        return False
    return True
