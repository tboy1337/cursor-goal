# Changelog

All notable changes to this project are documented in this file.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.1.0/),
and this project adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

## [2.4.0] - 2026-08-01

First public 2.x GitHub Release (closes the gap where docs advertised 2.x while Latest was still v1.1.1).

### Added

- Wake watchdog (`wake arm` / `loop` / `tick` / `disarm`) as a race-immune continuation path when Cursor drops stop-hook `followup_message`
- Schema v3: independent turn budget and wake budget; `shell_ok` / `--deny-shell`
- `manage doctor` health checks (data dir, hooks, wake, shell mode, ACL)
- Marketplace dual stop hooks (Windows `.cmd` + Unix `python3`) with singleflight
- Docs: [known limitations](docs/known-limitations.md), [troubleshooting](docs/troubleshooting.md)

### Security

- Windows: refuse symlink / junction / reparse-point data directories; create/validate/**stop/wake** refuse when ACL harden failed
- Cap `last_reason` / validation output / eval verdict / `created_at` at `MAX_FIELD_CHARS` (truncate on load)
- Redact secrets in `manage status` reasons and `eval parse-result` display (heuristic JWT / Basic auth too)
- Marketplace `.cmd`: require absolute `CURSOR_GOAL_PYTHON` when set; warn on PATH fallback
- Wake kill refuses missing ownership tokens (legacy plain-int `wake.pid`)
- Fail-open stop continues account against turn budget; still capped at 3
- Installers back up existing agent markdown before overwrite
- Hooks uninstall prefers `_cursor_goal` marker when present (avoids collateral removal)

### Changed

- Classic Windows install remains preferred for absolute-Python bake into launchers

## [2.3.0] - 2026-07

- Wake loop management and validation/logging hardening
- Documentation and install clarity updates

## [2.2.0] - 2026-07

- Version sync / install instruction updates

## [2.1.0] - 2026-07

- Schema v3 wake budgets, doctor improvements, Python 3.12+ launcher checks

## [2.0.0] - 2026-07

- Dual marketplace stop hooks, wake watchdog, security and CI hardening
- Breaking: schema and continuation model relative to 1.x

## [1.1.1] - 2026-07-31

- Last 1.x tagged GitHub Release before the 2.x line
