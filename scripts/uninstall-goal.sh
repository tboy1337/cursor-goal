#!/usr/bin/env bash
# uninstall-goal.sh — Remove /goal install artifacts from Cursor user config

set -euo pipefail

INSTALL_DIR="${HOME}/.cursor/skills/goal"
AGENTS_DIR="${HOME}/.cursor/agents"
DATA_DIR="${HOME}/.cursor-goal/data"
CURSOR_HOOKS_FILE="${HOME}/.cursor/hooks.json"
REMOVE_DATA="${1:-}"

detect_python() {
  local cand abs
  for cand in python3 python; do
    if command -v "$cand" >/dev/null 2>&1; then
      if "$cand" -c 'import sys; raise SystemExit(0 if sys.version_info >= (3, 12) else 1)' >/dev/null 2>&1; then
        abs="$("$cand" -c 'import sys; print(sys.executable)' 2>/dev/null || true)"
        if [ -n "$abs" ]; then
          echo "$abs"
          return 0
        fi
      fi
    fi
  done
  return 1
}

if [ -f "$CURSOR_HOOKS_FILE" ]; then
  if PY="$(detect_python)"; then
    if [ -d "${INSTALL_DIR}/cursor_goal" ]; then
      "$PY" - "$INSTALL_DIR" "$CURSOR_HOOKS_FILE" <<'PY'
import sys
from pathlib import Path

sys.path.insert(0, sys.argv[1])
from cursor_goal.hooks_config import remove_hooks_at_path

remove_hooks_at_path(Path(sys.argv[2]))
print("hooks cleaned")
PY
    else
      "$PY" - "$CURSOR_HOOKS_FILE" <<'PY'
import json
import os
import secrets
import sys
from pathlib import Path

path = Path(sys.argv[1])
data = json.loads(path.read_text(encoding="utf-8"))
hooks = data.get("hooks") or {}
stop = hooks.get("stop") or []

def is_goal_hook(item: object) -> bool:
    if not isinstance(item, dict):
        return False
    cmd = str(item.get("command", ""))
    return (
        item.get("_cursor_goal") == "cursor_goal_stop_hook"
        or "goal-stop.sh" in cmd
        or "stop_hook.py" in cmd
        or "stop_hook.cmd" in cmd
        or "cursor_goal stop" in cmd
        or "cursor-goal stop" in cmd
    )

hooks["stop"] = [item for item in stop if not is_goal_hook(item)]
data["hooks"] = hooks
tmp = path.with_name(f"{path.name}.{os.getpid()}.{secrets.token_hex(4)}.tmp")
tmp.write_text(json.dumps(data, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
tmp.replace(path)
print("hooks cleaned")
PY
    fi
    echo "[uninstall-goal] Removed stop hook entries from hooks.json"
  else
    echo "[uninstall-goal] Warning: Python not found; remove stop hook from $CURSOR_HOOKS_FILE manually"
  fi
fi

echo "[uninstall-goal] Removing skill at $INSTALL_DIR"
rm -rf "$INSTALL_DIR"

echo "[uninstall-goal] Removing agent definitions"
rm -f "${AGENTS_DIR}/goalKeeper.md" "${AGENTS_DIR}/goal-evaluator.md"

if [ "$REMOVE_DATA" = "--purge-data" ]; then
  rm -rf "${HOME}/.cursor-goal"
  echo "[uninstall-goal] Purged $HOME/.cursor-goal"
else
  echo "[uninstall-goal] Left data at $DATA_DIR (pass --purge-data to remove)"
fi

echo "[uninstall-goal] Done."
