#!/usr/bin/env bash
# Unix installer smoke test: install into a temp HOME, verify, uninstall.
# Usage: ./scripts/install-smoke.sh
# Intended for CI (ubuntu) and local verification.

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_ROOT="$(cd "${SCRIPT_DIR}/.." && pwd)"
# Prefer a real (non-symlink) base for HOME so doctor symlink/reparse gates pass.
# macOS CI uses /var/folders → /private/var/folders; mktemp under TMPDIR follows that.
_SMOKE_BASE="${CURSOR_GOAL_SMOKE_BASE:-}"
if [ -z "${_SMOKE_BASE}" ]; then
  if [ -d /private/tmp ] && [ ! -L /private/tmp ]; then
    _SMOKE_BASE="/private/tmp"
  elif [ -d /tmp ] && [ ! -L /tmp ]; then
    _SMOKE_BASE="/tmp"
  else
    _SMOKE_BASE="${TMPDIR:-/tmp}"
    # Resolve one level of symlink ancestors when possible.
    if command -v realpath >/dev/null 2>&1; then
      _SMOKE_BASE="$(realpath "${_SMOKE_BASE}" 2>/dev/null || echo "${_SMOKE_BASE}")"
    fi
  fi
fi
TMP_HOME="$(mktemp -d "${_SMOKE_BASE}/cursor-goal-smoke.XXXXXX")"
# Canonicalize HOME so data-dir paths do not retain symlink prefixes.
if command -v realpath >/dev/null 2>&1; then
  TMP_HOME="$(realpath "${TMP_HOME}")"
fi
cleanup() { rm -rf "$TMP_HOME"; }
trap cleanup EXIT

export HOME="$TMP_HOME"
export USERPROFILE="$TMP_HOME"

echo "[install-smoke] HOME=$HOME"
echo "[install-smoke] Installing..."
bash "${REPO_ROOT}/scripts/install-goal.sh"

test -f "$HOME/.cursor/skills/goal/cursor_goal/__init__.py"
test -f "$HOME/.cursor/skills/goal/scripts/run_goal.py"
test -f "$HOME/.cursor/skills/goal/scripts/stop_hook.py"
test -f "$HOME/.cursor/skills/goal/scripts/wake_loop.sh"
test ! -f "$HOME/.cursor/skills/goal/scripts/wake_loop.cmd"
test -f "$HOME/.cursor/skills/goal/VERSION"
test -f "$HOME/.cursor/agents/goalKeeper.md"
test -f "$HOME/.cursor/agents/goal-evaluator.md"
test -f "$HOME/.cursor/hooks.json"

grep -q "goalKeeper\|Autonomous\|goal" "$HOME/.cursor/agents/goalKeeper.md"
grep -q "goal-evaluator\|evaluator\|readonly" "$HOME/.cursor/agents/goal-evaluator.md"

# Use the absolute interpreter baked into hooks.json (same as installer-selected).
HOOK_CMD="$(python3 -c 'import json,sys; print(json.load(open(sys.argv[1],encoding="utf-8"))["hooks"]["stop"][0]["command"])' "$HOME/.cursor/hooks.json" 2>/dev/null || true)"
if [ -z "$HOOK_CMD" ]; then
  # Fallback when python3 is missing but installer used `python`
  HOOK_CMD="$(python -c 'import json,sys; print(json.load(open(sys.argv[1],encoding="utf-8"))["hooks"]["stop"][0]["command"])' "$HOME/.cursor/hooks.json")"
fi
echo "[install-smoke] Hook command: $HOOK_CMD"

# Resolve smoke Python from the first argv token of the hook command.
SMOKE_PY="$(python3 -c 'import shlex,sys; print(shlex.split(sys.argv[1])[0])' "$HOOK_CMD" 2>/dev/null || python -c 'import shlex,sys; print(shlex.split(sys.argv[1])[0])' "$HOOK_CMD")"
case "$SMOKE_PY" in
  /*) ;;
  *)
    echo "[install-smoke] FAIL: hook interpreter is not absolute: $SMOKE_PY" >&2
    exit 1
    ;;
esac
echo "[install-smoke] Using installer interpreter: $SMOKE_PY"

"$SMOKE_PY" - "$HOME/.cursor/hooks.json" <<'PY'
import json, sys
from pathlib import Path
data = json.loads(Path(sys.argv[1]).read_text(encoding="utf-8"))
stop = data["hooks"]["stop"]
assert isinstance(stop, list) and stop, "stop hooks missing"
entry = stop[0]
assert entry.get("_cursor_goal") == "cursor_goal_stop_hook", entry
assert entry.get("loop_limit") is None, entry
assert "-u" in entry.get("command", ""), entry
print("[install-smoke] Hook marker/loop_limit/-u OK")
PY

VERSION_FILE="$HOME/.cursor/skills/goal/VERSION"
VERSION="$(tr -d '[:space:]' < "$VERSION_FILE")"
test -n "$VERSION"
echo "[install-smoke] VERSION=$VERSION"

RUN_GOAL="$HOME/.cursor/skills/goal/scripts/run_goal.py"
echo "[install-smoke] Running manage status..."
"$SMOKE_PY" -u "$RUN_GOAL" manage status | grep -q "No active goal"

echo "[install-smoke] Running manage doctor..."
"$SMOKE_PY" -u "$RUN_GOAL" manage doctor

echo "[install-smoke] Running eval spawn-config..."
SPAWN="$("$SMOKE_PY" -u "$RUN_GOAL" eval spawn-config)"
echo "[install-smoke] spawn-config: $SPAWN"
"$SMOKE_PY" -c 'import json,sys; d=json.loads(sys.argv[1]); assert d.get("subagent_type")=="goal-evaluator"; assert d.get("readonly") is True; assert "model" in d' "$SPAWN"

echo "[install-smoke] Uninstalling..."
bash "${REPO_ROOT}/scripts/uninstall-goal.sh" --purge-data
test ! -d "$HOME/.cursor/skills/goal"
test ! -d "$HOME/.cursor-goal"

echo "[install-smoke] OK"
