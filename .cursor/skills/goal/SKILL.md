---
name: goal
description: Autonomous goal loop. Use when the user types /goal followed by a completion condition, or asks to pursue a goal until tests/lint/build pass. Keeps working across turns until the condition is met via a separate evaluator model and documented stop/subagentStop hooks.
---

# /goal — Autonomous Goal Loop

Set a persistent objective. Work toward it across turns until it is met.

## Harness (Python)

Resolve the runner **before** lifecycle commands (classic install or Teams marketplace).

**Resolution order:**

1. **Preferred:** `manage harness-cmd` once (via any known `run_goal.py` path below) and reuse its printed invocation / Wake loop lines for the rest of the session.
2. Classic: `~/.cursor/skills/goal/scripts/run_goal.py` (Windows: `$env:USERPROFILE\.cursor\skills\goal\scripts\run_goal.py`).
3. Marketplace: `"$CURSOR_PLUGIN_ROOT/skills/goal/scripts/run_goal.py"` when `CURSOR_PLUGIN_ROOT` is set.
4. Last resort (editable install only): `python -c "from cursor_goal.paths import run_goal_script; print(run_goal_script())"` — editable `pip install -e` does **not** register the skill/hooks.

**Unix / macOS / WSL (classic):**

```bash
python3 -u ~/.cursor/skills/goal/scripts/run_goal.py <command> ...
```

**Windows (PowerShell / Cursor Shell, classic):**

```powershell
py -3 -u "$env:USERPROFILE\.cursor\skills\goal\scripts\run_goal.py" <command> ...
```

**Marketplace — Unix / macOS / WSL** (when `CURSOR_PLUGIN_ROOT` is set):

```bash
python3 -u "$CURSOR_PLUGIN_ROOT/skills/goal/scripts/run_goal.py" <command> ...
```

**Marketplace — Windows PowerShell** (when `$env:CURSOR_PLUGIN_ROOT` is set):

```powershell
# Prefer absolute interpreter: $env:CURSOR_GOAL_PYTHON or py -3
py -3 -u "$env:CURSOR_PLUGIN_ROOT\skills\goal\scripts\run_goal.py" <command> ...
```

| Command | Purpose |
|---------|---------|
| `parse "<input>"` | Parse `/goal` input → JSON |
| `manage create\|status\|doctor\|harness-cmd\|pause\|resume\|done\|clear` | Goal state lifecycle |
| `eval validate\|spawn-config\|prompt\|parse-result\|parse-audit\|audit-prompt\|audit-spawn-config\|signal\|check` | Evaluator / remaining-work auditor harness |
| `stop` | Stop **and** subagentStop hook (stdin JSON → stdout JSON; dispatches on payload shape) |
| `wake arm\|tick\|disarm\|status\|loop` | Wake watchdog (shell notify sentinel) |

State file: `~/.cursor-goal/data/goal.json` (override with `CURSOR_GOAL_DATA`, must be absolute when set).

`validation_command` is trusted-user local state (executed by `eval validate` / agent Shell). Prefer argv-safe `--test` commands. New goals default to `shell_ok=false`; pass `--allow-shell` when the command needs shell metacharacters (`&&`, pipes, redirects). Treat `~/.cursor-goal/data` as shell-equivalent trust. Set `CURSOR_GOAL_DENY_SHELL=1` to refuse shell-mode validation globally.

Do **not** put production secrets in goal conditions — live prompts scrub secret-ish tokens heuristically; disk `last-stop-response.json` also strips condition text after `Goal:` / `toward:` markers.

## Setting a Goal

When the user says `/goal`, parse then act — do **not** evaluate shell strings from the parser output.

**Unix / macOS / WSL:**

```bash
PARSE=$(python3 -u ~/.cursor/skills/goal/scripts/run_goal.py parse "<raw user input after /goal>")
```

**Windows (PowerShell):**

```powershell
$PARSE = py -3 -u "$env:USERPROFILE\.cursor\skills\goal\scripts\run_goal.py" parse "<raw user input after /goal>"
```

`$PARSE` / `PARSE` is JSON, e.g.:

```json
{"subcommand":null,"action":"create","condition":"all tests pass","test_cmd":"npm test","budget":20}
```

Create may also include optional fields extracted from flags (omit when unset):

```json
{"subcommand":null,"action":"create","condition":"compound check","test_cmd":"npm test && npm run lint","budget":20,"allow_shell":true,"wake_budget":50,"workdir":"/tmp/proj","force":true}
```

or a subcommand:

