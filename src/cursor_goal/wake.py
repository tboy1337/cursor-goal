"""Goal wake watchdog: race-immune continuation via shell notify sentinels.

Cursor's stop-hook stdout capture can drop ``followup_message``. This module
arms a background loop that emits ``AGENT_GOAL_WAKE`` while a goal is
``pursuing``. Agents start ``wake loop`` with ``notify_on_output`` matching
``^AGENT_GOAL_WAKE`` (same pattern as Cursor's ``/loop`` skill).

Ownership uses a generation token shared by ``wake.json`` and ``wake.pid`` so
restarts cannot leave orphan loops or clear a newer loop's PID file. PID-file
ownership / kill mechanics live in :mod:`cursor_goal.wake_process` and are
imported/re-exported here so existing ``from cursor_goal.wake import ...``
call sites (including tests) keep working unchanged.
"""

# pylint: disable=too-many-lines,unused-import

from __future__ import annotations

import json
import os
import secrets
import sys
import time
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from cursor_goal.logging_config import get_logger
from cursor_goal.paths import wake_loop_invocation
from cursor_goal.state import (
    GoalState,
    atomic_write_text,
    budgets_exhausted,
    data_dir,
    goal_lock,
    mutate_goal,
    refuse_if_acl_harden_failed,
    refuse_if_data_dir_insecure,
    snapshot_goal,
)
from cursor_goal.validation import redact_secrets
from cursor_goal.wake_process import (  # noqa: F401 — re-exports for tests/callers
    WAKE_ORPHAN_NAME,
    WAKE_PID_NAME,
    _clear_pid,
    _cmdline_looks_owned,
    _kill_pid,
    _pid_alive,
    _pid_looks_owned,
    _read_pid,
    _read_pid_record,
    _unix_pid_looks_owned,
    _windows_pid_looks_owned,
    _write_pid,
    _write_pid_record,
    clear_orphan_wake,
    mark_orphan_wake,
    read_orphan_wake,
    wake_orphan_path,
    wake_pid_path,
)
from cursor_goal.win_acl import harden_windows_acl

logger = get_logger("cursor_goal.wake")

WAKE_JSON_NAME = "wake.json"
SENTINEL_PREFIX = "AGENT_GOAL_WAKE"
NOTIFY_PATTERN = f"^{SENTINEL_PREFIX}"
GOAL_WAKE_REQUIRED_PREFIX = "GOAL_WAKE_REQUIRED"
DEFAULT_INTERVAL_S = 15
MIN_INTERVAL_S = 5
MAX_INTERVAL_S = 600
SLEEP_SLICE_S = 0.5
HEARTBEAT_STALE_MULTIPLIER = 2


def wake_enabled() -> bool:
    """Return False when CURSOR_GOAL_WAKE disables the watchdog."""
    raw = os.environ.get("CURSOR_GOAL_WAKE", "1").strip().lower()
    return raw not in {"0", "false", "no", "off"}


def _interval_from_env_or(default: int) -> int:
    raw = os.environ.get("CURSOR_GOAL_WAKE_INTERVAL_S")
    if raw is None or raw == "":
        return _clamp_interval(default)
    try:
        value = int(raw)
    except ValueError:
        logger.warning(
            "Invalid CURSOR_GOAL_WAKE_INTERVAL_S=%r; using %s",
            raw,
            default,
        )
        return _clamp_interval(default)
    return _clamp_interval(value)


def _clamp_interval(value: int) -> int:
    if value < MIN_INTERVAL_S:
        logger.warning(
            "Wake interval %s below min %s; clamping",
            value,
            MIN_INTERVAL_S,
        )
        return MIN_INTERVAL_S
    if value > MAX_INTERVAL_S:
        logger.warning(
            "Wake interval %s exceeds max %s; clamping",
            value,
            MAX_INTERVAL_S,
        )
        return MAX_INTERVAL_S
    return value


def wake_json_path() -> Path:
    return data_dir() / WAKE_JSON_NAME


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")


