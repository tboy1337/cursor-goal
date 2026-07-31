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

if [ "${#SCRIPTS[@]}" -eq 0 ]; then
  echo "[shellcheck] No bash scripts found under scripts/" >&2
  exit 1
fi

echo "[shellcheck] Checking ${#SCRIPTS[@]} script(s)..."
shellcheck --severity=warning "${SCRIPTS[@]}"
echo "[shellcheck] All scripts passed"
