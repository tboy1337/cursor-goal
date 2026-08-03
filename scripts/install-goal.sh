#!/usr/bin/env bash
# install-goal.sh — Install /goal Python harness for Cursor (Unix/macOS/WSL/Git Bash)
#
# Usage (from a full clone or a GitHub source archive for a tagged release):
#   ./scripts/install-goal.sh
#   ./scripts/uninstall-goal.sh
#
# Do NOT pipe a lone curl of this file into bash — the installer needs the repo tree.
# On native Windows Cursor, use install-goal.ps1 instead (this script refuses Git Bash).

set -euo pipefail

RED='\033[0;31m'
GREEN='\033[0;32m'
YELLOW='\033[0;33m'
BLUE='\033[0;34m'
NC='\033[0m'

INSTALL_DIR="${HOME}/.cursor/skills/goal"
AGENTS_DIR="${HOME}/.cursor/agents"
DATA_DIR="${HOME}/.cursor-goal/data"
CURSOR_HOOKS_FILE="${HOME}/.cursor/hooks.json"
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_ROOT="$(cd "${SCRIPT_DIR}/.." && pwd)"
SKILL_BACKUP=""

log_info()  { echo -e "${GREEN}[install-goal]${NC} $1"; }
log_warn()  { echo -e "${YELLOW}[install-goal]${NC} $1"; }
log_error() { echo -e "${RED}[install-goal]${NC} $1" >&2; }
log_step()  { echo -e "${BLUE}==>${NC} $1"; }

