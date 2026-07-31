---
name: goal
description: Autonomous goal loop. Use when the user types /goal followed by a completion condition, or asks to pursue a goal until tests/lint/build pass. Keeps working across turns until the condition is met via a separate evaluator model and a stop-hook safety net.
---

# /goal — Autonomous Goal Loop

Set a persistent objective. Work toward it across turns until it is met.

## Harness (Python)

Prefer the installed skill runner. Use the interpreter that matches the host OS:

**Unix / macOS / WSL:**

```bash
python3 -u ~/.cursor/skills/goal/scripts/run_goal.py <command> ...
```

**Windows (PowerShell / Cursor Shell):**

```powershell
py -3 -u "$env:USERPROFILE\.cursor\skills\goal\scripts\run_goal.py" <command> ...
```

If the package is installed editable (`pip install -e .`), `python -m cursor_goal` and `cursor-goal` also work — but editable install does **not** register the Cursor skill, agents, or stop hook. Use the installer for that.

| Command | Purpose |
|---------|---------|
| `parse "<input>"` | Parse `/goal` input → JSON |
| `manage create\|status\|pause\|resume\|done\|clear` | Goal state lifecycle |
| `eval validate\|spawn-config\|prompt\|parse-result\|signal\|check` | Evaluator harness |
| `stop` | Stop hook (stdin JSON → stdout JSON) |

State file: `~/.cursor-goal/data/goal.json` (override with `CURSOR_GOAL_DATA`).

`validation_command` is trusted-user local state (executed by `eval validate` / agent Shell). Prefer `--test "..."` for compound commands. Treat `~/.cursor-goal/data` as shell-equivalent trust. Set `CURSOR_GOAL_DENY_SHELL=1` to refuse shell-mode validation.

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
- If `action` is `create` → `manage create "<condition>" [--test "<cmd>"] [--budget N]`

After creating a goal, **immediately start working** toward the condition.

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

**Do not rely on the stop hook as the primary evaluator.** In-turn subagent evaluation is primary. On Windows the installer uses a `.cmd` launcher plus a short stdout drain delay to mitigate Cursor’s capture race; if a followup still drops, keep evaluating inside the turn. With `CURSOR_GOAL_LOG=DEBUG`, the last stop JSON is also written to `~/.cursor-goal/data/last-stop-response.json`.

When you see a `[GOAL]` prefix, resume working toward the condition immediately.

## Turn Budget

Default budget is 20 turns (max 500). Customize with `--budget N` or natural language (`stop after 10 turns`). When exhausted, status becomes `budget-limited` and you wrap up.

## Writing Good Conditions

```
✓ all tests in test/auth pass and the lint step is clean
✓ every call site of the old API has been migrated and the build succeeds
✗ the code is clean
✗ implement the feature
```

## Status Values

`pursuing`, `paused`, `achieved`, `budget-limited`
