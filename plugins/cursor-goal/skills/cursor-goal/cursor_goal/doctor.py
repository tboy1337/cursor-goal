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
from typing import Any

from cursor_goal import __version__
from cursor_goal.logging_config import get_logger
from cursor_goal.models import (
    EVAL_MODEL_ENV,
    eval_model_is_known_invalid,
    resolve_eval_model,
)
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
from cursor_goal.validation import deny_shell_enabled, redact_secrets, try_split_argv
from cursor_goal.wake import NOTIFY_PATTERN
from cursor_goal.wake import status_info as wake_status_info
from cursor_goal.wake import wake_enabled
from cursor_goal.wake_process import read_orphan_wake
from cursor_goal.win_acl import harden_windows_acl

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
        _user_home() / ".cursor" / "skills" / "cursor-goal" / "scripts" / "stop_hook.py"
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


def _marketplace_hook_roots(env_root: str) -> list[Path]:
    """Candidate marketplace/plugin roots that might contain ``hooks.json``."""
    roots: list[Path] = []
    if env_root:
        roots.append(native_path(env_root))
    plugins = _user_home() / ".cursor" / "plugins"
    for candidate in (plugins / "cursor-goal", plugins / "cache" / "cursor-goal"):
        if candidate.is_dir():  # pragma: no branch — layout optional
            roots.append(candidate)
    return roots


def _hooks_file_has_goal_marker(hooks: Path) -> bool:
    """Return True if *hooks* (a ``hooks.json``) mentions the goal skill/plugin."""
    try:
        text = hooks.read_text(encoding="utf-8")
    except OSError:  # pragma: no cover — rare IO race
        return False
    return _hooks_json_looks_like_goal(text)


def _scan_root_for_marketplace_hooks(root: Path) -> tuple[bool, bool]:
    """Inspect one candidate root's ``hooks.json`` / classic ``stop_hook.py``.

    Returns ``(found, seen)``: ``found`` is True when this root's
    ``hooks.json`` mentions the goal skill; ``seen`` is True when either a
    ``hooks.json`` or a classic ``stop_hook.py`` exists under this root.
    """
    hooks = root / "hooks" / "hooks.json"
    if not hooks.is_file():
        alt = root / "skills" / "cursor-goal" / "scripts" / "stop_hook.py"
        return False, alt.is_file()
    return _hooks_file_has_goal_marker(hooks), True


def _marketplace_hooks_configured() -> bool | None:
    """Detect Teams marketplace plugin stop hooks when possible."""
    env_root = (os.environ.get("CURSOR_PLUGIN_ROOT") or "").strip()
    plugins = _user_home() / ".cursor" / "plugins"
    hook_files: list[Path] = []
    for base_name in ("cache", "local"):
        hook_files.extend(_collect_marketplace_hook_files(plugins / base_name))

    seen_file = False
    for root in _marketplace_hook_roots(env_root):
        found, seen = _scan_root_for_marketplace_hooks(root)
        seen_file = seen_file or seen
        if found:
            return True
    for hooks in hook_files:
        seen_file = True
        if _hooks_file_has_goal_marker(hooks):
            return True

    if seen_file or env_root:
        return False
    return None


def _cursor_goal_python_is_unsafe(value: str) -> bool:
    """Return True when *value* contains cmd metacharacters unsafe in .cmd quotes."""
    return any(ch in _UNSAFE_CGP_CHARS for ch in value)


def _legacy_user_skill_failures() -> list[str]:
    """Hard-fail when the pre-v5 ``~/.cursor/skills/goal`` tree is still present."""
    leftover = _user_home() / ".cursor" / "skills" / "goal" / "SKILL.md"
    if leftover.is_file():
        logger.warning("Legacy user skill still present path=%s", leftover)
        return [
            "Legacy user skill ~/.cursor/skills/goal still exists (collides with "
            "Cursor's built-in /goal). Re-run install-goal.sh / install-goal.ps1 "
            "to migrate to ~/.cursor/skills/cursor-goal and remove the old skill."
        ]
    return []