def _atomic_write_text(path: Path, text: str) -> None:
    """Write text via temp file + replace; apply private mode bits."""
    atomic_write_text(path, text)


def _read_wake_config() -> dict[str, Any] | None:
    path = wake_json_path()
    if not path.is_file():
        return None
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        logger.warning("Could not read %s: %s", path, exc)
        return None
    if not isinstance(data, dict):
        return None
    return data


def _write_wake_config(config: dict[str, Any]) -> None:
    _atomic_write_text(
        wake_json_path(),
        json.dumps(config, indent=2, ensure_ascii=False) + "\n",
    )


def _kill_existing_loop() -> None:
    """Stop any previously recorded wake loop before taking ownership."""
    record = _read_pid_record()
    if record is None:
        return
    pid = int(record["pid"])
    token = str(record.get("token") or "")
    if pid == os.getpid():
        return
    if not token:
        if _pid_alive(pid) and _pid_looks_owned(pid):
            logger.warning(
                "Killing legacy tokenless wake pid=%s after ownership probe OK",
                pid,
            )
            _kill_pid(pid, token="")
            _clear_pid()
            clear_orphan_wake()
            return
        if _pid_alive(pid):
            logger.warning(
                "Leaving orphan wake pid=%s (no ownership token; ownership "
                "unverified); clearing wake.pid without signaling. Re-arm "
                "after confirming no leftover loop.",
                pid,
            )
            mark_orphan_wake(
                pid,
                "legacy tokenless wake.pid while pid still alive and "
                "ownership unverified",
            )
            _clear_pid()
            return
        _clear_pid()
        clear_orphan_wake()
        return
    _kill_pid(pid, token=token)
    _clear_pid()
    clear_orphan_wake()


def _goal_is_pursuing() -> bool:
    state = snapshot_goal()
    if state is None:
        return False
    return bool(state.active and state.status == "pursuing")


def _followup_prompt() -> str:
    state = snapshot_goal()
    if state is None:
        return (
            "[GOAL] Resume working toward the active goal. "
            "Evaluate completion via subagent when ready."
        )
    remaining = max(0, state.turn_budget - state.turns_used)
    wake_remaining = max(0, int(state.wake_budget) - int(state.wake_ticks))
    wake_ticks = int(getattr(state, "wake_ticks", 0) or 0)
    safe_condition = redact_secrets(state.condition, max_chars=None)
    return (
        f"[GOAL] Turn {state.turns_used}/{state.turn_budget} "
        f"({remaining} remaining, wake_ticks={wake_ticks}/"
        f"{state.wake_budget}, wake_remaining={wake_remaining}). "
        f"Continue working toward: {safe_condition}. "
        "Evaluate completion via subagent when ready. "
        "(Wake watchdog — stop-hook followup may have been dropped.)"
    )


def _budget_exhausted(state: GoalState) -> bool:
    return budgets_exhausted(
        state.turns_used,
        state.turn_budget,
        state.wake_ticks,
        state.wake_budget,
    )


@dataclass(frozen=True)
class WakeTickResult:
    """Outcome of charging one wake tick against the goal budget."""

    status: str  # ok | inactive | persist_failed | budget_limited
    state: GoalState | None = None


def _record_wake_tick() -> WakeTickResult:
    """Increment wake_ticks; mark budget-limited when exhausted.

    Persist failures return ``persist_failed`` so callers fail closed and do
    not emit a wake sentinel without charging the budget.
    """

    def mutator(state: GoalState) -> None:
        if not state.active or state.status != "pursuing":
            raise ValueError("inactive")
        state.wake_ticks = int(state.wake_ticks) + 1
        if _budget_exhausted(state):
            state.status = "budget-limited"
            state.active = False
            state.last_reason = (
                f"wake budget exhausted ({state.wake_ticks}/{state.wake_budget})"
            )

    try:
        updated = mutate_goal(mutator)
    except ValueError:
        return WakeTickResult(status="inactive", state=snapshot_goal())
    except OSError as exc:
        logger.error("Failed to persist wake_ticks: %s", exc)
        return WakeTickResult(status="persist_failed", state=None)
    if updated is None:
        return WakeTickResult(status="inactive", state=None)
    if updated.status == "budget-limited":
        return WakeTickResult(status="budget_limited", state=updated)
    return WakeTickResult(status="ok", state=updated)


