#!/usr/bin/env bash
# Unix installer smoke test: install into a temp HOME, verify, uninstall.
# Usage: ./scripts/install-smoke.sh
# Intended for CI (ubuntu) and local verification.

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_ROOT="$(cd "${SCRIPT_DIR}/.." && pwd)"
TMP_HOME="$(mktemp -d "${TMPDIR:-/tmp}/cursor-goal-smoke.XXXXXX")"
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
test -f "$HOME/.cursor/agents/goalKeeper.md"
test -f "$HOME/.cursor/agents/goal-evaluator.md"
test -f "$HOME/.cursor/hooks.json"

HOOK_CMD="$(python3 -c 'import json,sys; print(json.load(open(sys.argv[1],encoding="utf-8"))["hooks"]["stop"][0]["command"])' "$HOME/.cursor/hooks.json")"
echo "[install-smoke] Hook command: $HOOK_CMD"

# First token (possibly quoted) must be an absolute path.
first="$(python3 -c 'import shlex,sys; print(shlex.split(sys.argv[1])[0])' "$HOOK_CMD")"
case "$first" in
  /*) ;;
  *)
    echo "[install-smoke] FAIL: hook interpreter is not absolute: $first" >&2
    exit 1
    ;;
esac

echo "[install-smoke] Running manage status..."
python3 -u "$HOME/.cursor/skills/goal/scripts/run_goal.py" manage status | grep -q "No active goal"

echo "[install-smoke] Uninstalling..."
bash "${REPO_ROOT}/scripts/uninstall-goal.sh" --purge-data
test ! -d "$HOME/.cursor/skills/goal"
test ! -d "$HOME/.cursor-goal"

echo "[install-smoke] OK"
