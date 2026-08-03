"""Install / health diagnostics for cursor-goal (``manage doctor``).

Detects stop-hook install path (classic vs. Teams marketplace), verifies the
data directory and Windows ACL hardening, checks for stale baked Python
interpreters in classic launchers, and reports wake/continuation readiness.
"""

from __future__ import annotations

import os
import shutil
import sys
from pathlib import Path

from cursor_goal import __version__
from cursor_goal.logging_config import get_logger
from cursor_goal.native_path import native_path, path_str_is_absolute
from cursor_goal.paths import harness_cmd_report, skill_root, wake_loop_invocation
from cursor_goal.state import (
    CorruptGoalError,
    GoalLockTimeoutError,
    GoalState,
    acl_harden_failure_message,
    assert_workdir_usable,
    data_dir,
    data_dir_is_insecure,
    snapshot_goal,
)
from cursor_goal.validation import deny_shell_enabled, try_split_argv
from cursor_goal.wake import NOTIFY_PATTERN
from cursor_goal.wake import status_info as wake_status_info
from cursor_goal.wake import wake_enabled

logger = get_logger("cursor_goal.doctor")

# Characters that break out of quoted ``"%CGP%"`` usage in .cmd launchers.
_UNSAFE_CGP_CHARS = frozenset('"&|^<>')
_MARKETPLACE_WALK_MAX_DEPTH = 6
_MARKETPLACE_WALK_MAX_HOOKS = 40


def _user_home() -> Path:
    """Host-native home directory (safe under ``os.name`` monkeypatches)."""
    return native_path(os.path.expanduser("~"))


def _validation_mode(state: GoalState) -> str:
    """Return argv|shell|none|denied for status/doctor."""
    cmd = (state.validation_command or "").strip()
    if not cmd:
        return "none"
    if deny_shell_enabled() or not state.shell_ok:
        if try_split_argv(cmd) is None:
            return "denied"
        return "argv"
    if try_split_argv(cmd) is None:
        return "shell"
    return "argv"


def _wake_loop_shell_hint() -> str:
    """OS-appropriate wake loop command for doctor / create hints."""
    try:
        return wake_loop_invocation()
    except ValueError as exc:
        logger.warning("Could not resolve wake loop hint: %s", exc)
        return "<unresolved-skill>/scripts/run_goal.py wake loop"


def _classic_hooks_configured() -> bool | None:
    """Return True/False if classic hooks.json is detectable; None if unknown."""
    hooks = _user_home() / ".cursor" / "hooks.json"
    if hooks.is_file():
        try:
            text = hooks.read_text(encoding="utf-8")
        except OSError:
            return None
        return "stop" in text and (
            "stop_hook" in text or "cursor_goal" in text or "run_goal" in text
        )
    skill_hook = (
        _user_home() / ".cursor" / "skills" / "goal" / "scripts" / "stop_hook.py"
    )
    if skill_hook.is_file():
        return False
    return None


def _hooks_json_looks_like_goal(text: str) -> bool:
    return "stop" in text and (
        "stop_hook" in text or "cursor_goal" in text or "CURSOR_PLUGIN_ROOT" in text
    )


def _collect_marketplace_hook_files(base: Path) -> list[Path]:
    """Bounded walk for ``hooks/hooks.json`` under Cursor plugin cache/local trees."""
    found: list[Path] = []

    def walk(current: Path, depth: int) -> None:
        if (
            depth > _MARKETPLACE_WALK_MAX_DEPTH
            or len(found) >= _MARKETPLACE_WALK_MAX_HOOKS
        ):
            return
        hooks = current / "hooks" / "hooks.json"
        if hooks.is_file():
            found.append(hooks)
            return
        try:
            children = list(current.iterdir())
        except OSError:
            return
        for child in children:
            if not child.is_dir():
                continue
            name = child.name
            if name.startswith("."):
                continue
            walk(child, depth + 1)

    if base.is_dir():
        walk(base, 0)
    return found