def allow_dead_wake() -> bool:
    """Escape hatch for validate when wake loop is intentionally down."""
    raw = os.environ.get("CURSOR_GOAL_ALLOW_DEAD_WAKE", "").strip().lower()
    return raw in {"1", "true", "yes", "on"}


def refuse_if_wake_dead() -> str | None:
    """Return an error when pursuing without a live wake loop.

    Stop hook stays fail-open. Escape with ``CURSOR_GOAL_ALLOW_DEAD_WAKE=1`` or
    disable wake via ``CURSOR_GOAL_WAKE=0``.
    """
    if not wake_enabled() or allow_dead_wake():
        return None
    state = snapshot_goal()
    if state is None or not state.active or state.status != "pursuing":
        return None
    readiness = continuation_readiness(goal_pursuing=True)
    if readiness.get("continuation_ready"):
        return None
    reason = str(readiness.get("reason") or "pid_dead")
    hint = str(readiness.get("command") or _wake_loop_command())
    pattern = str(readiness.get("pattern") or NOTIFY_PATTERN)
    if reason == "not_armed":
        return (
            "[goal] Error: wake not armed while pursuing "
            f"(continuation_ready=false reason=not_armed). "
            f"Arm and start: `{hint}` with notify_on_output matching {pattern}. "
            "Or set CURSOR_GOAL_ALLOW_DEAD_WAKE=1 to override."
        )
    return (
        "[goal] Error: wake loop not alive while pursuing "
        f"(continuation_ready=false reason={reason}). "
        f"Start background Shell: `{hint}` with notify_on_output "
        f"matching {pattern}, then confirm wake status "
        "continuation_ready=true / pid_alive=true. "
        "Or set CURSOR_GOAL_ALLOW_DEAD_WAKE=1 to override."
    )


def record_agent_nudge(*, source: str = "wake") -> None:
    """Stamp last_nudge_at / last_nudge_source for wake→wake coalesce.

    Only ``source=\"wake\"`` suppresses a subsequent wake tick. Stop stamps
    (``source=\"stop\"``) are diagnostics only so a dropped stop followup
    cannot delay the race-immune wake path for a full interval.
    """
    nudge_source = source if source in ("stop", "wake") else "wake"
    with goal_lock():
        config = _read_wake_config()
        if config is None:
            return
        config["last_nudge_at"] = _now_iso()
        config["last_nudge_source"] = nudge_source
        try:
            _write_wake_config(config)
        except OSError as exc:
            logger.debug("Could not update last_nudge_at: %s", exc)


def _nudge_within_coalesce_window(config: dict[str, Any] | None) -> bool:
    """Return True when a recent *wake* nudge should suppress this wake emit.

    Stop-originated stamps never coalesce. Missing ``last_nudge_source`` is
    treated as ``wake`` for older wake.json files written before sourcing.
    """
    if config is None:
        return False
    raw_source = config.get("last_nudge_source")
    if raw_source is None:
        nudge_source = "wake"
    elif isinstance(raw_source, str) and raw_source in ("stop", "wake"):
        nudge_source = raw_source
    else:
        return False
    if nudge_source != "wake":
        return False
    raw = config.get("last_nudge_at")
    if not raw or not isinstance(raw, str):
        return False
    try:
        stamped = datetime.fromisoformat(raw.replace("Z", "+00:00"))
    except ValueError:
        return False
    interval = _clamp_interval(int(config.get("interval_s") or DEFAULT_INTERVAL_S))
    age = (
        datetime.now(timezone.utc) - stamped.astimezone(timezone.utc)
    ).total_seconds()
    return age < float(interval)


