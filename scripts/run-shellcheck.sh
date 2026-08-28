#!/usr/bin/env bash
# Run ShellCheck on all repository bash scripts.
# Usage: ./scripts/run-shellcheck.sh
# Compatible with Bash 3.2+ (macOS system bash).

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_ROOT="$(cd "${SCRIPT_DIR}/.." && pwd)"

if ! command -v shellcheck >/dev/null 2>&1; then
  echo "[shellcheck] shellcheck not found on PATH." >&2
  echo "[shellcheck] Install via: pip install shellcheck-py" >&2
  echo "[shellcheck] Or: https://github.com/koalaman/shellcheck#installing" >&2
  exit 2
fi

SCRIPTS=()
while IFS= read -r line; do
  [ -n "$line" ] && SCRIPTS+=("$line")
done <<EOF
$(find "${REPO_ROOT}/scripts" -type f \( -name '*.sh' -o -name '*.bash' \) | sort)
EOF

# The marketplace/plugin skill tree ships its own bash entry point (the wake
# watchdog loop launcher); keep local runs in parity with CI by scanning it
# too instead of only scripts/.
WAKE_LOOP_SH="${REPO_ROOT}/.cursor/skills/cursor-goal/scripts/wake_loop.sh"
if [ -f "${WAKE_LOOP_SH}" ]; then
  SCRIPTS+=("${WAKE_LOOP_SH}")
fi
PLUGIN_WAKE_LOOP_SH="${REPO_ROOT}/plugins/cursor-goal/skills/cursor-goal/scripts/wake_loop.sh"
if [ -f "${PLUGIN_WAKE_LOOP_SH}" ]; then
  SCRIPTS+=("${PLUGIN_WAKE_LOOP_SH}")
fi

if [ "${#SCRIPTS[@]}" -eq 0 ]; then
  echo "[shellcheck] No bash scripts found under scripts/" >&2
  exit 1
fi

echo "[shellcheck] Checking ${#SCRIPTS[@]} script(s)..."
shellcheck --severity=warning "${SCRIPTS[@]}"
echo "[shellcheck] All scripts passed"