def _marketplace_hooks_configured() -> bool | None:
    """Detect Teams marketplace plugin stop hooks when possible."""
    roots: list[Path] = []
    hook_files: list[Path] = []
    env_root = (os.environ.get("CURSOR_PLUGIN_ROOT") or "").strip()
    if env_root:
        roots.append(native_path(env_root))
    cursor_home = _user_home() / ".cursor"
    plugins = cursor_home / "plugins"
    for candidate in (
        plugins / "cursor-goal",
        plugins / "cache" / "cursor-goal",
    ):
        if candidate.is_dir():  # pragma: no branch — layout optional
            roots.append(candidate)
    for base_name in ("cache", "local"):
        hook_files.extend(_collect_marketplace_hook_files(plugins / base_name))

    seen_file = False
    for root in roots:
        hooks = root / "hooks" / "hooks.json"
        if not hooks.is_file():
            alt = root / "skills" / "goal" / "scripts" / "stop_hook.py"
            if alt.is_file():
                seen_file = True
            continue
        seen_file = True
        try:
            text = hooks.read_text(encoding="utf-8")
        except OSError:  # pragma: no cover — rare IO race
            continue
        if _hooks_json_looks_like_goal(text):
            return True

    for hooks in hook_files:
        seen_file = True
        try:
            text = hooks.read_text(encoding="utf-8")
        except OSError:  # pragma: no cover — rare IO race
            continue
        if _hooks_json_looks_like_goal(text):
            return True

    if seen_file:
        return False
    if env_root:
        return False
    return None


def _cursor_goal_python_is_unsafe(value: str) -> bool:
    """Return True when *value* contains cmd metacharacters unsafe in .cmd quotes."""
    return any(ch in _UNSAFE_CGP_CHARS for ch in value)


def _install_version_failures() -> list[str]:
    """Hard-fail when classic/plugin skill VERSION drifts from package version."""
    try:
        root = skill_root()
    except ValueError:
        return []
    run_goal = root / "scripts" / "run_goal.py"
    if not run_goal.is_file():
        return []
    version_path = root / "VERSION"
    normalized = root.as_posix().replace("\\", "/")
    is_classic_user = "/.cursor/skills/goal" in normalized or normalized.endswith(
        ".cursor/skills/goal"
    )
    if not version_path.is_file():
        if is_classic_user:
            return [
                f"Installed skill VERSION missing at {version_path} "
                f"(package {__version__}). Re-run install-goal.sh / install-goal.ps1."
            ]
        return []
    try:
        stamped = version_path.read_text(encoding="utf-8").strip()
    except OSError as exc:
        return [f"Could not read installed skill VERSION: {exc}"]
    if stamped != __version__:
        return [
            f"Installed skill VERSION={stamped!r} != package {__version__}. "
            "Re-run the classic installer or sync the marketplace plugin."
        ]
    return []


def _hooks_look_configured() -> bool | None:
    """Return True/False if any install path has stop hooks; None if unknown."""
    classic = _classic_hooks_configured()
    market = _marketplace_hooks_configured()
    if classic is True or market is True:
        return True
    if classic is False or market is False:
        return False
    return None


def _hooks_stacking_failure() -> str | None:
    """Hard-fail message when classic and marketplace stop hooks both appear active."""
    classic = _classic_hooks_configured()
    market = _marketplace_hooks_configured()
    if classic is True and market is True:
        return (
            "Classic (~/.cursor/hooks.json) and marketplace plugin stop hooks both "
            "look configured — FAIL: pick one install path (uninstall classic hooks "
            "or disable the marketplace plugin) to avoid duplicate hook runs"
        )
    return None


def _hooks_stacking_warning() -> str | None:
    """Alias — stacking is a doctor hard-fail via ``_hooks_stacking_failure``."""
    return _hooks_stacking_failure()


def _is_absolute_interpreter_path(value: str) -> bool:
    """Return True when *value* looks like an absolute filesystem path."""
    return path_str_is_absolute(value)


