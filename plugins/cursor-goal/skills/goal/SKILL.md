---
name: goal
description: Autonomous goal loop. Use when the user types /goal followed by a completion condition, or asks to pursue a goal until tests/lint/build pass. Keeps working across turns until the condition is met via a separate evaluator model and a stop-hook safety net.
---

# /goal — Autonomous Goal Loop

Set a persistent objective. Work toward it across turns until it is met.

## Harness (Python)

Resolve the runner **before** lifecycle commands (classic install or Teams marketplace).

**Resolution order:**

1. Classic: `~/.cursor/skills/goal/scripts/run_goal.py` (Windows: `$env:USERPROFILE\.cursor\skills\goal\scripts\run_goal.py`).
2. Marketplace: `"$CURSOR_PLUGIN_ROOT/skills/goal/scripts/run_goal.py"` when `CURSOR_PLUGIN_ROOT` is set.
3. Optional: `manage harness-cmd` once and reuse its printed `Wake loop` / invocation lines.
4. Last resort (editable install only): `python -c "from cursor_goal.paths import run_goal_script; print(run_goal_script())"` — editable `pip install -e` does **not** register the skill/hooks.

**Unix / macOS / WSL (classic):**

```bash
python3 -u ~/.cursor/skills/goal/scripts/run_goal.py <command> ...
```

**Windows (PowerShell / Cursor Shell, classic):**

```powershell
py -3 -u "$env:USERPROFILE\.cursor\skills\goal\scripts\run_goal.py" <command> ...
```

**Marketplace (when `CURSOR_PLUGIN_ROOT` is set):**

```bash
python3 -u "$CURSOR_PLUGIN_ROOT/skills/goal/scripts/run_goal.py" <command> ...
```

| Command | Purpose |
|---------|---------|
| `parse "<input>"` | Parse `/goal` input → JSON |
| `manage create\|status\|doctor\|harness-cmd\|pause\|resume\|done\|clear` | Goal state lifecycle |
| `eval validate\|spawn-config\|prompt\|parse-result\|signal\|check` | Evaluator harness |
| `stop` | Stop hook (stdin JSON → stdout JSON) |
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

or a subcommand:

```json
{"subcommand":"status","action":"status","condition":null,"test_cmd":null,"budget":null}
```

Then:

- If `action` is `status|pause|resume|clear` → `manage <action>`
- If `action` is `create` → `manage create "<condition>" [--test "<cmd>"] [--budget N] [--workdir <path>] [--allow-shell]`

After **every** `create` or `resume`, complete the **Wake handshake** below **before** other work. **Do not skip.** If create/resume exits non-zero (wake arm failed → goal paused), fix the error and `manage resume` — do not work as if pursuing. If `wake status` shows `continuation_ready=false` while pursuing, refuse further goal work until the loop is alive. Then start working toward the condition.

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
| Evaluator | `CURSOR_GOAL_EVAL_MODEL` or default `fast` | `Task` → `goal-evaluator` |

Always resolve Task params from the harness:

**Unix:**

```bash
SPAWN=$(python3 -u ~/.cursor/skills/goal/scripts/run_goal.py eval spawn-config)
```

**Windows:**

```powershell
$SPAWN = py -3 -u "$env:USERPROFILE\.cursor\skills\goal\scripts\run_goal.py" eval spawn-config
```

Example: `{"subagent_type":"goal-evaluator","model":"fast","readonly":true}`

Never evaluate with `generalPurpose` or the same Task call as the worker. Some Cursor plans only accept Task `model: "fast"`; override with a specific slug only when your plan allows it.

## Working Toward the Goal

While the goal is active (`status: "pursuing"`), repeat:

1. **Do focused work** — make code changes, run commands, fix issues
2. **Run validation** (if configured) — prefer harness `eval validate` so output is persisted for the evaluator
3. **Evaluate** — spawn a readonly evaluator subagent on the configured model
4. **Act on result** — YES → `manage done`. NO → continue.

### Evaluation via Subagent

When a validation command is configured, run `eval validate` first.

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

Alternatively: `eval parse-result @path\to\file.txt` or (short output only) `eval parse-result "YES: …"`.

On YES: automatically records a YES-bound evaluator signal (exit 0). On NO/UNCLEAR: exit 1.

**If YES** (exit 0): run `manage done`.

**If NO** (exit 1): read REASON, continue working, evaluate again after more progress.

`eval signal` is only for recovery (`eval signal --force` after a YES parse if the signal file was cleared). Prefer `parse-result` auto-signal. `--force` is not cryptographic attestation.

