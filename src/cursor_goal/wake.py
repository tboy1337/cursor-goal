"""Goal wake watchdog: race-immune continuation via shell notify sentinels.

Cursor's stop-hook stdout capture can drop ``followup_message``. This module
arms a background loop that emits ``AGENT_GOAL_WAKE`` while a goal is
``pursuing``. Agents start ``wake loop`` with ``notify_on_output`` matching
``^AGENT_GOAL_WAKE`` (same pattern as Cursor's ``/loop`` skill).
"""

from __future__ import annotations

import json
import os
import signal
import sys
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from cursor_goal.logging_config import get_logger
from cursor_goal.state import data_dir, load_goal

logger = get_logger("cursor_goal.wake")

WAKE_JSON_NAME = "wake.json"
WAKE_PID_NAME = "wake.pid"
SENTINEL_PREFIX = "AGENT_GOAL_WAKE"
DEFAULT_INTERVAL_S = 45
MIN_INTERVAL_S = 5
MAX_INTERVAL_S = 600


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


def wake_pid_path() -> Path:
    return data_dir() / WAKE_PID_NAME


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")


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
    path = wake_json_path()
    path.write_text(
        json.dumps(config, indent=2, ensure_ascii=False) + "\n",
        encoding="utf-8",
    )


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


def _read_pid() -> int | None:
    path = wake_pid_path()
    if not path.is_file():
        return None
    try:
        raw = path.read_text(encoding="utf-8").strip()
        return int(raw)
    except (OSError, ValueError):
        return None


def _write_pid(pid: int) -> None:
    wake_pid_path().write_text(f"{pid}\n", encoding="utf-8")


def _clear_pid() -> None:
    path = wake_pid_path()
    try:
        path.unlink(missing_ok=True)
    except OSError as exc:
        logger.debug("Could not remove wake pid file: %s", exc)


def _kill_pid(pid: int) -> None:
    if pid <= 0:
        return
    if pid == os.getpid():
        logger.debug("Skipping kill of current process pid=%s", pid)
        return
    if not _pid_alive(pid):
        return
    try:
        os.kill(pid, signal.SIGTERM)
    except OSError as exc:
        logger.debug("Could not signal wake pid %s: %s", pid, exc)


def _goal_is_pursuing() -> bool:
    state = load_goal()
    if state is None:
        return False
    return bool(state.active and state.status == "pursuing")


def _followup_prompt() -> str:
    state = load_goal()
    if state is None:
        return (
            "[GOAL] Resume working toward the active goal. "
            "Evaluate completion via subagent when ready."
        )
    remaining = max(0, state.turn_budget - state.turns_used)
    return (
        f"[GOAL] Turn {state.turns_used}/{state.turn_budget} "
        f"({remaining} remaining). Continue working toward: {state.condition}. "
        "Evaluate completion via subagent when ready. "
        "(Wake watchdog — stop-hook followup may have been dropped.)"
    )


def emit_wake_line(prompt: str | None = None) -> None:
    """Print one notify_on_output sentinel line to stdout."""
    text = prompt if prompt is not None else _followup_prompt()
    payload = json.dumps({"prompt": text}, ensure_ascii=False)
    sys.stdout.write(f"{SENTINEL_PREFIX} {payload}\n")
    sys.stdout.flush()


def arm(*, interval: int | None = None) -> dict[str, Any]:
    """Write wake.json. Returns config dict (may be empty if disabled)."""
    if not wake_enabled():
        logger.info("Wake arm skipped (CURSOR_GOAL_WAKE disabled)")
        return {}
    if interval is not None:
        seconds = _clamp_interval(interval)
    else:
        seconds = _interval_from_env_or(DEFAULT_INTERVAL_S)
    config: dict[str, Any] = {
        "armed_at": _now_iso(),
        "interval_s": seconds,
        "sentinel": SENTINEL_PREFIX,
        "notify_pattern": f"^{SENTINEL_PREFIX}",
    }
    _write_wake_config(config)
    logger.info("Wake armed interval_s=%s", seconds)
    return config


def disarm(*, kill_loop: bool = True) -> bool:
    """Remove wake state; optionally signal the loop process. Returns True if armed."""
    existed = wake_json_path().is_file() or wake_pid_path().is_file()
    if kill_loop:
        pid = _read_pid()
        if pid is not None:
            _kill_pid(pid)
    _clear_pid()
    try:
        wake_json_path().unlink(missing_ok=True)
    except OSError as exc:
        logger.debug("Could not remove wake.json: %s", exc)
    if existed:
        logger.info("Wake disarmed")
    return existed


def tick() -> int:
    """Emit a wake sentinel if pursuing; auto-disarm when inactive. Exit 0."""
    if not wake_enabled():
        return 0
    config = _read_wake_config()
    if config is None:
        # Not armed via wake.json — still allow one-shot tick if pursuing
        # so scripts can probe without arm (no-op when idle).
        if not _goal_is_pursuing():
            return 0
        emit_wake_line()
        return 0

    if not _goal_is_pursuing():
        logger.info("Wake tick: goal not pursuing; disarming")
        disarm(kill_loop=False)
        return 0

    emit_wake_line()
    return 0


def status_report() -> dict[str, Any]:
    """Return wake status as a JSON-serializable dict."""
    config = _read_wake_config()
    pid = _read_pid()
    return {
        "enabled": wake_enabled(),
        "armed": config is not None,
        "config": config,
        "pid": pid,
        "pid_alive": _pid_alive(pid) if pid is not None else False,
        "goal_pursuing": _goal_is_pursuing(),
        "sentinel": SENTINEL_PREFIX,
        "notify_pattern": f"^{SENTINEL_PREFIX}",
    }


def run_loop(*, interval: int | None = None) -> int:
    """Block: sleep/tick until disarmed or goal leaves pursuing."""
    if not wake_enabled():
        print("[goal] Wake disabled (CURSOR_GOAL_WAKE=0).", file=sys.stderr)
        return 0

    config = arm(interval=interval)
    if not config:
        return 0

    seconds = int(config["interval_s"])
    _write_pid(os.getpid())
    logger.info("Wake loop started pid=%s interval_s=%s", os.getpid(), seconds)
    print(
        f"[goal] Wake loop running (pid={os.getpid()}, every {seconds}s). "
        f"Notify pattern: ^{SENTINEL_PREFIX}",
        file=sys.stderr,
    )

    try:
        while True:
            time.sleep(seconds)
            if _read_wake_config() is None:
                logger.info("Wake loop: config cleared; exiting")
                break
            if not _goal_is_pursuing():
                logger.info("Wake loop: goal not pursuing; exiting")
                disarm(kill_loop=False)
                break
            emit_wake_line()
    except KeyboardInterrupt:
        print("[goal] Wake loop interrupted.", file=sys.stderr)
    finally:
        _clear_pid()
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


def cmd_wake(argv: list[str]) -> int:
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
            print(
                "  Unix: python3 -u ~/.cursor/skills/goal/scripts/run_goal.py wake loop"
            )
            print(
                '  Windows: py -3 -u "$env:USERPROFILE\\.cursor\\skills\\goal\\'
                'scripts\\run_goal.py" wake loop'
            )
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
    print("  arm [--interval N]   Write wake.json (default interval 45s)")
    print("  tick                 Emit AGENT_GOAL_WAKE if goal is pursuing")
    print("  disarm               Stop loop (if any) and clear wake state")
    print("  status               Print wake status JSON")
    print("  loop [--interval N]  Run sleep/tick until disarmed or not pursuing")