def _baked_python_from_cmd(
    path: Path,
) -> str | None:  # pylint: disable=too-many-nested-blocks
    """Extract the absolute Python path baked into a classic .cmd launcher."""
    try:
        text = path.read_text(encoding="utf-8", errors="replace")
    except OSError:
        return None
    # Prefer CURSOR_GOAL_PYTHON override lines if present in marketplace-style cmds.
    for line in text.splitlines():
        stripped = line.strip()
        if not stripped or stripped.startswith("REM") or stripped.startswith("::"):
            continue
        lower = stripped.lower()
        if "cursor_goal_python" in lower:
            continue
        # Match: "C:\...\python.exe" ... -u "..."
        if ".exe" in lower or "python" in lower:
            # Quoted absolute path as first token
            if stripped.startswith('"'):
                end = stripped.find('"', 1)
                if end > 1:
                    candidate = stripped[1:end]
                    if path_str_is_absolute(candidate):
                        return candidate
    return None


def _stale_baked_python_failures() -> list[str]:
    """Hard-fail when classic stop/wake .cmd bake a missing interpreter."""
    if os.name != "nt":
        return []
    fails: list[str] = []
    try:
        root = skill_root()
    except ValueError:
        return fails
    for name in ("stop_hook.cmd", "wake_loop.cmd"):
        # Join with strings on *root* — avoid Path("scripts") which dispatches on
        # live os.name and raises UnsupportedOperation under nt mocks on POSIX.
        cmd_path = root / "scripts" / name
        if not cmd_path.is_file():
            continue
        baked = _baked_python_from_cmd(cmd_path)
        if not baked:
            continue
        env_py = (os.environ.get("CURSOR_GOAL_PYTHON") or "").strip()
        if env_py and _is_absolute_interpreter_path(env_py):
            # Marketplace/override path takes precedence — verify that instead.
            if not native_path(env_py).is_file():
                fails.append(
                    f"CURSOR_GOAL_PYTHON does not exist ({env_py}). "
                    "Fix the path or re-run the installer."
                )
            continue
        if not native_path(baked).is_file():
            fails.append(
                f"Baked Python in {cmd_path.name} is missing ({baked}). "
                "Re-run install-goal.ps1 after upgrading/moving Python."
            )
    return fails


