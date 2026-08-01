"""hooks.json merge/remove helpers for installers and tests."""

from __future__ import annotations

import json
import logging
import os
import secrets
from pathlib import Path
from typing import Any

HOOK_MARKER = "cursor_goal_stop_hook"

logger = logging.getLogger("cursor_goal.hooks_config")

_LEGACY_NEEDLES = (
    "goal-stop.sh",
    "stop_hook.py",
    "stop_hook.cmd",
    "cursor_goal stop",
    "cursor-goal stop",
)


def is_goal_stop_hook(item: object, *, allow_legacy: bool = True) -> bool:
    """Return True if *item* is a cursor-goal stop hook entry.

    Prefer the ``_cursor_goal`` marker. Substring matching is a legacy
    uninstall/upgrade fallback and is logged when used.
    """
    if not isinstance(item, dict):
        return False
    if item.get("_cursor_goal") == HOOK_MARKER:
        return True
    if not allow_legacy:
        return False
    cmd = str(item.get("command", ""))
    if any(needle in cmd for needle in _LEGACY_NEEDLES):
        logger.info(
            "Matched legacy goal stop hook by command substring: %r",
            cmd[:120],
        )
        return True
    return False


def build_stop_entry(command: str, *, timeout: int = 30) -> dict[str, Any]:
    return {
        "command": command,
        "loop_limit": None,
        "timeout": timeout,
        "_cursor_goal": HOOK_MARKER,
    }


def normalize_stop_hooks(stop: object) -> list[Any]:
    """Normalize hooks.stop to a list of dict entries.

    A single object is wrapped as a one-element list. Non-list/non-dict values
    become an empty list. Non-dict list items are skipped.
    """
    if stop is None:
        return []
    if isinstance(stop, dict):
        logger.info("Normalized hooks.stop object to a single-element list")
        return [stop]
    if not isinstance(stop, list):
        logger.warning(
            "hooks.stop was %s; replacing with empty list",
            type(stop).__name__,
        )
        return []
    normalized: list[Any] = []
    for item in stop:
        if isinstance(item, dict):
            normalized.append(item)
        else:
            logger.warning(
                "Skipping non-object hooks.stop entry of type %s",
                type(item).__name__,
            )
    return normalized


def merge_stop_hook(data: dict[str, Any], entry: dict[str, Any]) -> dict[str, Any]:
    hooks = data.setdefault("hooks", {})
    if not isinstance(hooks, dict):
        hooks = {}
        data["hooks"] = hooks
    stop = normalize_stop_hooks(hooks.get("stop"))
    stop = [item for item in stop if not is_goal_stop_hook(item)]
    stop.append(entry)
    hooks["stop"] = stop
    data["version"] = data.get("version", 1)
    return data


def remove_stop_hooks(data: dict[str, Any]) -> dict[str, Any]:
    hooks = data.get("hooks")
    if not isinstance(hooks, dict):
        return data
    stop = normalize_stop_hooks(hooks.get("stop"))
    # Prefer marker-only removal when at least one marked entry exists so
    # unrelated hooks whose command happens to contain stop_hook.* are kept.
    has_marked = any(
        isinstance(item, dict) and item.get("_cursor_goal") == HOOK_MARKER
        for item in stop
    )
    hooks["stop"] = [
        item
        for item in stop
        if not is_goal_stop_hook(item, allow_legacy=not has_marked)
    ]
    data["hooks"] = hooks
    return data


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


def merge_hooks_at_path(hooks_path: Path, command: str) -> None:
    """Merge a goal stop hook into hooks.json (installer entry point)."""
    entry = build_stop_entry(command)
    if hooks_path.is_file():
        data = read_hooks_file(hooks_path)
    else:
        data = {"version": 1, "hooks": {"stop": []}}
    write_hooks_file(hooks_path, merge_stop_hook(data, entry))


def remove_hooks_at_path(hooks_path: Path) -> None:
    """Remove goal stop hooks from hooks.json (uninstaller entry point)."""
    if not hooks_path.is_file():
        return
    write_hooks_file(hooks_path, remove_stop_hooks(read_hooks_file(hooks_path)))