def emit_wake_line(prompt: str | None = None) -> None:
    """Print one notify_on_output sentinel line to stdout."""
    text = prompt if prompt is not None else _followup_prompt()
    payload = json.dumps({"prompt": text}, ensure_ascii=False)
    sys.stdout.write(f"{SENTINEL_PREFIX} {payload}\n")
    sys.stdout.flush()
    _touch_last_emit()


def _touch_last_emit() -> None:
    """Record last_emit_at and wake-sourced nudge stamp on wake.json when armed."""
    with goal_lock():
        config = _read_wake_config()
        if config is None:
            return
        now = _now_iso()
        config["last_emit_at"] = now
        config["last_nudge_at"] = now
        config["last_nudge_source"] = "wake"
        try:
            _write_wake_config(config)
        except OSError as exc:
            logger.debug("Could not update wake last_emit_at: %s", exc)


def arm(*, interval: int | None = None) -> dict[str, Any]:
    """Write wake.json. Returns config dict (may be empty if disabled)."""
    if not wake_enabled():
        logger.info("Wake arm skipped (CURSOR_GOAL_WAKE disabled)")
        return {}
    insecure = refuse_if_data_dir_insecure()
    if insecure is not None:
        raise OSError(insecure)
    acl_fail = refuse_if_acl_harden_failed()
    if acl_fail is not None:
        raise OSError(acl_fail)
    if interval is not None:
        seconds = _clamp_interval(interval)
    else:
        seconds = _interval_from_env_or(DEFAULT_INTERVAL_S)
    token = secrets.token_hex(8)
    config: dict[str, Any] = {
        "armed_at": _now_iso(),
        "interval_s": seconds,
        "sentinel": SENTINEL_PREFIX,
        "notify_pattern": NOTIFY_PATTERN,
        "token": token,
    }
    with goal_lock():
        _write_wake_config(config)
    logger.info("Wake armed interval_s=%s token=%s", seconds, token[:8])
    return config


def disarm(*, kill_loop: bool = True) -> bool:
    """Remove wake state; optionally signal the loop process. Returns True if armed."""
    with goal_lock():
        existed = wake_json_path().is_file() or wake_pid_path().is_file()
        # Clear config first so a running loop exits on next slice check.
        try:
            wake_json_path().unlink(missing_ok=True)
        except OSError as exc:
            logger.debug("Could not remove wake.json: %s", exc)
        record = _read_pid_record() if kill_loop else None
        if not kill_loop:
            _clear_pid()
    if kill_loop:
        if record is not None:
            _kill_pid(
                int(record["pid"]),
                token=str(record.get("token") or "") or None,
            )
        with goal_lock():
            _clear_pid()
    if existed:
        logger.info("Wake disarmed")
    return existed


def tick() -> int:  # pylint: disable=too-many-return-statements
    """Emit a wake sentinel if pursuing; auto-disarm when inactive. Exit 0."""
    if not wake_enabled():
        return 0
    insecure = refuse_if_data_dir_insecure()
    if insecure is not None:
        logger.warning("Wake tick refused: %s", insecure)
        return 1
    acl_fail = refuse_if_acl_harden_failed()
    if acl_fail is not None:
        logger.warning("Wake tick refused: %s", acl_fail)
        return 1
    config = _read_wake_config()
    if config is None:
        if not _goal_is_pursuing():
            return 0
        # Fail closed: never emit without an armed config + charged tick.
        logger.warning("Wake tick: goal pursuing but wake not armed; refusing emit")
        return 1

    if not _goal_is_pursuing():
        logger.info("Wake tick: goal not pursuing; disarming")
        disarm(kill_loop=False)
        return 0

    if _nudge_within_coalesce_window(config):
        logger.info(
            "Wake tick coalesced (recent stop/wake nudge within interval_s=%s)",
            config.get("interval_s"),
        )
        return 0

    result = _record_wake_tick()
    if result.status == "persist_failed":
        logger.error("Wake tick: failed to persist wake_ticks; refusing emit")
        return 1
    if result.status == "inactive":
        logger.info("Wake tick: goal not pursuing after tick attempt; disarming")
        disarm(kill_loop=False)
        return 0
    if result.status == "budget_limited":
        state = result.state
        assert state is not None
        safe_condition = redact_secrets(state.condition, max_chars=None)
        emit_wake_line(
            f"[GOAL BUDGET] Wake tick limit ({state.wake_budget}) reached. "
            f"Wrap up current work and summarize progress toward: {safe_condition}"
        )
        disarm(kill_loop=True)
        return 0

    emit_wake_line()
    return 0