# pylint: disable-next=too-many-branches,too-many-statements,too-many-locals
def cmd_doctor(_argv: list[str]) -> int:
    """Health check for install / data dir / wake / shell. Exit 1 on hard fail."""
    hard_fails: list[str] = []
    warnings: list[str] = []

    print("[goal] Doctor")
    print(f"  Python: {sys.version.split()[0]} ({sys.executable})")
    if sys.version_info < (3, 12):  # pragma: no cover — CI/runtime is 3.12+
        hard_fails.append("Python 3.12+ is required")

    try:
        ddir = data_dir(check_writable=False)
        print(f"  Data dir: {ddir}")
    except (OSError, ValueError) as exc:
        hard_fails.append(f"Cannot access data dir: {exc}")
        ddir = None

    if ddir is not None and data_dir_is_insecure(ddir):
        if os.name == "nt":
            hard_fails.append(
                f"Data directory is insecure ({ddir}). "
                "It must not be a symlink, junction, or other reparse point. "
                "Set CURSOR_GOAL_DATA to a normal private directory."
            )
        else:
            hard_fails.append(
                f"Data directory is insecure ({ddir}). "
                "It must not be a symlink, must be owned by you, and must not be "
                "group/world-writable. Run chmod 700 or set CURSOR_GOAL_DATA."
            )

    acl_fail = acl_harden_failure_message(ddir) if ddir is not None else None
    if acl_fail is not None:
        hard_fails.append(acl_fail)

    hooks_state = _hooks_look_configured()
    classic_hooks = _classic_hooks_configured()
    market_hooks = _marketplace_hooks_configured()
    if hooks_state is True:
        sources: list[str] = []
        if classic_hooks is True:
            sources.append("classic ~/.cursor/hooks.json")
        if market_hooks is True:
            sources.append("marketplace plugin hooks")
        label = " + ".join(sources) if sources else "detected"
        print(f"  Hooks: stop hook appears configured ({label})")
    elif hooks_state is False:
        hard_fails.append(
            "Goal skill/plugin scripts present but no stop hook was found. "
            "Re-run the classic installer, or enable the Teams marketplace plugin."
        )
    else:
        warnings.append(
            "Could not confirm stop hook configuration "
            "(classic hooks.json missing/unreadable and no marketplace plugin root)"
        )
    stacked = _hooks_stacking_failure()
    if stacked is not None:
        hard_fails.append(stacked)

    try:
        report = harness_cmd_report()
        print(f"  Harness: {report['run_goal']} (exists={report['exists']})")
        print(f"  Wake loop cmd: {report['wake_loop']}")
        if not report["exists"]:
            hard_fails.append(
                f"run_goal.py missing at {report['run_goal']}. "
                "Run the classic installer, enable the Teams marketplace plugin, "
                "or set CURSOR_GOAL_HOME / CURSOR_PLUGIN_ROOT to a tree that "
                "contains skills/goal/scripts/run_goal.py"
            )
    except ValueError as exc:
        warnings.append(f"Harness path unresolved: {exc}")

    if os.name == "nt":
        env_py = (os.environ.get("CURSOR_GOAL_PYTHON") or "").strip()
        if env_py:
            if _cursor_goal_python_is_unsafe(env_py):
                hard_fails.append(
                    "CURSOR_GOAL_PYTHON contains unsafe cmd metacharacters "
                    f'(one of " & | ^ < >): {env_py!r}'
                )
            elif not _is_absolute_interpreter_path(env_py):
                hard_fails.append(
                    "CURSOR_GOAL_PYTHON must be an absolute path to Python 3.12+ "
                    f"(got {env_py!r})"
                )
            else:
                print(f"  CURSOR_GOAL_PYTHON: {env_py}")
        else:
            msg = (
                "CURSOR_GOAL_PYTHON unset — marketplace stop_hook.cmd / wake_loop.cmd "
                "resolve Python via PATH (prefer classic install-goal.ps1 bake, or set "
                "CURSOR_GOAL_PYTHON to an absolute 3.12+ interpreter)"
            )
            if market_hooks is True:
                hard_fails.append(msg)
            else:
                warnings.append(msg)
        py = shutil.which("py") or shutil.which("python") or shutil.which("python3")
        if not py:
            no_py = (
                "No py/python/python3 on PATH — marketplace stop_hook.cmd / "
                "wake_loop.cmd cannot resolve Python; set CURSOR_GOAL_PYTHON to an "
                "absolute 3.12+ interpreter or use classic install-goal.ps1"
            )
            if market_hooks is True:
                hard_fails.append(no_py)
            else:
                warnings.append(no_py)
        else:
            print(f"  PATH Python: {py}")
        if market_hooks is True:
            print(
                "  Tip: Teams marketplace registers dual stop hooks (one per OS); "
                "Hooks UI may show a failure on the non-native entry by design."
            )

    hard_fails.extend(_stale_baked_python_failures())
    hard_fails.extend(_install_version_failures())

    try:
        state = snapshot_goal(raise_corrupt=True)
    except CorruptGoalError as exc:
        hard_fails.append(f"Corrupt goal.json: {exc}")
        state = None
    except GoalLockTimeoutError as exc:
        hard_fails.append(f"goal.lock timeout: {exc}")
        state = None

    wake_info = wake_status_info()
    if state is not None and state.active and state.status == "pursuing":
        print(f"  Goal: pursuing ({state.condition[:60]})")
        print(
            f"  Budgets: turns {state.turns_used}/{state.turn_budget}, "
            f"wake {state.wake_ticks}/{state.wake_budget}"
        )
        mode = _validation_mode(state)
        print(f"  Validation mode: {mode}")
        if mode == "shell":
            warnings.append(
                "Shell-mode validation active (trusted-user). "
                "Prefer argv-safe --test; shell requires --allow-shell "
                "(or unset CURSOR_GOAL_DENY_SHELL)."
            )
        if state.workdir:
            print(f"  Workdir: {state.workdir}")
            try:
                assert_workdir_usable(state.workdir)
            except ValueError as exc:
                warnings.append(str(exc))
        if wake_enabled():
            ready = bool(wake_info.get("continuation_ready"))
            reason = str(wake_info.get("continuation_reason") or "")
            print(f"  Continuation ready: {str(ready).lower()} ({reason})")
            hint = str(wake_info.get("command") or _wake_loop_shell_hint())
            pattern = str(wake_info.get("notify_pattern") or NOTIFY_PATTERN)
            if not wake_info.get("armed") or reason == "not_armed":
                hard_fails.append(
                    "Wake not armed while pursuing — BLOCKING: start background Shell: "
                    f"`{hint}` with notify_on_output matching {pattern}, then confirm "
                    "wake status continuation_ready=true before other work"
                )
            elif (
                not wake_info.get("pid_alive")
                or reason == "pid_dead"
                or reason == "pid_unverified"
            ):
                hard_fails.append(
                    "Wake armed but loop not alive/verified — BLOCKING: start: "
                    f"`{hint}` with notify_on_output matching {pattern}, then confirm "
                    "continuation_ready=true / pid_alive=true "
                    f"(reason={reason or 'pid_dead'})"
                )
            if wake_info.get("heartbeat_stale"):
                warnings.append(
                    "Wake heartbeat_stale — PID alive but last_emit_at older than "
                    "2× interval; restart wake loop if continuation stalls"
                )
        else:
            print("  Continuation ready: true (disabled)")
            print("  Wake: disabled (CURSOR_GOAL_WAKE=0) — liveness gate skipped")
    elif state is None:
        print("  Goal: none")
    else:
        print(f"  Goal: {state.status}")

    if not wake_enabled():
        print("  Wake: disabled")
    elif wake_info.get("armed"):
        print(
            f"  Wake: armed interval_s={wake_info.get('interval_s')} "
            f"alive={wake_info.get('pid_alive')} "
            f"last_emit={wake_info.get('last_emit_at') or 'never'}"
        )
    else:
        print("  Wake: not armed")

    last_stop = (ddir / "last-stop-response.json") if ddir is not None else None
    if last_stop is not None and last_stop.is_file():
        print(f"  Last stop response: {last_stop}")
        print(
            "  Tip: If Hooks UI shows {{}} but this file has followup_message, "
            "Cursor dropped stdout (known race) — rely on wake."
        )
    else:
        warnings.append(
            "No last-stop-response.json yet (normal before first stop emit)"
        )

    fail_open = (ddir / "stop-failopen-continues") if ddir is not None else None
    if fail_open is not None and fail_open.is_file():
        try:
            count = int(fail_open.read_text(encoding="utf-8").strip() or "0")
        except (OSError, ValueError):
            count = -1
        if count > 0:
            warnings.append(
                f"Stop fail-open continue counter is {count} "
                "(persist failures while pursuing — wake should still continue)"
            )

    if os.environ.get("CURSOR_GOAL_LOG_SECRETS", "").strip().lower() in {
        "1",
        "true",
        "yes",
        "on",
    }:
        warnings.append("CURSOR_GOAL_LOG_SECRETS is enabled")

    log_file = os.environ.get("CURSOR_GOAL_LOG_FILE", "").strip()
    if log_file:
        print(f"  Durable log: CURSOR_GOAL_LOG_FILE={log_file}")
    else:
        log_level = os.environ.get("CURSOR_GOAL_LOG", "").strip()
        if not log_level:
            tip = (
                "For richer diagnostics set CURSOR_GOAL_LOG=INFO "
                "or CURSOR_GOAL_LOG_FILE=1"
            )
            if state is not None and state.active and state.status == "pursuing":
                warnings.append(tip)
            else:
                print(f"  Tip: {tip}")

    for item in warnings:
        print(f"  Warning: {item}")
    for item in hard_fails:
        print(f"  FAIL: {item}", file=sys.stderr)

    if hard_fails:
        print("[goal] Doctor: FAILED", file=sys.stderr)
        if not os.environ.get("CURSOR_GOAL_LOG_FILE", "").strip():
            print(
                "[goal] Tip: set CURSOR_GOAL_LOG_FILE=1 for durable diagnostics.",
                file=sys.stderr,
            )
        return 1
    if warnings:
        print("[goal] Doctor: OK (with warnings)")
    else:
        print("[goal] Doctor: OK")
    return 0
