# Known limitations

Operational limits of cursor-goal for real-world use. See also [troubleshooting](troubleshooting.md) and [SECURITY.md](../SECURITY.md).

`/goal` is an OpenAI Codex feature (`codex-rs/ext/goal`). This package is a Cursor port of that loop, not a Claude Code skill. Differences that matter in practice: Cursor has no Codex-style hidden idle injection or per-token goal budget, so continuation is `stop` / `subagentStop` plus optional wake; completion is gated by a separate auditor + evaluator rather than a same-model self-audit.

## Continuation: documented hooks are primary, wake is a best-effort backup

Cursor documents the [`stop` and `subagentStop` hooks](https://cursor.com/docs/hooks.md) with `followup_message` and `loop_limit: null` as the supported continuation contract. This harness registers **both**:

1. **`stop`** — fires when your turn ends; if the goal is still `pursuing`, returns a `followup_message` that auto-continues you. Cursor can drop stop-hook stdout on Windows and Linux (upstream race: process `exit` vs stream `close`; confirmed still open in Cursor forum reports through mid-2026 — see [cursor-windows-stop-hook-race.md](cursor-windows-stop-hook-race.md)). Drain delays in the stop hook are mitigations only. A `generation_id`-keyed dedupe stamp guards the marketplace's two `stop` entries (Windows `.cmd` + Unix `python3`) from double-charging `turns_used` when both run sequentially for the same turn. When Cursor omits `generation_id`, the stamp falls back to a SHA-256 of `status` + `loop_count` + `hook_event_name` + `conversation_id` so sequential dual hooks still share one key.
2. **`subagentStop`** (`matcher: "goal-evaluator"` and `matcher: "goal-auditor"`) — fires the instant that subagent finishes, independent of the worker's own turn ending. Evaluator finished → `eval parse-result`. Auditor finished → `eval parse-audit`. Neither hook calls `manage done` itself.

In-turn remaining-work audit (`goal-auditor`) then `goal-evaluator` Task evaluation remains the primary driver of *progress* (CLEAR then YES/NO); the two hooks above are what keeps the *turn loop* itself going when the agent would otherwise idle.

**Wake watchdog (`wake arm`/`loop`/`tick`/`disarm`) is a best-effort, undocumented supplement**, not a Cursor-documented mechanism — it relies on `notify_on_output` matching a background Shell's stdout, which is not listed in Cursor's hook/tooling docs and is subject to the platform reaping idle background shells during long sessions. `manage create`/`resume` still prints `GOAL_WAKE_REQUIRED` and arming still gates `pursuing` status by default; starting the loop and confirming `wake status` `continuation_ready=true` remains recommended for the closest thing to a race-immune path, but it is no longer a hard requirement for evaluation.

Wake emit lines are `AGENT_GOAL_WAKE FOLLOWUP_REQUIRED pursuing spawn_goal-auditor {json}` so Cursor's **Matched text** includes `FOLLOWUP_REQUIRED` (the JSON `prompt` is often never shown). `notify_on_output` must match `^AGENT_GOAL_WAKE FOLLOWUP_REQUIRED pursuing spawn_goal-auditor`. Existing loops from 4.2.x need a new create/resume handshake to attach the longer pattern. Cursor may wrap the notification as “if no follow-ups needed” — while status is `pursuing` that wrapper is wrong; a follow-up is required.

- `eval validate` / `eval prompt` / `eval spawn-config` print a loud warning (not a refusal) when pursuing without a verified-alive wake loop, and continue relying on the `stop`/`subagentStop` hooks alone. Set `CURSOR_GOAL_REQUIRE_WAKE=1` to restore the old hard-refusal behavior, or `CURSOR_GOAL_ALLOW_DEAD_WAKE=1` to silence the warning too. Disable wake entirely with `CURSOR_GOAL_WAKE=0`.
- `manage doctor` / `manage status` are unchanged: they still **hard-fail** while `pursuing` when wake is enabled and `continuation_ready=false`, since those commands are explicit health checks rather than steps in the per-turn continuation path.

For fully unattended runs where you do not want to depend on any in-IDE background Shell at all, prefer a [Cursor Automation](https://cursor.com/docs/automations.md) or the Cursor CLI/SDK's headless agent loop to drive turns from outside the IDE — both are officially documented long-running mechanisms, unlike the wake watchdog's `notify_on_output` polling.

Manual `wake tick` **coalesces** (skips emit) when a recent *wake*-sourced nudge falls inside one interval. The background `wake loop` emits on its own cadence and does not use that coalesce window. Stop/subagentStop followup stamps do **not** suppress wake — so a dropped stop stdout cannot delay the race-immune path for a full interval.

If wake arm fails during create/resume, the harness leaves the goal **`paused`** (exit 1) rather than pursuing without an armed wake.

Tokenless / plain-int `wake.pid` files (pre-3.0): cleared without kill. If the PID is still alive the harness writes `wake.orphan` and `manage doctor` hard-fails until you confirm no leftover loop and re-arm.

## Blocked is the honest stop; pause is user-owned

There is no model-initiated pause. `manage pause` is for `/goal pause` from the user. A repeated impasse (missing secret, permission denied, waiting on the user) is `manage blocked "<reason>"`. The **same** normalized reason on **3 consecutive** pursuing turns (distinct `turns_used`/`wake_ticks` keys) sets `status=blocked`, disarms wake, and stop/wake emit no continuation. Resume from blocked starts a fresh streak. Never mark blocked because the work is hard.

Mid-goal condition edits use `manage update` (same `created_at`, CLEAR+YES invalidated). Agents should not `create --force` a weaker condition.

## Condition text is untrusted data

Followups and eval/audit prompts wrap the stored condition in `<untrusted_condition>` after secret redaction and HTML-escaping. Protocol (CLEAR+YES, remaining-work auditor, fidelity) outranks anything written in the condition. Heuristic redaction is still incomplete — do not put production secrets in conditions.

## Shell validation defaults to denied

New goals use `shell_ok=false`. `--test` commands that need shell metacharacters require `--allow-shell` at create time (create **refuses** otherwise). Prefer argv-safe commands, or set `CURSOR_GOAL_DENY_SHELL=1` as a hard global refuse. Only schema v1 `goal.json` is supported — clear or recreate incompatible state files. `parse` extracts `--allow-shell` / `--deny-shell` / `--workdir` / `--wake-budget` / `--force` into JSON so agents can forward them to `manage create`.

## Secret redaction is heuristic

Logs, status, and persisted validation output redact likely secrets incompletely by design. Do not put production secrets in goal conditions or validation commands.

## IDE only

Harness unit tests cover the Python package. **Cursor CLI E2E is not tested.** Support claim is Cursor IDE 1.7+ with the classic or marketplace install paths.

## Single-user only

No multi-tenant / shared-host isolation. Anyone who can write `~/.cursor-goal/data` can cause validation commands to run as you.

## Eval YES is not attestation

`eval signal` / content-hash binding is a protocol guard. `manage done --force` and `eval signal --force` bypass it (logged recovery escapes).

When a `validation_command` is set but has never been run, `eval prompt` tells the checker it **MUST answer NO** (missing evidence). That is a prompt instruction, not a harness refusal — the worker is still expected to run `eval validate` this turn before spawning the evaluator.

## Remaining-work audit is in the loop; Plan Mode UI is not

`/goal` spawns a readonly `goal-auditor` subagent (empty context, original condition, no work summary) as the unattended equivalent of a fresh plan-mode chat. For a production-audit-style condition the auditor must actually explore (tree, schema vs runtime, CI/installers, fail-open). For a “tests pass” condition it stays narrow. `manage done` requires that CLEAR signal plus evaluator YES (unless `--force`), and rejects if the working tree changed after CLEAR (spawn a new auditor). `eval validate` clears both CLEAR and YES so a later pass cannot reuse a stale audit. The auditor is scoped to the original condition: it must not invent extra polish for a “tests pass” goal.

`/goal` still does **not** invoke the Cursor Plan Mode UI, `ce-plan`, `/review`, `/review-bugbot`, `/review-security`, or thermo-nuclear review. Those wait on the user (which stalls unattended continuation) or add a second checker that can refuse a goal whose condition is already met.

## Marketplace vs classic Windows install

Marketplace `stop_hook.cmd` / `wake_loop.cmd` may fall back to PATH discovery, but **`manage doctor` requires an absolute `CURSOR_GOAL_PYTHON` (Python 3.12+)** for Windows marketplace installs — PATH-only is not treated as success. Classic `install-goal.ps1` bakes an absolute interpreter — preferred for individuals on Windows. Classic and marketplace/template launchers execute quote-stripped `%CGP%` (not the raw env var), reject unsafe cmd metacharacters in `CURSOR_GOAL_PYTHON`, and doctor rejects the same. Classic install also hardens the data-dir ACL at install time and writes helper scripts under the install tree's `scripts\.tmp` (not shared `%TEMP%`). Residual risk: `echo %CGP%| findstr` can expand nested `%VAR%` inside a crafted path — keep `CURSOR_GOAL_PYTHON` free of percent signs.

Teams marketplace installs are **standalone**: resolve the harness with `manage harness-cmd` or `$CURSOR_PLUGIN_ROOT/skills/goal/scripts/run_goal.py`. Do not stack classic `~/.cursor/hooks.json` entries with marketplace plugin hooks — `manage doctor` **FAIL**s when both look configured. See [teams-agpl.md](teams-agpl.md).

`${CURSOR_PLUGIN_ROOT}` (used inside the marketplace `hooks.json` commands) is **not** one of Cursor's documented hook environment variables — it is set by the plugin host at hook-invocation time, on a best-effort basis. `stop_hook.py`'s own path resolution does not depend on it: it locates the vendored `cursor_goal` package relative to its own file location first (`scripts/`, the parent skill directory, or a source checkout's `src/`), so the hook still works if `CURSOR_PLUGIN_ROOT` is ever unset. The classic `~/.cursor/skills/goal` install path never references `CURSOR_PLUGIN_ROOT` at all.

The marketplace `hooks.json` registers **two** unconditional entries per event (`stop` and `subagentStop`): a Windows `cmd /c ...stop_hook.cmd` and a Unix `python3 -u ...stop_hook.py`. `subagentStop` is registered twice per launcher (`matcher: goal-evaluator` and `matcher: goal-auditor`), so the marketplace tree has four `subagentStop` rows. Exactly one launcher is expected to fail per platform (missing `cmd` on Unix, missing/renamed `python3` on Windows) — this is expected Hooks UI noise, not a broken install. A singleflight lock plus a `generation_id`-keyed dedupe stamp ensure only one hook instance mutates goal state and emits a `followup_message` per turn even when both entries happen to run.

## Name collision

An unrelated npm package is also named `cursor-goal`. This project is the Python/AGPL harness at [tboy1337/cursor-goal](https://github.com/tboy1337/cursor-goal).

## License (Teams / redistribution)

cursor-goal is **AGPL-3.0-only**. Teams marketplace import and any network-facing modification/redistribution must comply with AGPL (including source offer obligations). Review [teams-agpl.md](teams-agpl.md) and [COPYING](../COPYING) before enterprise redistribution.