def _wake_loop_command() -> str:
    """Best-effort Shell command string for starting the wake loop."""
    try:
        return wake_loop_invocation()
    except ValueError as exc:
        logger.warning("Could not resolve wake loop command: %s", exc)
        return "<unresolved-skill>/scripts/run_goal.py wake loop"


def _heartbeat_stale(
    *,
    armed: bool,
    pid_alive: bool,
    last_emit_at: object,
    interval_s: object,
) -> bool:
    """True when the loop is alive but last_emit_at is older than 2× interval.

    Missing ``last_emit_at`` (armed but not yet emitted) is not stale.
    """
    if not armed or not pid_alive:
        return False
    if not last_emit_at or not isinstance(last_emit_at, str):
        return False
    try:
        stamped = datetime.fromisoformat(last_emit_at.replace("Z", "+00:00"))
    except ValueError:
        return False
    raw_interval = DEFAULT_INTERVAL_S
    if isinstance(interval_s, int) and not isinstance(interval_s, bool):
        raw_interval = interval_s
    elif isinstance(interval_s, str):
        try:
            raw_interval = int(interval_s)
        except ValueError:
            raw_interval = DEFAULT_INTERVAL_S
    interval = _clamp_interval(raw_interval)
    age = (
        datetime.now(timezone.utc) - stamped.astimezone(timezone.utc)
    ).total_seconds()
    return age > float(HEARTBEAT_STALE_MULTIPLIER * interval)


def continuation_readiness(  # pylint: disable=too-many-return-statements,too-many-branches
    *,
    enabled: bool | None = None,
    armed: bool | None = None,
    pid_alive: bool | None = None,
    goal_pursuing: bool | None = None,
    last_emit_at: object = None,
    interval_s: object = None,
) -> dict[str, Any]:
    """Single continuation-readiness verdict for status/doctor/agents.

    ``continuation_ready`` is false only while pursuing with wake enabled and
    the loop missing (not armed or PID dead). Heartbeat staleness is a warning
    only (ready stays true, ``reason`` may be ``heartbeat_stale``).
    """
    is_enabled = wake_enabled() if enabled is None else enabled
    command = _wake_loop_command()
    pattern = NOTIFY_PATTERN
    if not is_enabled:
        return {
            "continuation_ready": True,
            "reason": "disabled",
            "heartbeat_stale": False,
            "command": command,
            "pattern": pattern,
            "notify_pattern": pattern,
        }
    pursuing = _goal_is_pursuing() if goal_pursuing is None else goal_pursuing
    if not pursuing:
        return {
            "continuation_ready": True,
            "reason": "not_pursuing",
            "heartbeat_stale": False,
            "command": command,
            "pattern": pattern,
            "notify_pattern": pattern,
        }
    config = _read_wake_config() if armed is None else None
    is_armed = (config is not None) if armed is None else armed
    ownership_checked = False
    is_owned = True
    if pid_alive is None:
        record = _read_pid_record()
        pid = int(record["pid"]) if record is not None else None
        is_alive = _pid_alive(pid) if pid is not None else False
        if is_alive and pid is not None:
            ownership_checked = True
            is_owned = _pid_looks_owned(pid)
    else:
        is_alive = pid_alive
    emit_at = last_emit_at
    interval = interval_s
    if config is not None:
        if emit_at is None:
            emit_at = config.get("last_emit_at")
        if interval is None:
            interval = config.get("interval_s")
    if not is_armed:
        return {
            "continuation_ready": False,
            "reason": "not_armed",
            "heartbeat_stale": False,
            "command": command,
            "pattern": pattern,
            "notify_pattern": pattern,
        }
    if not is_alive:
        return {
            "continuation_ready": False,
            "reason": "pid_dead",
            "heartbeat_stale": False,
            "command": command,
            "pattern": pattern,
            "notify_pattern": pattern,
        }
    if ownership_checked and not is_owned:
        return {
            "continuation_ready": False,
            "reason": "pid_unverified",
            "heartbeat_stale": False,
            "command": command,
            "pattern": pattern,
            "notify_pattern": pattern,
        }
    stale = _heartbeat_stale(
        armed=True,
        pid_alive=True,
        last_emit_at=emit_at,
        interval_s=interval,
    )
    return {
        "continuation_ready": True,
        "reason": "heartbeat_stale" if stale else "ready",
        "heartbeat_stale": stale,
        "command": command,
        "pattern": pattern,
        "notify_pattern": pattern,
    }