# Native Windows Cursor needs install-goal.ps1 (.cmd launcher). Refuse Git Bash /
# MSYS / Cygwin, and WSL installs that write into a Windows USERPROFILE path.
refuse_non_native_windows_install() {
  local uname_s
  uname_s="$(uname -s 2>/dev/null || true)"
  case "$uname_s" in
    MINGW*|MSYS*|CYGWIN*)
      log_error "Git Bash / MSYS / Cygwin detected ($uname_s)."
      log_error "Native Windows Cursor requires install-goal.ps1 (writes stop_hook.cmd)."
      log_error "Run from PowerShell:"
      log_error "  powershell -NoProfile -ExecutionPolicy Bypass -File .\\scripts\\install-goal.ps1"
      exit 1
      ;;
  esac
  if [ -n "${WSL_DISTRO_NAME:-}" ] || grep -qi microsoft /proc/version 2>/dev/null; then
    case "${HOME}" in
      /mnt/[a-zA-Z]/*)
        log_error "WSL install targeting a Windows-mounted home ($HOME) is not supported."
        log_error "Use install-goal.ps1 from native Windows PowerShell for Windows Cursor."
        exit 1
        ;;
    esac
  fi
}

# Resolve a Python 3.12+ interpreter to an absolute sys.executable path.
detect_python() {
  local cand abs
  for cand in python3 python; do
    if command -v "$cand" >/dev/null 2>&1; then
      if "$cand" -c 'import sys; raise SystemExit(0 if sys.version_info >= (3, 12) else 1)' 2>/dev/null; then
        abs="$("$cand" -c 'import sys; print(sys.executable)')"
        if [ -n "$abs" ] && [ -x "$abs" ]; then
          echo "$abs"
          return 0
        fi
      fi
    fi
  done
  return 1
}

shell_quote() {
  "$PYTHON_BIN" -c 'import shlex,sys; print(shlex.quote(sys.argv[1]))' "$1"
}

check_dependencies() {
  log_step "Checking dependencies..."
  if ! PYTHON_BIN="$(detect_python)"; then
    log_error "Python 3.12+ is required (python3 or python)."
    log_error "Install Python from https://www.python.org/downloads/ then re-run."
    exit 1
  fi
  local py_ver
  py_ver="$("$PYTHON_BIN" -c 'import sys; print("%d.%d" % sys.version_info[:2])')"
  log_info "Using interpreter: $PYTHON_BIN ($py_ver)"
  export PYTHON_BIN
}

install_skill_files() {
  log_step "Installing skill files..."
  local SOURCE_SKILL="${REPO_ROOT}/.cursor/skills/goal"
  local SOURCE_PKG="${REPO_ROOT}/src/cursor_goal"
  local SOURCE_AGENT="${REPO_ROOT}/.cursor/agents/goalKeeper.md"
  local SOURCE_EVALUATOR="${REPO_ROOT}/.cursor/agents/goal-evaluator.md"

  if [ ! -d "$SOURCE_PKG" ]; then
    log_error "Package not found: $SOURCE_PKG"
    log_error "Run this script from a full cursor-goal clone or release tarball (not a lone curl)."
    exit 1
  fi
  if [ ! -f "${SOURCE_SKILL}/SKILL.md" ]; then
    log_error "SKILL.md not found under $SOURCE_SKILL"
    exit 1
  fi
  if [ ! -f "$SOURCE_AGENT" ]; then
    log_error "Required agent not found: $SOURCE_AGENT"
    exit 1
  fi
  if [ ! -f "$SOURCE_EVALUATOR" ]; then
    log_error "Required agent not found: $SOURCE_EVALUATOR"
    exit 1
  fi

  mkdir -p "$INSTALL_DIR/scripts" "$AGENTS_DIR" "$DATA_DIR"
  if ! chmod 700 "$DATA_DIR"; then
    log_error "Failed to chmod 700 on data dir: $DATA_DIR"
    exit 1
  fi

  if [ -d "$INSTALL_DIR" ] && [ -f "${INSTALL_DIR}/SKILL.md" ]; then
    SKILL_BACKUP="${INSTALL_DIR}.bak.$(date -u +%Y%m%dT%H%M%SZ)"
    rm -rf "$SKILL_BACKUP"
    cp -R "$INSTALL_DIR" "$SKILL_BACKUP"
    log_info "Backed up previous skill install to $SKILL_BACKUP"
  fi

  rm -rf "${INSTALL_DIR}/cursor_goal"
  cp -R "$SOURCE_PKG" "${INSTALL_DIR}/cursor_goal"
  find "${INSTALL_DIR}/cursor_goal" -type d -name '__pycache__' -prune -exec rm -rf {} + 2>/dev/null || true
  cp "${SOURCE_SKILL}/SKILL.md" "${INSTALL_DIR}/SKILL.md"
  cp "${SOURCE_SKILL}/scripts/stop_hook.py" "${INSTALL_DIR}/scripts/stop_hook.py"
  cp "${SOURCE_SKILL}/scripts/run_goal.py" "${INSTALL_DIR}/scripts/run_goal.py"
  if [ -f "${SOURCE_SKILL}/scripts/wake_loop.sh" ]; then
    cp "${SOURCE_SKILL}/scripts/wake_loop.sh" "${INSTALL_DIR}/scripts/wake_loop.sh"
    chmod +x "${INSTALL_DIR}/scripts/wake_loop.sh" || true
  fi
  # Do not copy wake_loop.cmd onto Unix installs (Windows-only launcher).
  rm -f "${INSTALL_DIR}/scripts/wake_loop.cmd"

  "$PYTHON_BIN" - "$INSTALL_DIR" <<'PY'
import sys
from pathlib import Path
install = Path(sys.argv[1])
sys.path.insert(0, str(install))
from cursor_goal import __version__
(install / "VERSION").write_text(__version__ + "\n", encoding="utf-8")
print(__version__)
PY

  # Remove legacy bash harness if present
  rm -f "${INSTALL_DIR}/goal-manage.sh" \
        "${INSTALL_DIR}/goal-stop.sh" \
        "${INSTALL_DIR}/goal-eval.sh" \
        "${INSTALL_DIR}/goal-parse.sh"

  # Backup existing agents before overwrite (same pattern as skill backup).
  local TS
  TS="$(date -u +%Y%m%dT%H%M%SZ)"
  if [ -f "${AGENTS_DIR}/goalKeeper.md" ]; then
    cp "${AGENTS_DIR}/goalKeeper.md" "${AGENTS_DIR}/goalKeeper.md.bak.${TS}"
    log_info "Backed up existing goalKeeper.md"
  fi
  if [ -f "${AGENTS_DIR}/goal-evaluator.md" ]; then
    cp "${AGENTS_DIR}/goal-evaluator.md" "${AGENTS_DIR}/goal-evaluator.md.bak.${TS}"
    log_info "Backed up existing goal-evaluator.md"
  fi

  cp "$SOURCE_AGENT" "${AGENTS_DIR}/goalKeeper.md"
  log_info "Installed: ${AGENTS_DIR}/goalKeeper.md"
  cp "$SOURCE_EVALUATOR" "${AGENTS_DIR}/goal-evaluator.md"
  log_info "Installed: ${AGENTS_DIR}/goal-evaluator.md"

  log_info "Installed package + scripts under $INSTALL_DIR"
}

hook_command() {
  local stop_script="${INSTALL_DIR}/scripts/stop_hook.py"
  echo "$(shell_quote "$PYTHON_BIN") -u $(shell_quote "$stop_script")"
}

configure_stop_hook() {
  log_step "Configuring Cursor stop hook..."
  mkdir -p "${HOME}/.cursor"

  local CMD
  CMD="$(hook_command)"
  local HOOKS_BACKUP=""

  if [ -f "$CURSOR_HOOKS_FILE" ]; then
    HOOKS_BACKUP="${CURSOR_HOOKS_FILE}.bak.$(date -u +%Y%m%dT%H%M%SZ)"
    cp "$CURSOR_HOOKS_FILE" "$HOOKS_BACKUP"
    log_info "Backed up existing hooks.json to $HOOKS_BACKUP"
  fi

  if ! "$PYTHON_BIN" - "$INSTALL_DIR" "$CURSOR_HOOKS_FILE" "$CMD" <<'PY'
import sys
from pathlib import Path

sys.path.insert(0, sys.argv[1])
from cursor_goal.hooks_config import merge_hooks_at_path

merge_hooks_at_path(Path(sys.argv[2]), sys.argv[3])
print("merged")
PY
  then
    log_error "Failed to merge stop hook into hooks.json."
    if [ -n "$HOOKS_BACKUP" ] && [ -f "$HOOKS_BACKUP" ]; then
      log_warn "Restoring hooks.json from $HOOKS_BACKUP"
      cp "$HOOKS_BACKUP" "$CURSOR_HOOKS_FILE"
      log_info "Restored previous hooks.json."
    fi
    if [ -n "$SKILL_BACKUP" ] && [ -d "$SKILL_BACKUP" ]; then
      log_warn "Restoring skill files from $SKILL_BACKUP"
      rm -rf "$INSTALL_DIR"
      mv "$SKILL_BACKUP" "$INSTALL_DIR"
      log_info "Restored previous skill install."
    else
      log_error "Skill files were installed under $INSTALL_DIR (no prior backup to restore)."
    fi
    exit 1
  fi
  log_info "Merged/upgraded stop hook in hooks.json"
}

print_summary() {
  echo ""
  echo "Running manage doctor..."
  if ! "$PYTHON_BIN" -u "$INSTALL_DIR/scripts/run_goal.py" manage doctor; then
    echo ""
    echo -e "${RED}manage doctor FAILED — install files were written, but the harness is not healthy.${NC}"
    echo "Fix the FAIL lines above, then re-run doctor or uninstall and retry:"
    echo "  $(shell_quote "$PYTHON_BIN") -u $(shell_quote "$INSTALL_DIR/scripts/run_goal.py") manage doctor"
    echo "  $(shell_quote "$SCRIPT_DIR/uninstall-goal.sh")"
    exit 1
  fi
  echo ""
  echo -e "${GREEN}============================================${NC}"
  echo -e "${GREEN} /goal Autonomous Loop - Installed!         ${NC}"
  echo -e "${GREEN}============================================${NC}"
  echo ""
  echo "Components:"
  echo "  goalKeeper.md       $AGENTS_DIR/goalKeeper.md"
  echo "  goal-evaluator.md   $AGENTS_DIR/goal-evaluator.md"
  echo "  skill               $INSTALL_DIR"
  echo "  stop hook        $(hook_command)"
  echo "  hooks.json       $CURSOR_HOOKS_FILE"
  echo "  Data dir         $DATA_DIR"
  echo ""
  echo "Verify:"
  echo "  $(shell_quote "$PYTHON_BIN") -u $(shell_quote "$INSTALL_DIR/scripts/run_goal.py") manage doctor"
  echo "  $(shell_quote "$PYTHON_BIN") -u $(shell_quote "$INSTALL_DIR/scripts/run_goal.py") manage status"
  echo ""
  echo "Next steps:"
  echo "  1) In Cursor: /goal <verifiable condition>"
  echo "  2) Start wake loop with notify_on_output matching ^AGENT_GOAL_WAKE"
  echo "  3) Confirm wake status shows pid_alive=true before other work"
  echo "  4) If Hooks UI shows {} but last-stop-response.json has followup_message, rely on wake"
  echo ""
  echo "Usage in Cursor agent:"
  echo "  /goal all tests pass and lint is clean"
  echo "  /goal status | pause | resume | clear"
  echo ""
  echo "Note: Prefer in-turn evaluation; the stop hook is a safety net."
  echo "On Windows, use install-goal.ps1 (stop_hook.cmd + drain delay)."
  echo "Shell validation is off by default; pass --allow-shell only when needed."
  echo "Shared machine tip: keep default deny-shell, or set CURSOR_GOAL_DENY_SHELL=1."
  echo ""
}

main() {
  echo ""
  echo -e "${BLUE}================================${NC}"
  echo -e "${BLUE} Installing /goal skill         ${NC}"
  echo -e "${BLUE} Python harness for Cursor      ${NC}"
  echo -e "${BLUE}================================${NC}"
  echo ""

  refuse_non_native_windows_install
  check_dependencies
  install_skill_files
  configure_stop_hook
  print_summary
}

main "$@"
