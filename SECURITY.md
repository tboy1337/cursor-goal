# Security Policy

## Supported versions

Security fixes are applied on the latest tagged release of [cursor-goal](https://github.com/tboy1337/cursor-goal) (`main` / current semver).

## Threat model

cursor-goal is a **single-user, trusted-local** Cursor IDE harness.

| Asset | Trust assumption |
|-------|------------------|
| `~/.cursor-goal/data/` (`goal.json`, eval signal, wake files) | Equivalent to shell trust for the interactive user |
| `validation_command` | May be executed by `eval validate` / agent Shell |
| Stop hook | Fail-open; never blocks Cursor on corrupt state; singleflight across dual marketplace entries |
| Eval YES signal | Protocol guard bound to goal content hash — **not** cryptographic attestation |

If another user or process can write your goal data directory, they can cause commands to run as you. Protect the directory (`0700` on Unix; Windows best-effort `icacls /inheritance:r` then grant current user full control). If inheritance strip succeeds but the grant fails, cursor-goal logs a loud error — verify you can still access the data directory.

## Hardening controls

- Prefer argv-safe `--test` commands; compound shell snippets use `shell=True` (`COMSPEC`/cmd on Windows).
- Set `CURSOR_GOAL_DENY_SHELL=1` or create with `--deny-shell` (`shell_ok=false`) to refuse shell-mode validation.
- Create/validate/stop/wake refuse insecure data directories: on Unix, symlink / wrong owner / group/world-writable (stop fails open to `{}`; wake arm/loop/tick refuse). On Windows, symlink / junction / reparse point (same refuse behavior).
- On Windows, create/validate/**stop/wake** refuse when ACL harden was attempted and failed (doctor also hard-fails). If inheritance was stripped and the grant fails, inheritance is restored (`icacls /inheritance:e`) best-effort before recording the failure. Skip with `CURSOR_GOAL_SKIP_ACL=1` after manually locking down the path.
- Exclusive `goal.lock` times out after ~10s (Unix and Windows). Fail-open stop continues are capped and counted against the turn budget.
- Corrupt `goal.json` is quarantined to `goal.json.corrupt.<UTC>`.
- Field length limits enforced on load (truncate) and update (reject) for condition, validation command, reasons, outputs, verdicts, and timestamps (`MAX_FIELD_CHARS`).
- Turn budget and wake budget are independent counters (schema v3).
- `manage done --force` and `eval signal --force` are recovery escapes and are logged; they are not attestation.
- Secret-ish tokens in validation commands **and validation output** / status reasons are redacted in logs/status/prompts/persisted state (heuristic; incomplete by design). Validation subprocesses receive a scrubbed environment (PATH/home/locale/`VIRTUAL_ENV`/`CURSOR_GOAL_*` — not ambient API keys, and not `PYTHONPATH`/`PYTHONHOME`). `CURSOR_GOAL_LOG_SECRETS=1` may log full commands at DEBUG.
- `last-stop-response.json` stores a redacted payload (condition text after `Goal:` / `toward:` stripped) with private file mode when the OS allows. Optional durable logs: `CURSOR_GOAL_LOG_FILE=1` (or a path); when set without `CURSOR_GOAL_LOG`, log level defaults to INFO.
- Wake kill requires an ownership token and verifies process cmdline (Windows CIM / Unix `/proc` or `ps`) before signaling; legacy tokenless `wake.pid` is cleared without kill.
- When `CURSOR_GOAL_PYTHON` is set (marketplace Windows launchers), it must be an absolute path to Python 3.12+.
- `manage doctor` reports insecure dirs, Windows ACL harden failures, PATH vs absolute Python, hook presence, wake health (with exact wake-loop command), fail-open continue counter, and shell mode.

## Reporting a vulnerability

Please report via [GitHub Security Advisories for tboy1337/cursor-goal](https://github.com/tboy1337/cursor-goal/security/advisories/new).

Include:

1. Affected version / commit
2. Reproduction steps
3. Impact (e.g. unexpected command execution, data disclosure)

Do not file public issues for unfixed vulnerabilities.

## Out of scope

- Multi-tenant / shared-host isolation
- Cursor IDE / Task / stop-hook platform bugs (report upstream when possible)
- Compromised Cursor session model or agent that already has Shell access
