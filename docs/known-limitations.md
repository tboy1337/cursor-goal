# Known limitations

Operational limits of cursor-goal for real-world use. See also [troubleshooting](troubleshooting.md) and [SECURITY.md](../SECURITY.md).

## Continuation depends on more than the stop hook

Cursor can drop stop-hook stdout (`followup_message`) on Windows and Linux (upstream race: process `exit` vs stream `close`). Confirmed still open in Cursor forum reports through mid-2026. See [cursor-windows-stop-hook-race.md](cursor-windows-stop-hook-race.md). Drain delays in the stop hook are mitigations only.

**Durable continuation requires (operational prerequisite while pursuing):**

1. In-turn `goal-evaluator` Task evaluation (primary)
2. Wake watchdog: `manage create`/`resume` prints `GOAL_WAKE_REQUIRED`; agent starts that `command` in a background Shell with `notify_on_output` matching `pattern` (`^AGENT_GOAL_WAKE`), then confirms `wake status` `continuation_ready=true` (race-immune)
3. Stop `followup_message` (secondary, best-effort)

If wake is not started while a goal is `pursuing`, goals can stall when Hooks show `{}`. `eval validate` refuses while pursuing without a live wake loop unless `CURSOR_GOAL_ALLOW_DEAD_WAKE=1` (or wake is disabled via `CURSOR_GOAL_WAKE=0`). Doctor skips wake liveness hard-fails when wake is disabled.

Manual `wake tick` **coalesces** (skips emit) when a recent *wake*-sourced nudge falls inside one interval. The background `wake loop` emits on its own cadence and does not use that coalesce window. Stop-hook followup stamps do **not** suppress wake — so a dropped stop stdout cannot delay the race-immune path for a full interval.

If wake arm fails during create/resume, the harness leaves the goal **`paused`** (exit 1) rather than pursuing without an armed wake.

## Shell validation defaults to denied

New goals use `shell_ok=false`. `--test` commands that need shell metacharacters require `--allow-shell` (or a global allow — not recommended). Prefer argv-safe commands, or set `CURSOR_GOAL_DENY_SHELL=1` as a hard global refuse. Only schema v1 `goal.json` is supported — clear or recreate incompatible state files.

## Secret redaction is heuristic

Logs, status, and persisted validation output redact likely secrets incompletely by design. Do not put production secrets in goal conditions or validation commands.

## IDE only

Harness unit tests cover the Python package. **Cursor CLI E2E is not tested.** Support claim is Cursor IDE 1.7+ with the classic or marketplace install paths.

## Single-user only

No multi-tenant / shared-host isolation. Anyone who can write `~/.cursor-goal/data` can cause validation commands to run as you.

## Eval YES is not attestation

`eval signal` / content-hash binding is a protocol guard. `manage done --force` and `eval signal --force` bypass it (logged recovery escapes).

## Marketplace vs classic Windows install

Marketplace `stop_hook.cmd` / `wake_loop.cmd` may fall back to PATH discovery, but **`manage doctor` requires an absolute `CURSOR_GOAL_PYTHON` (Python 3.12+)** for Windows marketplace installs — PATH-only is not treated as success. Classic `install-goal.ps1` bakes an absolute interpreter — preferred for individuals on Windows.

Teams marketplace installs are **standalone**: resolve the harness with `manage harness-cmd` or `$CURSOR_PLUGIN_ROOT/skills/goal/scripts/run_goal.py`. Do not stack classic `~/.cursor/hooks.json` entries with marketplace plugin hooks — `manage doctor` **FAIL**s when both look configured. See [teams-agpl.md](teams-agpl.md).

## Name collision

An unrelated npm package is also named `cursor-goal`. This project is the Python/AGPL harness at [tboy1337/cursor-goal](https://github.com/tboy1337/cursor-goal).

## License (Teams / redistribution)

cursor-goal is **AGPL-3.0-only**. Teams marketplace import and any network-facing modification/redistribution must comply with AGPL (including source offer obligations). Review [teams-agpl.md](teams-agpl.md) and [COPYING](../COPYING) before enterprise redistribution.
