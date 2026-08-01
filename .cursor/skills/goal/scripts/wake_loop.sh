#!/usr/bin/env bash
# Goal wake watchdog launcher (optional convenience wrapper).
# Prefer: python3 -u "$(dirname "$0")/run_goal.py" wake loop
set -euo pipefail
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
RUN_GOAL="${SCRIPT_DIR}/run_goal.py"
export PYTHONUNBUFFERED=1

if [[ -n "${CURSOR_GOAL_PYTHON:-}" ]]; then
  exec "${CURSOR_GOAL_PYTHON}" -u "${RUN_GOAL}" wake loop "$@"
fi
if command -v python3 >/dev/null 2>&1; then
  exec python3 -u "${RUN_GOAL}" wake loop "$@"
fi
if command -v python >/dev/null 2>&1; then
  exec python -u "${RUN_GOAL}" wake loop "$@"
fi
echo "[cursor-goal] No Python found for wake_loop.sh" >&2
exit 1
