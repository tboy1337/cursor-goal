"""Goal wake watchdog: race-immune continuation via shell notify sentinels.

Cursor's stop-hook stdout capture can drop ``followup_message``. This module
arms a background loop that emits ``AGENT_GOAL_WAKE`` while a goal is
``pursuing``. Agents start ``wake loop`` with ``notify_on_output`` matching
``^AGENT_GOAL_WAKE`` (same pattern as Cursor's ``/loop`` skill).

Ownership uses a generation token shared by ``wake.json`` and ``wake.pid`` so
restarts cannot leave orphan loops or clear a newer loop's PID file.
"""

from __future__ import annotations

import json
import os
import secrets
import signal
import subprocess  # nosec B404 — taskkill / ownership checks only
import sys
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from cursor_goal.logging_config import get_logger
from cursor_goal.state import (
    GoalState,
    atomic_write_text,
    budgets_exhausted,
    data_dir,
    goal_lock,
    mutate_goal,
    refuse_if_data_dir_insecure,
    snapshot_goal,
)

logger = get_logger("cursor_goal.wake")

WAKE_JSON_NAME = "wake.json"
WAKE_PID_NAME = "wake.pid"
SENTINEL_PREFIX = "AGENT_GOAL_WAKE"
DEFAULT_INTERVAL_S = 15
MIN_INTERVAL_S = 5
MAX_INTERVAL_S = 600
SLEEP_SLICE_S = 0.5


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


def wake_pid_path() -> Path:
    return data_dir() / WAKE_PID_NAME


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


def _read_pid_record() -> (  # pylint: disable=too-many-return-statements
    dict[str, Any] | None
):
    """Return ``{pid, token, started_at}`` or None. Accepts legacy plain-int files."""
    path = wake_pid_path()
    if not path.is_file():
        return None
    try:
        raw = path.read_text(encoding="utf-8").strip()
    except OSError:
        return None
    if not raw:
        return None
    try:
        data = json.loads(raw)
    except json.JSONDecodeError:
        try:
            # Legacy plain-int pid files have no ownership token.
            return {
                "pid": int(raw),
                "token": str(),
                "started_at": str(),
            }
        except ValueError:
            return None
    if isinstance(data, int):
        return {
            "pid": int(data),
            "token": str(),
            "started_at": str(),
        }
    if isinstance(data, dict) and "pid" in data:
        try:
            return {
                "pid": int(data["pid"]),
                "token": str(data.get("token") or ""),
                "started_at": str(data.get("started_at") or ""),
            }
        except (TypeError, ValueError):
            return None
    return None


def _read_pid() -> int | None:
    record = _read_pid_record()
    if record is None:
        return None
    return int(record["pid"])


def _write_pid_record(pid: int, token: str) -> None:
    payload = {
        "pid": pid,
        "token": token,
        "started_at": _now_iso(),
    }
    with goal_lock():
        _atomic_write_text(
            wake_pid_path(),
            json.dumps(payload, indent=2, ensure_ascii=False) + "\n",
        )


def _write_pid(pid: int, token: str | None = None) -> None:
    """Write pid ownership record (tests may omit token)."""
    _write_pid_record(pid, token if token else secrets.token_hex(8))


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
    """Return True when *cmdline* looks like a cursor-goal / wake harness process."""
    lowered = cmdline.strip().lower()
    if not lowered:
        return False
    # Require cursor-goal identity — never match bare "wake" (false-positive kill).
    if "cursor_goal" in lowered or "cursor-goal" in lowered:
        return True
    if "run_goal.py" in lowered:
        return True
    return False