```json
{"subcommand":"status","action":"status","condition":null,"test_cmd":null,"budget":null}
```

Then:

- If `action` is `status|pause|resume|clear` → `manage <action>`
- If `action` is `create` → `manage create "<condition>"` plus flags from parse JSON:
  - `test_cmd` → `--test "<cmd>"`
  - `budget` → `--budget N`
  - `allow_shell: true` → `--allow-shell`; `allow_shell: false` → `--deny-shell`
  - `wake_budget` → `--wake-budget N`
  - `workdir` → `--workdir <path>`
  - `force: true` → `--force` (replace existing goal)
  - If parse lacks `allow_shell` but the raw user text contains `--allow-shell` / `--deny-shell`, forward those flags from the raw input before create.

After **every** `create` or `resume`, complete the **Wake handshake** below **before** other work. **Do not skip.** Create with wake enabled prints `Status: paused (awaiting wake arm)` until arm/activate succeeds, then `Status: pursuing`. If create/resume exits non-zero (wake arm failed → goal paused), fix the error and `manage resume` — do not work as if pursuing. If `wake status` shows `continuation_ready=false` while pursuing, `manage status` / `manage doctor` treat that as blocking (ACTION REQUIRED / FAIL, exit non-zero) — start the loop before relying on those commands' success. Tip: `CURSOR_GOAL_LOG_FILE=1` for durable diagnostics. Then start working toward the condition.

## Command Reference

| Command | Action |
|---------|--------|
| `/goal <condition>` | Set goal and start working |
| `/goal status` | Show current goal state |
| `/goal pause` | Pause auto-continuation |
| `/goal resume` | Resume a paused goal |
| `/goal clear` | Remove goal entirely |

Aliases for clear: `stop`, `off`, `reset`, `cancel`

## Multi-model (maker ≠ checker)

| Role | Model | How |
|------|--------|-----|
| Worker | Session model | This skill / `goalKeeper` (`inherit`) |
| Evaluator | `CURSOR_GOAL_EVAL_MODEL` or default `composer-2.5` | `Task` → `goal-evaluator` |
| Remaining-work auditor | session model (`inherit`) | `Task` → `goal-auditor` |

Always resolve Task params from the harness:

**Unix:**

```bash
SPAWN=$(python3 -u ~/.cursor/skills/goal/scripts/run_goal.py eval spawn-config)
```

**Windows:**

```powershell
$SPAWN = py -3 -u "$env:USERPROFILE\.cursor\skills\goal\scripts\run_goal.py" eval spawn-config
```

Example: `{"subagent_type":"goal-evaluator","model":"composer-2.5","readonly":true}`

Never evaluate with `generalPurpose` or the same Task call as the worker. Never use `generalPurpose` for the remaining-work auditor — spawn `goal-auditor` from `eval audit-spawn-config`. `CURSOR_GOAL_EVAL_MODEL` must be `inherit` or a real Cursor model ID — `fast` is **not** a valid value (it is only a bracket option on a real model, e.g. `composer-2.5[fast=false]`); a legacy `fast` override is rejected and silently falls back to the default. On legacy request-based Cursor plans without Max Mode, Task subagents may still run on a Cursor-selected model regardless of the requested `model` — spawn-config reflects the *requested* model, not a runtime guarantee.

## Working Toward the Goal

While the goal is active (`status: "pursuing"`), follow this playbook automatically. Do **not** ask the user which path to take. Do **not** wait on Plan Mode, `ce-plan`, Bugbot, or a review skill. No extra flags.

**Iron law:** no completion claims, no `goal-evaluator` spawn, and no `manage done` without **fresh** evidence from **this turn**. If a `validation_command` is configured, that evidence is `eval validate` run in this turn. Never say "should pass", "looks done", or "probably fixed". Never claim the goal is complete in chat while `manage status` is `pursuing`.

Do **not** invent a `--test` / `validation_command` the user did not pass. If parse JSON has no `test_cmd`, create the goal without `--test`. Never recreate the goal with a shorter proxy command because `eval validate` timed out — raise `block_until_ms` on a Shell run of the real command, or rely on the harness timeout (`CURSOR_GOAL_VALIDATE_TIMEOUT_SEC`, default 600s).

While `status` is `pursuing`, repeat:

1. **Do focused work** — make the next concrete change toward the condition.
2. **Verify this turn** — if a validation command is configured, run `eval validate` so output is persisted. If none is configured, gather explicit evidence for the condition (command output, file contents) and say so in `--work-summary`. Do not spawn the auditor or evaluator yet.
3. **If validation failed (non-zero) or behavior is unexpected — debug, do not thrash:**
   1. Investigate root cause from the failure output before proposing a fix (read the failing test/assertion, the implementation it hit, and why that path ran). Do not shotgun-patch or hardcode expected values.
   2. If failures look like compile or typecheck errors: group by file/category, fix the highest-confidence issues first, re-run.
   3. If the tree has conflict markers (`<<<<<<<`): resolve them so validation can run, then go back to step 2.
   4. If **two or more independent** failure domains exist (unrelated packages/suites): spawn parallel `Task` workers (not `goal-evaluator` / `goal-auditor`) for those domains, merge results, then go back to step 2.
   5. After a targeted fix, go back to step 2 (re-validate this turn). Do not audit or evaluate on a failed validation.
4. **If validation passed (exit 0) and `git diff` is non-empty:** once before the first YES attempt, remove AI slop introduced in this work (unnecessary comments, `any` casts, extra defensive try/except, deep nesting that an early return would replace). Keep behavior unchanged. Re-run `eval validate`. Skip this pass on later wake ticks if you already did it for this goal.
5. **Remaining-work audit** — spawn a readonly `goal-auditor` (see below) with the original condition and **no** work summary. This is the unattended equivalent of a new plan-mode chat. Do **not** invoke Cursor Plan Mode or `/ce-plan` (they wait on the user).
6. **Act on audit** — REMAINING → continue at step 1 with that punch list (do not spawn `goal-evaluator` yet). CLEAR → evaluate.
7. **Evaluate** — spawn a readonly evaluator subagent on the configured model (see below). Never evaluate after every single file edit or after read-only exploration.
8. **Act on result** — YES → `manage done` (requires CLEAR + YES). NO → continue at step 1 with the reason.

Do **not** invoke Plan Mode, `/ce-plan`, `/review`, `/review-bugbot`, `/review-security`, or thermo-nuclear review inside this loop. They stall unattended continuation. The remaining-work auditor *is* the plan-mode-quality pass; it is scoped to the original condition (not a second quality bar for a "tests pass" goal).

### Remaining-work audit via Subagent

Before the first YES attempt (and again after more work), spawn the remaining-work auditor. Capture audit spawn-config and prompt (no `--work-summary`):

**Windows (PowerShell):**

```powershell
$AUDIT_SPAWN = py -3 -u "$env:USERPROFILE\.cursor\skills\goal\scripts\run_goal.py" eval audit-spawn-config
$AUDIT_PROMPT = py -3 -u "$env:USERPROFILE\.cursor\skills\goal\scripts\run_goal.py" eval audit-prompt
```

Spawn **Task** with `subagent_type`, `model`, and `readonly` from that JSON (`goal-auditor`, `inherit`, readonly), `prompt` set to the audit prompt text.

Parse with `eval parse-audit --stdin` (same stdin/`@file` rules as parse-result).

**If REMAINING** (exit 1): implement that punch list; do not spawn `goal-evaluator` yet.

**If CLEAR** (exit 0): proceed to evaluation below. `manage done` rejects without this CLEAR signal.

### Evaluation via Subagent

When a validation command is configured, run `eval validate` **this turn** first. If it has not been run, the evaluator prompt instructs the checker to answer NO — do not skip validate and hope.

Then generate the prompt and spawn config (capture into variables with the OS-appropriate Shell syntax above), and spawn **Task** with `subagent_type`, `model`, and `readonly` from the spawn-config JSON, plus `prompt` set to the eval prompt text.

Parse the result. **Prefer `--stdin` (especially on Windows)** so long evaluator text never hits argv length limits:

**Unix:**

```bash
python3 -u ~/.cursor/skills/goal/scripts/run_goal.py eval parse-result --stdin <<'EOF'
<subagent response>
EOF
```

**Windows (PowerShell):**

```powershell
@'
<subagent response>
'@ | py -3 -u "$env:USERPROFILE\.cursor\skills\goal\scripts\run_goal.py" eval parse-result --stdin
```

Alternatively: `eval parse-result @path\to\file.txt` or (short output only) `eval parse-result "YES: …"`. `@file` paths must resolve under the goal data directory unless `--allow-cwd` is also passed, which additionally permits paths under the current working directory.

On YES: automatically records a YES-bound evaluator signal (exit 0). On NO/UNCLEAR: exit 1.

**If YES** (exit 0): run `manage done` (also requires a CLEAR audit signal this cycle).