def _skill_layout_warnings() -> list[str]:
    """Warn about leftover bak folders and Cursor's built-in /goal skill."""
    warnings: list[str] = []
    skills = _user_home() / ".cursor" / "skills"
    if skills.is_dir():
        try:
            bak_dirs = [
                path
                for path in skills.iterdir()
                if path.is_dir() and path.name.startswith("goal.bak.")
            ]
        except OSError as exc:
            logger.warning("Could not scan skills dir for bak folders: %s", exc)
            bak_dirs = []
        if bak_dirs:
            warnings.append(
                f"Leftover skill backup folders under ~/.cursor/skills "
                f"({len(bak_dirs)}). Re-run the installer to move them to "
                "~/.cursor-goal/backups."
            )
    builtin = _user_home() / ".cursor" / "skills-cursor" / "goal" / "SKILL.md"
    if builtin.is_file():
        warnings.append(
            "Cursor built-in /goal is present (~/.cursor/skills-cursor/goal). "
            "Expected: layer it under this harness (CreateGoal for continuation; "
            "CLEAR+YES then manage done then UpdateGoal complete). Vanilla /goal "
            "without this skill still uses same-model self-audit."
        )
    return warnings


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
    is_classic_user = (
        "/.cursor/skills/cursor-goal" in normalized
        or normalized.endswith(".cursor/skills/cursor-goal")
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


def _is_absolute_interpreter_path(value: str) -> bool:
    """Return True when *value* looks like an absolute filesystem path."""
    return path_str_is_absolute(value)


def _leading_quoted_absolute_path(stripped_line: str) -> str | None:
    """Return the leading ``"..."``-quoted token of *stripped_line* if absolute."""
    if not stripped_line.startswith('"'):
        return None
    end = stripped_line.find('"', 1)
    if end <= 1:
        return None
    candidate = stripped_line[1:end]
    return candidate if path_str_is_absolute(candidate) else None


def _baked_python_from_line(stripped: str) -> str | None:
    """Extract a baked absolute Python path from one .cmd launcher line, if any."""
    if not stripped or stripped.startswith("REM") or stripped.startswith("::"):
        return None
    lower = stripped.lower()
    if "cursor_goal_python" in lower:
        return None
    # Match: "C:\...\python.exe" ... -u "..."
    if ".exe" not in lower and "python" not in lower:
        return None
    return _leading_quoted_absolute_path(stripped)


def _baked_python_from_cmd(path: Path) -> str | None:
    """Extract the absolute Python path baked into a classic .cmd launcher."""
    try:
        text = path.read_text(encoding="utf-8", errors="replace")
    except OSError:
        return None
    # Prefer CURSOR_GOAL_PYTHON override lines if present in marketplace-style cmds.
    for line in text.splitlines():
        found = _baked_python_from_line(line.strip())
        if found is not None:
            return found
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


def _doctor_check_python(hard_fails: list[str]) -> None:
    print(f"  Python: {sys.version.split()[0]} ({sys.executable})")
    if sys.version_info < (3, 12):  # pragma: no cover — CI/runtime is 3.12+
        hard_fails.append("Python 3.12+ is required")


def _doctor_check_data_dir(hard_fails: list[str]) -> Path | None:
    try:
        ddir = data_dir(check_writable=False)
        print(f"  Data dir: {ddir}")
    except (OSError, ValueError) as exc:
        hard_fails.append(f"Cannot access data dir: {exc}")
        return None

    if data_dir_is_insecure(ddir):
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

    if os.name == "nt":
        # Force re-harden so long-lived processes cannot stale-trust forever.
        harden_windows_acl(ddir, force=True)

    acl_fail = acl_harden_failure_message(ddir)
    if acl_fail is not None:
        hard_fails.append(acl_fail)
    return ddir


def _doctor_check_eval_model(hard_fails: list[str]) -> None:
    resolved_eval_model = resolve_eval_model()
    print(f"  Evaluator model: {resolved_eval_model}")
    raw_eval_model_env = (os.environ.get(EVAL_MODEL_ENV) or "").strip()
    if raw_eval_model_env and eval_model_is_known_invalid(raw_eval_model_env):
        hard_fails.append(
            f"{EVAL_MODEL_ENV}={raw_eval_model_env!r} is not a valid Cursor "
            'subagent model (only "inherit" or a real model ID is honored; '
            f"resolved to default {resolved_eval_model} instead, silently "
            f"using a different model than intended). Unset {EVAL_MODEL_ENV} "
            "or set a real model ID — see "
            "https://cursor.com/docs/subagents.md#model-configuration"
        )


def _doctor_check_orphan_wake(hard_fails: list[str]) -> None:
    orphan = read_orphan_wake()
    if orphan is None:
        return
    orphan_pid = orphan.get("pid", "?")
    orphan_reason = str(orphan.get("reason") or "unspecified")
    hard_fails.append(
        f"Orphan wake suspected (pid={orphan_pid}): {orphan_reason}. "
        "Confirm no leftover wake loop, then re-arm or clear the goal."
    )


def _doctor_check_hooks(hard_fails: list[str], warnings: list[str]) -> bool | None:
    """Inspect hook install paths; return marketplace-hooks truth for later checks."""
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
    return market_hooks


def _doctor_check_harness(hard_fails: list[str], warnings: list[str]) -> None:
    try:
        report = harness_cmd_report()
        print(f"  Harness: {report['run_goal']} (exists={report['exists']})")
        print(f"  Wake loop cmd: {report['wake_loop']}")
        if not report["exists"]:
            hard_fails.append(
                f"run_goal.py missing at {report['run_goal']}. "
                "Run the classic installer, enable the Teams marketplace plugin, "
                "or set CURSOR_GOAL_HOME / CURSOR_PLUGIN_ROOT to a tree that "
                "contains skills/cursor-goal/scripts/run_goal.py"
            )
    except ValueError as exc:
        warnings.append(f"Harness path unresolved: {exc}")


# pylint: disable-next=too-many-branches,too-many-statements
def _doctor_check_windows_python(
    hard_fails: list[str],
    warnings: list[str],
    market_hooks: bool | None,
) -> None:
    if os.name != "nt":
        return
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


def _doctor_load_goal(hard_fails: list[str]) -> GoalState | None:
    try:
        return snapshot_goal(raise_corrupt=True)
    except CorruptGoalError as exc:
        hard_fails.append(f"Corrupt goal.json: {exc}")
        return None
    except GoalLockTimeoutError as exc:
        hard_fails.append(f"goal.lock timeout: {exc}")
        return None


# pylint: disable-next=too-many-branches,too-many-statements
def _doctor_check_pursuing(
    hard_fails: list[str],
    warnings: list[str],
    state: GoalState | None,
    wake_info: dict[str, Any],
) -> None:
    if state is not None and state.active and state.status == "pursuing":
        print(f"  Goal: pursuing " f"({redact_secrets(state.condition, max_chars=60)})")
        print(
            f"  Budgets: turns {state.turns_used}/{state.turn_budget}, "
            f"wake {state.wake_ticks}/{state.wake_budget}"
            + (" (advisory while native)" if state.native_continuation else "")
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
        if state.native_continuation:
            print("  Continuation: native CreateGoal/UpdateGoal (wake skipped)")
            print("  Continuation ready: true (native)")
        elif wake_enabled():
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


def _doctor_print_wake_summary(wake_info: dict[str, Any]) -> None:
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


def _doctor_check_stop_artifacts(warnings: list[str], ddir: Path | None) -> None:
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


def _doctor_check_log_env(warnings: list[str], state: GoalState | None) -> None:
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
        return
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


def cmd_doctor(_argv: list[str]) -> int:
    """Health check for install / data dir / wake / shell. Exit 1 on hard fail."""
    hard_fails: list[str] = []
    warnings: list[str] = []

    print("[goal] Doctor")
    _doctor_check_python(hard_fails)
    ddir = _doctor_check_data_dir(hard_fails)
    _doctor_check_eval_model(hard_fails)
    _doctor_check_orphan_wake(hard_fails)
    market_hooks = _doctor_check_hooks(hard_fails, warnings)
    _doctor_check_harness(hard_fails, warnings)
    _doctor_check_windows_python(hard_fails, warnings, market_hooks)
    hard_fails.extend(_stale_baked_python_failures())
    hard_fails.extend(_install_version_failures())
    hard_fails.extend(_legacy_user_skill_failures())
    warnings.extend(_skill_layout_warnings())

    state = _doctor_load_goal(hard_fails)
    wake_info = wake_status_info()
    _doctor_check_pursuing(hard_fails, warnings, state, wake_info)
    _doctor_print_wake_summary(wake_info)
    _doctor_check_stop_artifacts(warnings, ddir)
    _doctor_check_log_env(warnings, state)

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