def _windows_pid_looks_owned(pid: int) -> bool:
    """Best-effort: confirm PID exists and command line mentions wake/goal."""
    try:
        completed = subprocess.run(  # nosec B603 B607 — fixed powershell args
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
    return _cmdline_looks_owned(completed.stdout or "")


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
        completed = subprocess.run(  # nosec B603 B607 — fixed ps argv
            ["ps", "-p", str(int(pid)), "-o", "command="],
            capture_output=True,
            text=True,
            timeout=5,
            check=False,
        )
    except (OSError, subprocess.TimeoutExpired) as exc:
        logger.debug("Unix ps ownership probe failed for pid=%s: %s", pid, exc)
        return False
    if completed.returncode != 0:
        return False
    return _cmdline_looks_owned(completed.stdout or "")


def _kill_pid(pid: int, *, token: str | None = None) -> None:
    """Signal a wake loop process. Verify ownership before kill (PID reuse)."""
    if pid <= 0:
        return
    if pid == os.getpid():
        logger.debug("Skipping kill of current process pid=%s", pid)
        return
    if not _pid_alive(pid):
        return

    record = _read_pid_record()
    if token is not None and record is not None:
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


def _kill_existing_loop() -> None:
    """Stop any previously recorded wake loop before taking ownership."""
    record = _read_pid_record()
    if record is None:
        return
    pid = int(record["pid"])
    token = str(record.get("token") or "")
    if pid == os.getpid():
        return
    _kill_pid(pid, token=token or None)
    _clear_pid()


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
    return (
        f"[GOAL] Turn {state.turns_used}/{state.turn_budget} "
        f"({remaining} remaining, wake_ticks={wake_ticks}/"
        f"{state.wake_budget}, wake_remaining={wake_remaining}). "
        f"Continue working toward: {state.condition}. "
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


def _record_wake_tick() -> GoalState | None:
    """Increment wake_ticks; mark budget-limited when exhausted. Returns state."""

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
        return mutate_goal(mutator)
    except ValueError:
        return snapshot_goal()
    except OSError as exc:
        logger.error("Failed to persist wake_ticks: %s", exc)
        return snapshot_goal()


def emit_wake_line(prompt: str | None = None) -> None:
    """Print one notify_on_output sentinel line to stdout."""
    text = prompt if prompt is not None else _followup_prompt()
    payload = json.dumps({"prompt": text}, ensure_ascii=False)
    sys.stdout.write(f"{SENTINEL_PREFIX} {payload}\n")
    sys.stdout.flush()
    _touch_last_emit()


def _touch_last_emit() -> None:
    """Record last_emit_at on wake.json when armed."""
    with goal_lock():
        config = _read_wake_config()
        if config is None:
            return
        config["last_emit_at"] = _now_iso()
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
    if interval is not None:
        seconds = _clamp_interval(interval)
    else:
        seconds = _interval_from_env_or(DEFAULT_INTERVAL_S)
    token = secrets.token_hex(8)
    config: dict[str, Any] = {
        "armed_at": _now_iso(),
        "interval_s": seconds,
        "sentinel": SENTINEL_PREFIX,
        "notify_pattern": f"^{SENTINEL_PREFIX}",
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


def tick() -> int:
    """Emit a wake sentinel if pursuing; auto-disarm when inactive. Exit 0."""
    if not wake_enabled():
        return 0
    insecure = refuse_if_data_dir_insecure()
    if insecure is not None:
        logger.warning("Wake tick refused: %s", insecure)
        return 1
    config = _read_wake_config()
    if config is None:
        if not _goal_is_pursuing():
            return 0
        emit_wake_line()
        return 0

    if not _goal_is_pursuing():
        logger.info("Wake tick: goal not pursuing; disarming")
        disarm(kill_loop=False)
        return 0

    state = _record_wake_tick()
    if state is not None and state.status == "budget-limited":
        emit_wake_line(
            f"[GOAL BUDGET] Wake tick limit ({state.wake_budget}) reached. "
            f"Wrap up current work and summarize progress toward: {state.condition}"
        )
        disarm(kill_loop=True)
        return 0

    emit_wake_line()
    return 0


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
        "pid_alive": _pid_alive(pid) if pid is not None else False,
        "goal_pursuing": _goal_is_pursuing(),
        "interval_s": (config or {}).get("interval_s"),
        "last_emit_at": (config or {}).get("last_emit_at"),
        "wake_ticks": None if state is None else state.wake_ticks,
        "wake_budget": None if state is None else state.wake_budget,
        "wake_remaining": wake_remaining,
        "sentinel": SENTINEL_PREFIX,
        "notify_pattern": f"^{SENTINEL_PREFIX}",
    }


def status_info() -> dict[str, Any]:
    """Compact wake health for manage status/doctor."""
    report = status_report()
    return {
        "armed": bool(report.get("armed")),
        "pid_alive": bool(report.get("pid_alive")),
        "interval_s": report.get("interval_s"),
        "token_prefix": report.get("token_prefix"),
        "last_emit_at": report.get("last_emit_at"),
        "wake_remaining": report.get("wake_remaining"),
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


def run_loop(*, interval: int | None = None) -> int:
    """Block: emit immediately, then sleep/tick until disarmed or not pursuing."""
    if not wake_enabled():
        print("[goal] Wake disabled (CURSOR_GOAL_WAKE=0).", file=sys.stderr)
        return 0
    insecure = refuse_if_data_dir_insecure()
    if insecure is not None:
        print(insecure, file=sys.stderr)
        return 1

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

            state = _record_wake_tick()
            if state is not None and state.status == "budget-limited":
                emit_wake_line(
                    f"[GOAL BUDGET] Wake tick limit ({state.wake_budget}) reached. "
                    f"Wrap up current work and summarize progress toward: "
                    f"{state.condition}"
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
    print(
        f"  arm [--interval N]   Write wake.json "
        f"(default interval {DEFAULT_INTERVAL_S}s)"
    )
    print("  tick                 Emit AGENT_GOAL_WAKE if goal is pursuing")
    print("  disarm               Stop loop (if any) and clear wake state")
    print("  status               Print wake status JSON")
    print("  loop [--interval N]  Run sleep/tick until disarmed or not pursuing")
