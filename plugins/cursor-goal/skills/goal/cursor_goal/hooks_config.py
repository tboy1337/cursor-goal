"""hooks.json merge/remove helpers for installers and tests.

Generalized to support marker-based merge/remove across multiple hook
*events* (``stop`` and ``subagentStop``) so both the classic installers and
the marketplace plugin can register a race-free ``subagentStop`` continuation
point (scoped to the ``goal-evaluator`` subagent) alongside the existing
``stop`` safety net, using the same launcher command for both.
"""

from __future__ import annotations

import json
import logging
import os
import secrets
from pathlib import Path
from typing import Any

HOOK_MARKER = "cursor_goal_stop_hook"
SUBAGENT_STOP_MARKER = "cursor_goal_subagent_stop_hook"
SUBAGENT_STOP_EVENT = "subagentStop"
SUBAGENT_STOP_MATCHER = "goal-evaluator"

logger = logging.getLogger("cursor_goal.hooks_config")


def is_goal_marked_entry(item: object, marker: str) -> bool:
    """Return True if *item* is a hook entry carrying the given marker.

    Only the ``_cursor_goal`` marker is recognized (no command-substring
    match), so user-authored hooks are never mistaken for ours.
    """
    if not isinstance(item, dict):
        return False
    return item.get("_cursor_goal") == marker


def is_goal_stop_hook(item: object) -> bool:
    """Return True if *item* is a marked cursor-goal stop hook entry."""
    return is_goal_marked_entry(item, HOOK_MARKER)


def is_goal_subagent_stop_hook(item: object) -> bool:
    """Return True if *item* is a marked cursor-goal subagentStop hook entry."""
    return is_goal_marked_entry(item, SUBAGENT_STOP_MARKER)


def build_stop_entry(command: str, *, timeout: int = 30) -> dict[str, Any]:
    return {
        "command": command,
        "loop_limit": None,
        "timeout": timeout,
        "_cursor_goal": HOOK_MARKER,
    }


def build_subagent_stop_entry(command: str, *, timeout: int = 30) -> dict[str, Any]:
    """Build a subagentStop entry scoped to the goal-evaluator subagent.

    Uses the same launcher *command* as the stop hook — the payload shape
    (presence of ``subagent_type``) distinguishes the two events at runtime.
    """
    return {
        "command": command,
        "loop_limit": None,
        "timeout": timeout,
        "matcher": SUBAGENT_STOP_MATCHER,
        "_cursor_goal": SUBAGENT_STOP_MARKER,
    }


def normalize_hook_event(value: object, *, label: str) -> list[Any]:
    """Normalize a ``hooks.<event>`` value to a list of dict entries.

    A single object is wrapped as a one-element list. Non-list/non-dict
    values become an empty list. Non-dict list items are skipped.
    """
    if value is None:
        return []
    if isinstance(value, dict):
        logger.info("Normalized %s object to a single-element list", label)
        return [value]
    if not isinstance(value, list):
        logger.warning(
            "%s was %s; replacing with empty list", label, type(value).__name__
        )
        return []
    normalized: list[Any] = []
    for item in value:
        if isinstance(item, dict):
            normalized.append(item)
        else:
            logger.warning(
                "Skipping non-object %s entry of type %s", label, type(item).__name__
            )
    return normalized


def normalize_stop_hooks(stop: object) -> list[Any]:
    """Normalize hooks.stop to a list of dict entries (back-compat alias)."""
    return normalize_hook_event(stop, label="hooks.stop")


def _merge_marked_entry(
    data: dict[str, Any], event: str, marker: str, entry: dict[str, Any]
) -> dict[str, Any]:
    hooks = data.setdefault("hooks", {})
    if not isinstance(hooks, dict):
        hooks = {}
        data["hooks"] = hooks
    items = normalize_hook_event(hooks.get(event), label=f"hooks.{event}")
    items = [item for item in items if not is_goal_marked_entry(item, marker)]
    items.append(entry)
    hooks[event] = items
    data["version"] = data.get("version", 1)
    return data


def _remove_marked_entries(
    data: dict[str, Any], event: str, marker: str
) -> dict[str, Any]:
    hooks = data.get("hooks")
    if not isinstance(hooks, dict):
        return data
    items = normalize_hook_event(hooks.get(event), label=f"hooks.{event}")
    hooks[event] = [item for item in items if not is_goal_marked_entry(item, marker)]
    data["hooks"] = hooks
    return data


def merge_stop_hook(data: dict[str, Any], entry: dict[str, Any]) -> dict[str, Any]:
    return _merge_marked_entry(data, "stop", HOOK_MARKER, entry)


def remove_stop_hooks(data: dict[str, Any]) -> dict[str, Any]:
    return _remove_marked_entries(data, "stop", HOOK_MARKER)


def merge_subagent_stop_hook(
    data: dict[str, Any], entry: dict[str, Any]
) -> dict[str, Any]:
    return _merge_marked_entry(data, SUBAGENT_STOP_EVENT, SUBAGENT_STOP_MARKER, entry)


def remove_subagent_stop_hooks(data: dict[str, Any]) -> dict[str, Any]:
    return _remove_marked_entries(data, SUBAGENT_STOP_EVENT, SUBAGENT_STOP_MARKER)


def write_hooks_file(path: Path, data: dict[str, Any]) -> None:
    """Atomically write hooks.json as UTF-8 without BOM."""
    path.parent.mkdir(parents=True, exist_ok=True)
    payload = json.dumps(data, indent=2, ensure_ascii=False) + "\n"
    tmp = path.with_name(f"{path.name}.{os.getpid()}.{secrets.token_hex(4)}.tmp")
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


def read_hooks_file(path: Path) -> dict[str, Any]:
    # utf-8-sig tolerates BOM from older Windows PowerShell Set-Content writes.
    raw: Any = json.loads(path.read_text(encoding="utf-8-sig"))
    if not isinstance(raw, dict):
        raise ValueError(f"hooks.json root must be an object: {path}")
    return dict(raw)


def merge_hooks_at_path(
    hooks_path: Path,
    command: str,
    *,
    subagent_stop_command: str | None = None,
) -> None:
    """Merge goal hooks into hooks.json (installer entry point).

    Always merges the ``stop`` hook. When *subagent_stop_command* is given,
    also merges a ``subagentStop`` hook scoped to the goal-evaluator subagent
    (typically the same command, since one script handles both event shapes).
    """
    if hooks_path.is_file():
        data = read_hooks_file(hooks_path)
    else:
        data = {"version": 1, "hooks": {"stop": []}}
    data = merge_stop_hook(data, build_stop_entry(command))
    if subagent_stop_command:
        data = merge_subagent_stop_hook(
            data, build_subagent_stop_entry(subagent_stop_command)
        )
    write_hooks_file(hooks_path, data)


def remove_hooks_at_path(hooks_path: Path) -> None:
    """Remove goal stop + subagentStop hooks from hooks.json (uninstaller)."""
    if not hooks_path.is_file():
        return
    data = read_hooks_file(hooks_path)
    data = remove_stop_hooks(data)
    data = remove_subagent_stop_hooks(data)
    write_hooks_file(hooks_path, data)