**If NO** (exit 1): read REASON, continue working, audit again after more progress.

`eval signal` is only for recovery (`eval signal --force` after a YES parse if the signal file was cleared). Prefer `parse-result` auto-signal. `--force` is not cryptographic attestation.

### When to Evaluate

Evaluate after validation results, logical units of work, or changes that could satisfy the condition. Do not evaluate after every single file edit or after read-only exploration.

**While `status` is `pursuing`, do not end the turn idle.** Either mark the goal done (`manage done` after CLEAR + YES) or finish an audit/evaluate cycle (REMAINING or NO) and start the next concrete action in the same turn. Do not stop mid-goal waiting for the stop hook to wake you. Do not tell the user the goal is complete while status is `pursuing`.

## Continuation Hooks (documented, primary safety net)

Two Cursor-documented hooks (`https://cursor.com/docs/hooks.md`), both wired to `scripts/stop_hook.py` (Windows: `scripts/stop_hook.cmd`), keep the turn loop going:

- **`stop`** fires when your turn ends. If the goal is still `pursuing`, it returns a `followup_message` that auto-continues you.
- **`subagentStop`** (`matcher: "goal-evaluator"` and `matcher: "goal-auditor"`) fires the instant that subagent finishes, independent of when your own turn ends. Evaluator: run `eval parse-result`. Auditor: run `eval parse-audit`. It never calls `manage done` itself.

**Do not rely on either hook as the evaluator itself.** In-turn subagent evaluation decides YES/NO; these hooks only keep turns flowing. On Windows the installer uses a `.cmd` launcher plus a stdout drain delay to mitigate Cursor's capture race; if a followup still drops, the wake watchdog below is a best-effort supplement. Every `stop` emit writes `~/.cursor-goal/data/last-stop-response.json` (`ts`, `pid`, `payload`); every `subagentStop` emit writes `~/.cursor-goal/data/last-subagent-stop-response.json`.

When you see a `[GOAL]` prefix, resume working toward the condition immediately.

## Wake Watchdog (best-effort supplement, not a Cursor-documented mechanism)

While a goal is `pursuing` and wake is enabled (the default), arming and starting a live Shell wake loop with `notify_on_output` is **recommended** as a supplement to the two hooks above — it does not depend on Cursor capturing hook stdout (see repo `docs/cursor-windows-stop-hook-race.md`). It is **not** a Cursor-documented API (`notify_on_output` on a background Shell is a Cursor IDE convenience, not a hooks contract), so treat it as best-effort: long-idle background shells can be reaped by the platform. `eval validate` / `eval prompt` / `eval spawn-config` warn (not refuse) when pursuing without a verified-alive loop; set `CURSOR_GOAL_REQUIRE_WAKE=1` to restore the old hard-refusal behavior. `manage create`/`resume` and `manage doctor`/`status` are unchanged: arming still gates `pursuing`, and doctor/status still hard-fail while pursuing with `continuation_ready=false`. Opt out of wake entirely with `CURSOR_GOAL_WAKE=0`. For fully unattended runs outside the IDE turn loop, prefer a Cursor Automation or the Cursor CLI/SDK headless agent loop instead of relying on a monitored background shell.

`manage create` / `resume` arms wake state and prints one machine-readable line agents must consume:

```text
GOAL_WAKE_REQUIRED {"command":"<shell command>","pattern":"^AGENT_GOAL_WAKE","notify_pattern":"^AGENT_GOAL_WAKE","interval_s":15}
```

If arm fails, create/resume **exits 1**, leaves the goal **`paused`**, and does not print `GOAL_WAKE_REQUIRED`.

### Wake handshake (recommended after every create/resume)

Do this before other work when wake is enabled. Until `continuation_ready=true` (implies `pid_alive=true`), `manage status` / `manage doctor` report **ACTION REQUIRED** / **FAIL** and exit non-zero — treat that as blocking for those specific commands. `eval validate` / `eval prompt` / `eval spawn-config` only **warn** (they no longer refuse) while `continuation_ready=false`; set `CURSOR_GOAL_REQUIRE_WAKE=1` if you want them to hard-refuse instead, or `CURSOR_GOAL_ALLOW_DEAD_WAKE=1` to silence the warning.