def wake_required_event(config: dict[str, Any]) -> dict[str, Any]:
    """Payload for the ``GOAL_WAKE_REQUIRED`` machine-readable create/resume line."""
    pattern = str(config.get("notify_pattern") or NOTIFY_PATTERN)
    return {
        "command": _wake_loop_command(),
        "pattern": pattern,
        "notify_pattern": pattern,
        "interval_s": config.get("interval_s"),
    }


def format_wake_required_line(config: dict[str, Any]) -> str:
    """One stdout line agents can parse after successful arm."""
    payload = json.dumps(wake_required_event(config), ensure_ascii=False)
    return f"{GOAL_WAKE_REQUIRED_PREFIX} {payload}"


def status_report() -> dict[str, Any]:
    """Return wake status as a JSON-serializable dict."""
    config = _read_wake_config()
    record = _read_pid_record()
    pid = int(record["pid"]) if record is not None else None
    token = str((record or {}).get("token") or (config or {}).get("token") or "")
    state = snapshot_goal()
    wake_remaining = None
    if state is not None:
        wake_remaining = max(0, int(state.wake_budget) - int(state.wake_ticks))
    pid_alive = _pid_alive(pid) if pid is not None else False
    pid_owned = _pid_looks_owned(pid) if pid_alive and pid is not None else False
    pursuing = _goal_is_pursuing()
    interval_s = (config or {}).get("interval_s")
    last_emit_at = (config or {}).get("last_emit_at")
    # Let continuation_readiness re-read PID so ownership is enforced.
    readiness = continuation_readiness(
        enabled=wake_enabled(),
        armed=config is not None,
        goal_pursuing=pursuing,
        last_emit_at=last_emit_at,
        interval_s=interval_s,
    )
    # Do not expose full generation token in status JSON (prefix only).
    return {
        "enabled": wake_enabled(),
        "armed": config is not None,
        "config": (
            {**config, "token": (token[:8] + "…") if token else None}
            if config is not None
            else None
        ),
        "pid": pid,
        "token": (token[:8] + "…") if token else None,
        "token_prefix": token[:8] if token else None,
        "pid_alive": pid_alive,
        "pid_owned": pid_owned,
        "goal_pursuing": pursuing,
        "interval_s": interval_s,
        "last_emit_at": last_emit_at,
        "wake_ticks": None if state is None else state.wake_ticks,
        "wake_budget": None if state is None else state.wake_budget,
        "wake_remaining": wake_remaining,
        "sentinel": SENTINEL_PREFIX,
        "notify_pattern": NOTIFY_PATTERN,
        "pattern": NOTIFY_PATTERN,
        "command": readiness["command"],
        "continuation_ready": readiness["continuation_ready"],
        "continuation_reason": readiness["reason"],
        "heartbeat_stale": readiness["heartbeat_stale"],
    }


