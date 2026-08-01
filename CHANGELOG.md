# Changelog

All notable changes to this project are documented in this file.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.1.0/),
and this project adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

## [2.1.0] - 2026-08-01

### Added

- Schema v3 goal state with independent `wake_budget` / `wake_ticks` alongside turn budgets
- `manage doctor` hard-fail / warning exits for install and environment checks
- Installer post-install doctor hints and Python 3.12+ gates in wake loop launchers
- Windows ACL harden via `icacls` (`/inheritance:r` then user grant); skip with `CURSOR_GOAL_SKIP_ACL`

### Changed

- Wake Windows process ownership markers require `cursor_goal` / `cursor-goal` / `run_goal.py` (no bare `wake`)
- Shell validation remains allowed by default; `CURSOR_GOAL_DENY_SHELL` / `--deny-shell` force argv-only
- Docs and install pins updated for the 2.1.0 release line

### Security

- Fail-open continue counter under `goal.lock`
- Stronger data-directory permission guidance and Windows ACL best-effort hardening

## [2.0.0] - 2026-08-01

### Added

- Dual stop-hook registration (`stop_hook.cmd` on Windows, `stop_hook.py` on Unix) with singleflight lock
- Wake watchdog (`wake loop` / `AGENT_GOAL_WAKE`) as a safety net when stop-hook stdout is dropped
- Version sync requires `CHANGELOG.md` section matching the package version

### Changed

- Stricter validation for command inputs and goal state management
- CI coverage and platform smoke checks expanded

## [1.4.0] - 2026-08-01

### Added

- Wake watchdog feature and related install / platform-compatibility notes

### Changed

- Goal management and stop-hook documentation refinements for Windows users

## [1.3.0] - 2026-07-31

### Added

- Version synchronization checks across package, docs pins, and plugin manifests
- Stricter validation for command inputs and goal state management

### Changed

- Installation instructions and CI workflows (Windows / macOS) improved

## [1.1.1] - 2026-07-31

### Added

- Initial tagged GitHub Release line for cursor-goal
