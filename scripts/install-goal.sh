#!/usr/bin/env bash
# install-goal.sh — Install /cursor-goal Python harness for Cursor (Unix/macOS/WSL/Git Bash)
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

INSTALL_DIR="${HOME}/.cursor/skills/cursor-goal"
AGENTS_DIR="${HOME}/.cursor/agents"
DATA_DIR="${HOME}/.cursor-goal/data"
CURSOR_HOOKS_FILE="${HOME}/.cursor/hooks.json"
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_ROOT="$(cd "${SCRIPT_DIR}/.." && pwd)"
BACKUP_MANIFEST=""

log_info()  { echo -e "${GREEN}[install-goal]${NC} $1"; }
log_warn()  { echo -e "${YELLOW}[install-goal]${NC} $1"; }
log_error() { echo -e "${RED}[install-goal]${NC} $1" >&2; }
log_step()  { echo -e "${BLUE}==>${NC} $1"; }

INSTALL_HOLD_SECONDS=10

hold_before_exit() {
  log_info "Waiting ${INSTALL_HOLD_SECONDS} seconds before exit so this window stays readable."
  sleep "${INSTALL_HOLD_SECONDS}"
}

trap hold_before_exit EXIT

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

run_install_backup() {
  "$PYTHON_BIN" - "$REPO_ROOT/src" "$HOME" "$@" <<'PY'
import sys
sys.path.insert(0, sys.argv[1])
from cursor_goal.install_backup import main
raise SystemExit(main(["--home", sys.argv[2], *sys.argv[3:]]))
PY
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
  local SOURCE_SKILL="${REPO_ROOT}/.cursor/skills/cursor-goal"
  local SOURCE_PKG="${REPO_ROOT}/src/cursor_goal"
  local SOURCE_AGENT="${REPO_ROOT}/.cursor/agents/goalKeeper.md"
  local SOURCE_EVALUATOR="${REPO_ROOT}/.cursor/agents/goal-evaluator.md"
  local SOURCE_AUDITOR="${REPO_ROOT}/.cursor/agents/goal-auditor.md"

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
  if [ ! -f "$SOURCE_AUDITOR" ]; then
    log_error "Required agent not found: $SOURCE_AUDITOR"
    exit 1
  fi

  mkdir -p "$INSTALL_DIR/scripts" "$AGENTS_DIR" "$DATA_DIR"
  if ! chmod 700 "$DATA_DIR"; then
    log_error "Failed to chmod 700 on data dir: $DATA_DIR"
    exit 1
  fi

  mkdir -p "${HOME}/.cursor-goal/backups"
  BACKUP_MANIFEST="${HOME}/.cursor-goal/backups/.last-install-manifest.json"
  if ! run_install_backup backup-before --manifest "$BACKUP_MANIFEST" >/dev/null; then
    log_error "Failed to snapshot previous skill/agents/hooks for rollback."
    exit 1
  fi
  log_info "Wrote install backup manifest $BACKUP_MANIFEST"

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
  # Do not copy Windows-only launchers onto Unix installs.
  rm -f "${INSTALL_DIR}/scripts/wake_loop.cmd" \
        "${INSTALL_DIR}/scripts/stop_hook.cmd"

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

  cp "$SOURCE_AGENT" "${AGENTS_DIR}/goalKeeper.md"
  log_info "Installed: ${AGENTS_DIR}/goalKeeper.md"
  cp "$SOURCE_EVALUATOR" "${AGENTS_DIR}/goal-evaluator.md"
  log_info "Installed: ${AGENTS_DIR}/goal-evaluator.md"
  cp "$SOURCE_AUDITOR" "${AGENTS_DIR}/goal-auditor.md"
  log_info "Installed: ${AGENTS_DIR}/goal-auditor.md"

  log_info "Installed package + scripts under $INSTALL_DIR"
}

hook_command() {
  local stop_script="${INSTALL_DIR}/scripts/stop_hook.py"
  echo "$(shell_quote "$PYTHON_BIN") -u $(shell_quote "$stop_script")"
}