def status_info() -> dict[str, Any]:
    """Compact wake health for manage status/doctor."""
    report = status_report()
    return {
        "armed": bool(report.get("armed")),
        "pid_alive": bool(report.get("pid_alive")),
        "pid_owned": bool(report.get("pid_owned")),
        "interval_s": report.get("interval_s"),
        "token_prefix": report.get("token_prefix"),
        "last_emit_at": report.get("last_emit_at"),
        "wake_remaining": report.get("wake_remaining"),
        "enabled": bool(report.get("enabled")),
        "command": report.get("command"),
        "notify_pattern": report.get("notify_pattern"),
        "pattern": report.get("pattern") or report.get("notify_pattern"),
        "continuation_ready": bool(report.get("continuation_ready")),
        "continuation_reason": report.get("continuation_reason"),
        "heartbeat_stale": bool(report.get("heartbeat_stale")),
    }


def _interruptible_sleep(seconds: float, token: str) -> bool:
    """Sleep in slices; return False if config cleared or token mismatch."""
    deadline = time.monotonic() + max(0.0, seconds)
    while time.monotonic() < deadline:
        config = _read_wake_config()
        if config is None:
            return False
        if str(config.get("token") or "") != token:
            logger.info("Wake loop: token mismatch; exiting")
            return False
        remaining = deadline - time.monotonic()
        time.sleep(min(SLEEP_SLICE_S, max(0.0, remaining)))
    return True


def run_loop(*, interval: int | None = None) -> int:  # pylint: disable=too-many-branches
    """Block: emit immediately, then sleep/tick until disarmed or not pursuing."""
    if not wake_enabled():
        print("[goal] Wake disabled (CURSOR_GOAL_WAKE=0).", file=sys.stderr)
        return 0
    insecure = refuse_if_data_dir_insecure()
    if insecure is not None:
        print(insecure, file=sys.stderr)
        return 1
    acl_fail = refuse_if_acl_harden_failed()
    if acl_fail is not None:
        print(acl_fail, file=sys.stderr)
        return 1

    # Re-verify ACL harden at loop start (process-local cache can go stale).
    try:
        harden_windows_acl(data_dir(check_writable=False), force=True)
    except OSError as exc:
        logger.warning("Wake loop ACL re-harden failed: %s", exc)

    _kill_existing_loop()
    config = arm(interval=interval)
    if not config:
        return 0

    seconds = int(config["interval_s"])
    token = str(config["token"])
    my_pid = os.getpid()
    _write_pid_record(my_pid, token)
    logger.info(
        "Wake loop started pid=%s interval_s=%s token=%s",
        my_pid,
        seconds,
        token[:8],
    )
    print(
        f"[goal] Wake loop running (pid={my_pid}, every {seconds}s). "
        f"Notify pattern: ^{SENTINEL_PREFIX}",
        file=sys.stderr,
    )

    try:
        while True:
            if _read_wake_config() is None:
                logger.info("Wake loop: config cleared; exiting")
                break
            cfg = _read_wake_config()
            if cfg is None or str(cfg.get("token") or "") != token:
                logger.info("Wake loop: token/config gone; exiting")
                break
            if not _goal_is_pursuing():
                logger.info("Wake loop: goal not pursuing; exiting")
                disarm(kill_loop=False)
                break

            result = _record_wake_tick()
            if result.status == "persist_failed":
                logger.error(
                    "Wake loop: failed to persist wake_ticks; exiting without emit"
                )
                print(
                    "[goal] Wake loop: failed to persist wake_ticks; exiting.",
                    file=sys.stderr,
                )
                return 1
            if result.status == "inactive":
                logger.info("Wake loop: goal not pursuing after tick; exiting")
                disarm(kill_loop=False)
                break
            if result.status == "budget_limited":
                state = result.state
                assert state is not None
                safe_condition = redact_secrets(state.condition, max_chars=None)
                emit_wake_line(
                    f"[GOAL BUDGET] Wake tick limit ({state.wake_budget}) reached. "
                    f"Wrap up current work and summarize progress toward: "
                    f"{safe_condition}"
                )
                disarm(kill_loop=False)
                break

            emit_wake_line()
            if not _interruptible_sleep(float(seconds), token):
                break
    except KeyboardInterrupt:
        print("[goal] Wake loop interrupted.", file=sys.stderr)
    finally:
        _clear_pid(only_if_pid=my_pid, only_if_token=token)
    return 0


