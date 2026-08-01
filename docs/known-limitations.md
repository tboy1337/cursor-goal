# Known limitations

Operational limits of cursor-goal for real-world use. See also [troubleshooting](troubleshooting.md) and [SECURITY.md](../SECURITY.md).

## Continuation depends on more than the stop hook

Cursor can drop stop-hook stdout (`followup_message`) on Windows and Linux (upstream race: process `exit` vs stream `close`). See [cursor-windows-stop-hook-race.md](cursor-windows-stop-hook-race.md).

**Durable continuation requires:**

1. In-turn `goal-evaluator` Task evaluation (primary)
2. Wake watchdog armed with Shell `notify_on_output` matching `^AGENT_GOAL_WAKE` (tertiary, race-immune)
3. Stop `followup_message` (secondary, best-effort)

If wake is not started while a goal is `pursuing`, goals can stall when Hooks show `{}`.

## Shell validation is allowed by default

`--test` commands may run with `shell=True` when argv-splitting fails. This matches the **single-user trusted-local** threat model. Prefer argv-safe commands, or set `CURSOR_GOAL_DENY_SHELL=1` / `--deny-shell`.

## Secret redaction is heuristic

Logs, status, and persisted validation output redact likely secrets incompletely by design. Do not put production secrets in goal conditions or validation commands.

## IDE only

Harness unit tests cover the Python package. **Cursor CLI E2E is not tested.** Support claim is Cursor IDE 1.7+ with the classic or marketplace install paths.

## Single-user only

No multi-tenant / shared-host isolation. Anyone who can write `~/.cursor-goal/data` can cause validation commands to run as you.

## Eval YES is not attestation

`eval signal` / content-hash binding is a protocol guard. `manage done --force` and `eval signal --force` bypass it (logged recovery escapes).

## Marketplace vs classic Windows install

Marketplace `stop_hook.cmd` / `wake_loop.cmd` resolve Python via `CURSOR_GOAL_PYTHON` (must be absolute when set) or PATH. Classic `install-goal.ps1` bakes an absolute interpreter — preferred for individuals on Windows.

Teams marketplace installs are **standalone**: resolve the harness with `manage harness-cmd` or `$CURSOR_PLUGIN_ROOT/skills/goal/scripts/run_goal.py`. Do not stack classic `~/.cursor/hooks.json` entries with marketplace plugin hooks.

## Name collision

An unrelated npm package is also named `cursor-goal`. This project is the Python/AGPL harness at [tboy1337/cursor-goal](https://github.com/tboy1337/cursor-goal).

## License (Teams / redistribution)

cursor-goal is **AGPL-3.0-only**. Teams marketplace import and any network-facing modification/redistribution must comply with AGPL (including source offer obligations). Review [COPYING](../COPYING) before enterprise redistribution.
