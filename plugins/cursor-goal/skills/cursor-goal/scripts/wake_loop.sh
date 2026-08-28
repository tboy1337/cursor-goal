#!/usr/bin/env bash
# Goal wake watchdog launcher (optional convenience wrapper).
# Prefer: python3 -u "$(dirname "$0")/run_goal.py" wake loop
# Requires Python 3.12+.
set -euo pipefail
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
RUN_GOAL="${SCRIPT_DIR}/run_goal.py"
export PYTHONUNBUFFERED=1

_py_ok() {
  "$1" -c 'import sys; raise SystemExit(0 if sys.version_info >= (3, 12) else 1)' >/dev/null 2>&1
}

if [[ -n "${CURSOR_GOAL_PYTHON:-}" ]]; then
  if [[ "${CURSOR_GOAL_PYTHON}" != /* ]]; then
    echo "[cursor-goal] CURSOR_GOAL_PYTHON must be an absolute path" >&2
    exit 1
  fi
  if _py_ok "${CURSOR_GOAL_PYTHON}"; then
    exec "${CURSOR_GOAL_PYTHON}" -u "${RUN_GOAL}" wake loop "$@"
  fi
  echo "[cursor-goal] CURSOR_GOAL_PYTHON is not Python 3.12+" >&2
  exit 1
fi
if command -v python3 >/dev/null 2>&1 && _py_ok python3; then
  exec python3 -u "${RUN_GOAL}" wake loop "$@"
fi
if command -v python >/dev/null 2>&1 && _py_ok python; then
  exec python -u "${RUN_GOAL}" wake loop "$@"
fi
echo "[cursor-goal] Python 3.12+ not found for wake_loop.sh" >&2
exit 1
