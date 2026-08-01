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

If another user or process can write your goal data directory, they can cause commands to run as you. Protect the directory (`0700` on Unix; Windows best-effort `icacls` grant for the current user). Windows ACL harden grants the current user full control and does **not** strip inheritance (stripping can brick access if a later grant fails).

## Hardening controls

- Prefer argv-safe `--test` commands; compound shell snippets use `shell=True` (`COMSPEC`/cmd on Windows).
- Set `CURSOR_GOAL_DENY_SHELL=1` to refuse shell-mode validation.
- Create/validate refuse a group/world-writable data directory on Unix.
- Exclusive `goal.lock` times out after ~10s (Unix and Windows).
- Corrupt `goal.json` is quarantined to `goal.json.corrupt.<UTC>`.
- Field length limits enforced on load and update (`MAX_FIELD_CHARS`).
- `manage done --force` and `eval signal --force` are recovery escapes and are logged; they are not attestation.
- Secret-ish tokens in validation commands are redacted in logs/status/prompts (heuristic; incomplete by design). `CURSOR_GOAL_LOG_SECRETS=1` may log full commands at DEBUG.
- `last-stop-response.json` stores a redacted payload with private file mode when the OS allows.

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