def _parse_interval_flag(argv: list[str]) -> tuple[int | None, list[str]]:
    """Extract --interval N from argv; return (interval_or_None, remaining)."""
    interval: int | None = None
    rest: list[str] = []
    i = 0
    while i < len(argv):
        arg = argv[i]
        if arg == "--interval":
            if i + 1 >= len(argv):
                raise ValueError("--interval requires a value")
            try:
                interval = int(argv[i + 1])
            except ValueError as exc:
                raise ValueError(
                    f"--interval must be an integer, got {argv[i + 1]!r}"
                ) from exc
            i += 2
        else:
            rest.append(arg)
            i += 1
    return interval, rest


# pylint: disable-next=too-many-return-statements,too-many-branches
def cmd_wake(
    argv: list[str],
) -> int:
    """CLI: wake arm|tick|disarm|status|loop."""
    if not argv or argv[0] in {"-h", "--help", "help"}:
        _print_help()
        return 0 if argv and argv[0] in {"-h", "--help", "help"} else 1

    command = argv[0]
    rest = argv[1:]

    try:
        if command == "arm":
            interval, leftover = _parse_interval_flag(rest)
            if leftover:
                print(
                    f"[goal] Error: unexpected arguments: {' '.join(leftover)}",
                    file=sys.stderr,
                )
                return 1
            if not wake_enabled():
                print("[goal] Wake disabled (CURSOR_GOAL_WAKE=0).")
                return 0
            config = arm(interval=interval)
            print("[goal] Wake armed:")
            print(f"  Interval: {config['interval_s']}s")
            print(f"  Sentinel: {config['sentinel']}")
            print(f"  Notify pattern: {config['notify_pattern']}")
            print(
                "  Start loop in background with notify_on_output, then continue work."
            )
            try:
                print(f"  {wake_loop_invocation()}")
            except ValueError as exc:
                logger.warning("Could not resolve wake loop path: %s", exc)
                print("  (resolve with: manage harness-cmd)")
            return 0

        if command == "tick":
            return tick()

        if command == "disarm":
            existed = disarm(kill_loop=True)
            if existed:
                print("[goal] Wake disarmed.")
            else:
                print("[goal] Wake was not armed.")
            return 0

        if command == "status":
            report = status_report()
            print(json.dumps(report, indent=2, ensure_ascii=False))
            return 0

        if command == "loop":
            interval, leftover = _parse_interval_flag(rest)
            if leftover:
                print(
                    f"[goal] Error: unexpected arguments: {' '.join(leftover)}",
                    file=sys.stderr,
                )
                return 1
            return run_loop(interval=interval)
    except ValueError as exc:
        print(f"[goal] Error: {exc}", file=sys.stderr)
        return 1
    except OSError as exc:
        print(f"[goal] Error: {exc}", file=sys.stderr)
        return 1

    print(f"[goal] Error: unknown wake command: {command}", file=sys.stderr)
    _print_help()
    return 1


def _print_help() -> None:
    print("Usage: cursor-goal wake <command> [args...]")
    print(
        f"  arm [--interval N]   Write wake.json "
        f"(default interval {DEFAULT_INTERVAL_S}s)"
    )
    print("  tick                 Emit AGENT_GOAL_WAKE if goal is pursuing")
    print("  disarm               Stop loop (if any) and clear wake state")
    print("  status               Print wake status JSON")
    print("  loop [--interval N]  Run sleep/tick until disarmed or not pursuing")
