# Platform Compatibility

cursor-goal targets **Cursor IDE only**. The harness is a **Python 3.12+** package; agent protocol and stop-hook auto-continuation are Cursor-specific.

**Install:** `./scripts/install-goal.sh` or `.\scripts\install-goal.ps1` from a full clone (see [install.md](install.md)).

## Compatibility Matrix

| Platform | Agent Defs | Subagent Tool | Stop Hook | Tested |
|----------|------------|---------------|-----------|--------|
| Cursor IDE (Unix) | `goalKeeper.md` + `goal-evaluator.md` | `Task` | `hooks.json` → `stop_hook.py` | **Harness YES**; stop followups **YES** |
| Cursor IDE (Windows) | same | `Task` | `hooks.json` → `stop_hook.cmd` (+ drain delay) | Harness YES; race mitigated ([forum](https://forum.cursor.com/t/race-condition-silently-disables-hooks-that-exit-quickly/165818)); still prefer in-turn eval |
| Cursor CLI | same | `Task` | `hooks.json` | NO (E2E) |

## Installed Layout

```
.cursor/agents/goalKeeper.md       → ~/.cursor/agents/goalKeeper.md
.cursor/agents/goal-evaluator.md   → ~/.cursor/agents/goal-evaluator.md
.cursor/skills/goal/SKILL.md       → ~/.cursor/skills/goal/SKILL.md
.cursor/skills/goal/scripts/*      → ~/.cursor/skills/goal/scripts/
src/cursor_goal/                   → ~/.cursor/skills/goal/cursor_goal/
~/.cursor/hooks.json               → stop → (Unix) <python> -u …/stop_hook.py
                                   → stop → (Windows) …/stop_hook.cmd
VERSION                            → ~/.cursor/skills/goal/VERSION (package stamp)
```

## Maker ≠ Checker (multi-model)

| Role | Model | Mechanism |
|------|--------|-----------|
| Worker | Session model | Skill + `goalKeeper` (`model: inherit`) |
| Evaluator | `CURSOR_GOAL_EVAL_MODEL` or default `fast` | `goal-evaluator` via `Task` |
| Continuation | N/A (no LLM) | Script Stop hook (`followup_message`) |

Resolve Task spawn parameters from the harness (do not hardcode a premium model):

```bash
python3 -u ~/.cursor/skills/goal/scripts/run_goal.py eval spawn-config
# → {"subagent_type":"goal-evaluator","model":"fast","readonly":true}
```

Some Cursor plans only accept Task `model: "fast"`; specific model IDs work when your plan allows them. See [Cursor subagents](https://cursor.com/docs/subagents.md).

## Environment Variables

| Variable | Purpose |
|----------|---------|
| `CURSOR_GOAL_DATA` | Absolute override for `~/.cursor-goal/data` |
| `CURSOR_GOAL_EVAL_MODEL` | Evaluator model slug for `eval spawn-config` (default `fast`) |
| `CURSOR_GOAL_LOG` | Log level (`WARNING` default; `DEBUG` writes `last-stop-response.json`) |
| `CURSOR_GOAL_STOP_DRAIN_MS` | Stop-hook stdout drain delay before exit (default ~100) |

## Harness Commands

| Command | Purpose |
|---------|---------|
| `…/run_goal.py parse "<input>"` | Parse `/goal` → JSON |
| `…/run_goal.py manage …` | State lifecycle |
| `…/run_goal.py eval validate` | Run `validation_command`; persist output |
| `…/run_goal.py eval spawn-config` | JSON Task params for the evaluator |
| `…/run_goal.py eval prompt\|parse-result\|signal\|check` | Evaluator harness |
| `…/run_goal.py stop` / `stop_hook.py` | Cursor stop hook stdin/stdout JSON |

State: `~/.cursor-goal/data/goal.json` (or `CURSOR_GOAL_DATA`). Treat that directory as **trusted-user state** — equivalent to shell trust. `validation_command` may be executed by `eval validate` (prefers argv; falls back to `shell=True` for metacharacters). Unix installers attempt `0600` on state files; Windows ACL hardening is not applied.

## Subagent Invocation Pattern

```bash
python3 -u ~/.cursor/skills/goal/scripts/run_goal.py eval validate
EVAL_PROMPT=$(python3 -u ~/.cursor/skills/goal/scripts/run_goal.py eval prompt --work-summary "...")
SPAWN=$(python3 -u ~/.cursor/skills/goal/scripts/run_goal.py eval spawn-config)
# Cursor Task: subagent_type / model / readonly from SPAWN JSON + prompt=$EVAL_PROMPT
# Never use generalPurpose for evaluation.
python3 -u ~/.cursor/skills/goal/scripts/run_goal.py eval parse-result "<response>"
# YES auto-records a YES-bound signal → manage done
```

## Security notes

- `~/.cursor-goal/data` and `validation_command` are trusted-user local state. If an attacker can write `goal.json`, they can run commands as you.
- Prefer `--test "..."` / simple argv-safe commands; compound shell snippets force `shell=True`.
- Eval signal is a protocol guard bound to the goal content hash with `verdict: YES` — not cryptographic attestation. `manage done --force` and `eval signal --force` exist for recovery and are logged.
- Stop hook does **not** run validation (avoids 30s hook timeouts).

## Design notes

- **Primary evaluation is in-turn.** The stop hook is a Cursor safety net only.
- Installer sets `loop_limit: null` so product `turn_budget` governs length (Cursor default would be 5).
- Prefer `--test "..."` for compound validation commands; NL runner hints truncate at `&&` / `|` / `;`.
- **Windows stop-hook mitigation:** installer writes `stop_hook.cmd` (cmd.exe launcher with absolute Python) and the Python hook flushes + waits ~100ms (`CURSOR_GOAL_STOP_DRAIN_MS`) before exit so Cursor’s stdout reader can catch `followup_message` ([Cursor race](https://forum.cursor.com/t/race-condition-silently-disables-hooks-that-exit-quickly/165818)). Residual risk remains until Cursor ships a permanent launcher fix. Debug: `CURSOR_GOAL_LOG=DEBUG` writes `~/.cursor-goal/data/last-stop-response.json`.