### When to Evaluate

Evaluate after validation results, logical units of work, or changes that could satisfy the condition. Do not evaluate after every single file edit or after read-only exploration.

**While `status` is `pursuing`, do not end the turn idle.** Either mark the goal done (`manage done` after YES) or finish an evaluate cycle (NO) and start the next concrete action in the same turn. Do not stop mid-goal waiting for the stop hook to wake you.

## Stop Hook Safety Net

The stop hook (`scripts/stop_hook.py`, Windows: `scripts/stop_hook.cmd`) fires when your turn ends. If the goal is still active, it returns a `followup_message` that auto-continues you.

**Do not rely on the stop hook as the primary evaluator.** In-turn subagent evaluation is primary. On Windows the installer uses a `.cmd` launcher plus a stdout drain delay to mitigate Cursor’s capture race; if a followup still drops, use the wake watchdog. Every stop emit writes `~/.cursor-goal/data/last-stop-response.json` (`ts`, `pid`, `payload`).

When you see a `[GOAL]` prefix, resume working toward the condition immediately.

## Wake Watchdog (race-immune continuation)

**Operational prerequisite:** while a goal is `pursuing` and wake is enabled, a live Shell wake loop with `notify_on_output` is required. Cursor may drop stop-hook stdout (see repo `docs/cursor-windows-stop-hook-race.md`); wake is the durable path. Opt out only with `CURSOR_GOAL_WAKE=0`.

`manage create` / `resume` arms wake state and prints one machine-readable line agents must consume:

```text
GOAL_WAKE_REQUIRED {"command":"<shell command>","pattern":"^AGENT_GOAL_WAKE","interval_s":15}
```

If arm fails, create/resume **exits 1**, leaves the goal **`paused`**, and does not print `GOAL_WAKE_REQUIRED`.

### Wake handshake (mandatory after every create/resume)

Do **not** skip. Until `continuation_ready=true` (implies `pid_alive=true`), refuse other goal work (`eval validate` will refuse unless `CURSOR_GOAL_ALLOW_DEAD_WAKE=1` or wake is disabled).

1. **Parse** the `GOAL_WAKE_REQUIRED` line from create/resume stdout (JSON after the prefix). If missing and wake is enabled, run `wake status` and use its `command` + `notify_pattern` fields (or `manage harness-cmd` → Wake loop line). Prefer the event `command` over hardcoded paths.
2. **Background Shell tool call** — immediately start that `command` with `notify_on_output` matching the event `pattern` (always `^AGENT_GOAL_WAKE` unless the event says otherwise). This must be a real Shell tool invocation; the harness cannot attach Cursor notifications for you.
3. **Verify:** `wake status` → `continuation_ready=true` (also `armed=true`, `pid_alive=true`). If `heartbeat_stale` is true, restart the loop.
4. **On wake:** when Shell notifies on `AGENT_GOAL_WAKE`, read the sentinel JSON `prompt`, run `manage status`, continue if still `pursuing`.
5. `manage done` / `pause` / `clear` disarms. Disable with `CURSOR_GOAL_WAKE=0` (doctor skips wake liveness when disabled).

Manual `wake tick` coalesces when a recent *wake*-sourced nudge falls inside one interval (avoids double ticks). The background `wake loop` emits on its own cadence and does not apply that coalesce window. Stop-hook stamps never suppress wake.

Interval: `CURSOR_GOAL_WAKE_INTERVAL_S` (default 15, min 5, max 600). Each wake emission increments `wake_ticks` against **`wake_budget`** (independent of turn budget). Default `wake_budget = turn_budget * 10` (min 10, max 500). Override with `--wake-budget N`.

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
| `manage status` ACTION REQUIRED / doctor FAIL wake | `continuation_ready=false` while pursuing | Blocking: start wake loop, confirm `continuation_ready=true` |
| Doctor FAIL classic + marketplace | Stacked install paths | Uninstall classic hooks **or** disable marketplace plugin |
| Marketplace hooks fail on Windows | Missing absolute `CURSOR_GOAL_PYTHON` | Set absolute env (required for doctor OK) or prefer `install-goal.ps1` |
| Validation refused (shell) | `--deny-shell` or `CURSOR_GOAL_DENY_SHELL` | Use argv-safe `--test` or allow shell |
| Doctor FAIL insecure data dir | World-writable `CURSOR_GOAL_DATA` | `chmod 700` / private path |

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