configure_stop_hook() {
  log_step "Configuring Cursor stop/subagentStop hooks..."
  mkdir -p "${HOME}/.cursor"

  local CMD
  CMD="$(hook_command)"

  if ! "$PYTHON_BIN" - "$INSTALL_DIR" "$CURSOR_HOOKS_FILE" "$CMD" <<'PY'
import sys
from pathlib import Path

sys.path.insert(0, sys.argv[1])
from cursor_goal.hooks_config import merge_hooks_at_path

# Same launcher command for both events: cmd_stop() dispatches on payload
# shape (subagentStop payloads carry "subagent_type"), scoped further by the
# installed hooks.json "matcher": "goal-evaluator" / "goal-auditor".
merge_hooks_at_path(Path(sys.argv[2]), sys.argv[3], subagent_stop_command=sys.argv[3])
print("merged")
PY
  then
    log_error "Failed to merge stop/subagentStop hooks into hooks.json."
    if [ -n "$BACKUP_MANIFEST" ] && [ -f "$BACKUP_MANIFEST" ]; then
      log_warn "Rolling back skill/agents/hooks from $BACKUP_MANIFEST"
      run_install_backup restore --manifest "$BACKUP_MANIFEST" || true
    else
      log_error "Skill files were installed under $INSTALL_DIR (no backup manifest to restore)."
    fi
    exit 1
  fi
  log_info "Merged/upgraded stop + subagentStop hooks in hooks.json"
  if ! run_install_backup prune-after; then
    log_warn "Post-install backup prune failed (install itself succeeded)."
  fi
}

print_summary() {
  echo ""
  echo "Running manage doctor..."
  if ! "$PYTHON_BIN" -u "$INSTALL_DIR/scripts/run_goal.py" manage doctor; then
    echo ""
    echo -e "${RED}manage doctor FAILED — install files were written, but the harness is not healthy.${NC}"
    echo "Doctor failure does not roll back (unlike a hook-merge failure). Fix the FAIL lines above, then re-run doctor or uninstall and retry:"
    echo "  $(shell_quote "$PYTHON_BIN") -u $(shell_quote "$INSTALL_DIR/scripts/run_goal.py") manage doctor"
    echo "  $(shell_quote "$SCRIPT_DIR/uninstall-goal.sh")"
    if [ -n "${BACKUP_MANIFEST:-}" ] && [ -f "$BACKUP_MANIFEST" ]; then
      echo "Previous snapshot (manual restore, not automatic): $BACKUP_MANIFEST"
      echo "  PYTHONPATH=$(shell_quote "$INSTALL_DIR") $(shell_quote "$PYTHON_BIN") -m cursor_goal.install_backup --home $(shell_quote "$HOME") restore --manifest $(shell_quote "$BACKUP_MANIFEST")"
    fi
    exit 1
  fi
  echo ""
  echo -e "${GREEN}============================================${NC}"
  echo -e "${GREEN} /cursor-goal harness - Installed!           ${NC}"
  echo -e "${GREEN}============================================${NC}"
  echo ""
  echo "Components:"
  echo "  goalKeeper.md       $AGENTS_DIR/goalKeeper.md"
  echo "  goal-evaluator.md   $AGENTS_DIR/goal-evaluator.md"
  echo "  goal-auditor.md     $AGENTS_DIR/goal-auditor.md"
  echo "  skill               $INSTALL_DIR"
  echo "  stop hook        $(hook_command)"
  echo "  hooks.json       $CURSOR_HOOKS_FILE"
  echo "  Data dir         $DATA_DIR"
  echo ""
  echo "Next steps:"
  echo "  1) Restart Cursor (or reload hooks) so hooks.json takes effect"
  echo "  2) In Cursor: /cursor-goal <verifiable condition>"
  echo ""
  echo "Usage in Cursor agent:"
  echo "  /cursor-goal all tests pass and lint is clean"
  echo "  /cursor-goal status | pause | resume | clear"
  echo ""
  echo "Note: Prefer in-turn evaluation; the stop hook is a safety net."
  echo "If create prints GOAL_WAKE_REQUIRED (native CreateGoal missing or CURSOR_GOAL_NATIVE=0),"
  echo "  start that wake loop with notify_on_output matching ^AGENT_GOAL_WAKE FOLLOWUP_REQUIRED pursuing spawn_goal-auditor,"
  echo "  then confirm wake status shows continuation_ready=true."
  echo "On Windows, use install-goal.ps1 (stop_hook.cmd + drain delay)."
  echo "Shell validation is off by default; pass --allow-shell only when needed."
  echo "Shared machine tip: keep default deny-shell, or set CURSOR_GOAL_DENY_SHELL=1."
  echo ""
}

main() {
  echo ""
  echo -e "${BLUE}================================${NC}"
  echo -e "${BLUE} Installing /cursor-goal skill  ${NC}"
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
