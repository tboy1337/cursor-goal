"""Wake continuation-readiness verdicts (status/doctor/agents).

Looks up helpers on :mod:`cursor_goal.wake` at call time so tests that
monkeypatch ``wake_mod._pid_alive`` (and similar) still apply.
"""

# pylint: disable=protected-access

from __future__ import annotations

from datetime import datetime, timezone
from typing import Any

import cursor_goal.wake as wake_mod


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
    raw_interval = wake_mod.DEFAULT_INTERVAL_S
    if isinstance(interval_s, int) and not isinstance(interval_s, bool):
        raw_interval = interval_s
    elif isinstance(interval_s, str):
        try:
            raw_interval = int(interval_s)
        except ValueError:
            raw_interval = wake_mod.DEFAULT_INTERVAL_S
    interval = wake_mod._clamp_interval(raw_interval)
    age = (
        datetime.now(timezone.utc) - stamped.astimezone(timezone.utc)
    ).total_seconds()
    return age > float(wake_mod.HEARTBEAT_STALE_MULTIPLIER * interval)


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
    is_enabled = wake_mod.wake_enabled() if enabled is None else enabled
    command = wake_mod._wake_loop_command()
    pattern = wake_mod.NOTIFY_PATTERN
    if not is_enabled:
        return {
            "continuation_ready": True,
            "reason": "disabled",
            "heartbeat_stale": False,
            "command": command,
            "pattern": pattern,
            "notify_pattern": pattern,
        }
    pursuing = wake_mod._goal_is_pursuing() if goal_pursuing is None else goal_pursuing
    if not pursuing:
        return {
            "continuation_ready": True,
            "reason": "not_pursuing",
            "heartbeat_stale": False,
            "command": command,
            "pattern": pattern,
            "notify_pattern": pattern,
        }
    config = wake_mod._read_wake_config() if armed is None else None
    is_armed = (config is not None) if armed is None else armed
    ownership_checked = False
    is_owned = True
    if pid_alive is None:
        record = wake_mod._read_pid_record()  # type: ignore[attr-defined]
        pid = int(record["pid"]) if record is not None else None
        is_alive = (
            wake_mod._pid_alive(pid)  # type: ignore[attr-defined]
            if pid is not None
            else False
        )
        if is_alive and pid is not None:
            ownership_checked = True
            is_owned = wake_mod._pid_looks_owned(pid)  # type: ignore[attr-defined]
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