1. **Parse** the `GOAL_WAKE_REQUIRED` line from create/resume stdout (JSON after the prefix). If missing and wake is enabled, run `wake status` and use its `command` + `notify_pattern` / `pattern` fields (or `manage harness-cmd` → Wake loop line). Prefer the event `command` over hardcoded paths. `pattern` and `notify_pattern` are aliases (same value).
2. **Background Shell tool call** — immediately start that `command` with `notify_on_output` matching the event `pattern` or `notify_pattern` (always `^AGENT_GOAL_WAKE` unless the event says otherwise). This must be a real Shell tool invocation; the harness cannot attach Cursor notifications for you.
3. **Verify:** `wake status` → `continuation_ready=true` (also `armed=true`, `pid_alive=true`). If `heartbeat_stale` is true, restart the loop. `manage status` exits **non-zero** while pursuing with `continuation_ready=false` (ACTION REQUIRED).
4. **On wake:** when Shell notifies on `AGENT_GOAL_WAKE`, read the sentinel JSON `prompt`, run `manage status`, continue if still `pursuing`. An earlier chat message that the goal is complete is invalid while status is `pursuing`.
5. `manage done` / `pause` / `clear` disarms. Disable with `CURSOR_GOAL_WAKE=0` (doctor skips wake liveness when disabled).

Manual `wake tick` coalesces when a recent *wake*-sourced nudge falls inside one interval (avoids double ticks). The background `wake loop` emits on its own cadence and does not apply that coalesce window. Stop-hook stamps never suppress wake.

Interval: `CURSOR_GOAL_WAKE_INTERVAL_S` (default 15, min 5, max 600), or pass `--interval N` directly to `wake arm` / `wake loop` (same clamp; the flag takes priority over the env var for that invocation). Each wake emission increments `wake_ticks` against **`wake_budget`** (independent of turn budget). Default `wake_budget = turn_budget * 10` (min 10, max 500). Override with `--wake-budget N`.

## Turn Budget

Default turn budget is 20 (max 500). Customize with `--budget N` or natural language (`stop after 10 turns`). Exhausted when `turns_used >= turn_budget` **or** `wake_ticks >= wake_budget` → `budget-limited` (check `last_reason` / status for which limit hit).

## Failure modes

| Symptom | Likely cause | Action |
|---------|--------------|--------|
| Goal ends quickly with low `turns_used` | `wake_budget` exhausted | Raise `--wake-budget` or interval |
| Hooks UI `{}` but `last-stop-response.json` has followup | Cursor stdout race | Rely on wake loop (stop stamps do not suppress wake) |
| Wake armed but no continuation | Loop not started / notify not attached | Start `command` from `GOAL_WAKE_REQUIRED` with `notify_on_output` |
| Create/resume exit 1, status paused | Wake arm failed | Fix data-dir/ACL, then `manage resume` |
| Double continuation soon after manual `wake tick` | Wake→wake coalesce on `tick` only | Expected; loop cadence is unchanged |
| `manage status` ACTION REQUIRED / exit 1 / doctor FAIL wake | `continuation_ready=false` while pursuing | Blocking: start wake loop, confirm `continuation_ready=true` |
| Create refused (shell metacharacters) | `shell_ok=false` + shell-mode `--test` | Pass `--allow-shell` or use an argv-safe `--test` |
| Doctor FAIL classic + marketplace | Stacked install paths | Uninstall classic hooks **or** disable marketplace plugin |
| Marketplace hooks fail on Windows | Missing absolute `CURSOR_GOAL_PYTHON` | Set absolute env (required for doctor OK) or prefer `install-goal.ps1` |
| Validation refused (shell) | `--deny-shell` or `CURSOR_GOAL_DENY_SHELL` | Use argv-safe `--test` or allow shell |
| Doctor FAIL insecure data dir | World-writable `CURSOR_GOAL_DATA` | `chmod 700` / private path |
| Doctor / harness-cmd FAIL missing run_goal.py | Skill/plugin tree not installed | Run installer or set `CURSOR_GOAL_HOME` / `CURSOR_PLUGIN_ROOT` |

Prefer argv-safe `--test` commands. New goals default to deny-shell (`shell_ok=false`); use `--allow-shell` only when needed. Shell mode is reported in `manage status` / `doctor`. Use `--deny-shell` or `CURSOR_GOAL_DENY_SHELL=1` to force argv-only.

Run `manage doctor` after install or when diagnosing stalls. Installers exit non-zero when doctor hard-fails.
## Writing Good Conditions

```
✓ all tests in test/auth pass and the lint step is clean
✓ every call site of the old API has been migrated and the build succeeds
✗ the code is clean
✗ implement the feature
```

## Status Values

`pursuing`, `paused`, `achieved`, `budget-limited`
