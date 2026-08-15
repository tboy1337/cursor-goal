# Platform Compatibility

cursor-goal targets **Cursor IDE only**. The harness is a **Python 3.12+** package; agent protocol and stop-hook auto-continuation are Cursor-specific. `/goal` itself is an OpenAI Codex feature; this package is the Cursor port.

**Install:** `./scripts/install-goal.sh` or `.\scripts\install-goal.ps1` from a full clone (see [install.md](install.md)).

Also: [known-limitations.md](known-limitations.md) · [troubleshooting.md](troubleshooting.md)

## Compatibility Matrix

| Platform | Agent Defs | Subagent Tool | Stop / subagentStop Hooks | Tested |
|----------|------------|---------------|---------------------------|--------|
| Cursor IDE (Unix) | `goalKeeper.md` + `goal-evaluator.md` + `goal-auditor.md` | `Task` | `hooks.json` → `stop_hook.py` (both events) | **Harness YES**; hook followups documented and primary; occasional stdout-capture race — arm wake as a best-effort supplement |
| Cursor IDE (Windows) | same | `Task` | `stop_hook.cmd` (+ drain), both events | Harness YES; race mitigated; wake is a recommended best-effort supplement ([research](cursor-windows-stop-hook-race.md)) |
| Teams marketplace plugin | same | `Task` | Dual `stop_hook.cmd` + `python3` + singleflight/dedupe, both events | Harness YES; set absolute `CURSOR_GOAL_PYTHON` (doctor requires it on Windows marketplace) |
| Cursor CLI | same | `Task` | `hooks.json` | NO (E2E) |

## Installed Layout

```
.cursor/agents/goalKeeper.md       → ~/.cursor/agents/goalKeeper.md
.cursor/agents/goal-evaluator.md   → ~/.cursor/agents/goal-evaluator.md
.cursor/agents/goal-auditor.md     → ~/.cursor/agents/goal-auditor.md
.cursor/skills/goal/SKILL.md       → ~/.cursor/skills/goal/SKILL.md
.cursor/skills/goal/scripts/*      → ~/.cursor/skills/goal/scripts/
src/cursor_goal/                   → ~/.cursor/skills/goal/cursor_goal/
~/.cursor/hooks.json               → stop, subagentStop (matcher: goal-evaluator and goal-auditor)
                                   → (Unix) <python> -u …/stop_hook.py
                                   → (Windows) …/stop_hook.cmd
VERSION                            → ~/.cursor/skills/goal/VERSION (package stamp)
```

## Maker ≠ Checker (multi-model)

| Role | Model | Mechanism |
|------|--------|-----------|
| Worker | Session model | Skill + `goalKeeper` (`model: inherit`) |
| Remaining-work auditor | Session model (`inherit`) | `goal-auditor` via `Task` |
| Evaluator | `CURSOR_GOAL_EVAL_MODEL` or default `composer-2.5` | `goal-evaluator` via `Task` |
| Continuation | N/A (no LLM) | `stop` + `subagentStop` hooks (`followup_message`, documented, primary) + wake watchdog (`AGENT_GOAL_WAKE`, undocumented, best-effort) |

Resolve Task spawn parameters from the harness (do not hardcode a premium model):

```bash
python3 -u ~/.cursor/skills/goal/scripts/run_goal.py eval spawn-config
# → {"subagent_type":"goal-evaluator","model":"composer-2.5","readonly":true}
python3 -u ~/.cursor/skills/goal/scripts/run_goal.py eval audit-spawn-config
# → {"subagent_type":"goal-auditor","model":"inherit","readonly":true}
```

`fast` is **not** a valid Cursor `model` ID — it is only a bracket parameter on a real model (e.g. `composer-2.5[fast=false]`). Setting `CURSOR_GOAL_EVAL_MODEL=fast` is treated as a known-invalid legacy value: it logs a warning and falls back to the default rather than being passed through, and `manage doctor` hard-fails on it. On legacy request-based Cursor plans without Max Mode, Task subagents may still run on a Cursor-selected model regardless of the requested `model` — `spawn-config` reflects the *requested* model, not a runtime guarantee. See [Cursor subagents](https://cursor.com/docs/subagents.md).

## Environment Variables

| Variable | Purpose |
|----------|---------|
| `CURSOR_GOAL_DATA` | Absolute override for `~/.cursor-goal/data` |
| `CURSOR_GOAL_HOME` | Absolute override for skill/home resolution used by path helpers (when set) |
| `CURSOR_GOAL_PYTHON` | Absolute Python 3.12+ interpreter for marketplace/classic Windows `.cmd` launchers (required for reliable Teams marketplace on Windows) |
| `CURSOR_GOAL_EVAL_MODEL` | Evaluator model slug for `eval spawn-config` (default `composer-2.5`; `fast` is a known-invalid legacy value that falls back to the default) |
| `CURSOR_GOAL_VALIDATE_TIMEOUT_SEC` | `eval validate` timeout in seconds (default 600; clamped to 25–3600). Invalid values fall back to 600 |
| `CURSOR_GOAL_LOG` | Log level (`WARNING` default; invalid values fall back to WARNING). `last-stop-response.json` is always written on stop emit (redacted) |
| `CURSOR_GOAL_LOG_FILE` | Optional durable log path, or `1`/`.` for `cursor-goal.log` under the data dir |
| `CURSOR_GOAL_STOP_DRAIN_MS` | Stop-hook stdout drain delay before exit (default ~250 on Windows, ~100 elsewhere; max 2000) |
| `CURSOR_GOAL_WAKE` | When `0`/`false`/`off`, disable wake watchdog arming |
| `CURSOR_GOAL_WAKE_INTERVAL_S` | Wake loop interval seconds (default 15, min 5, max 600) |
| `CURSOR_GOAL_ALLOW_DEAD_WAKE` | When `1`/`true`/`yes`/`on`, silence the wake-dead warning printed by `eval validate`/`prompt`/`spawn-config` (they already continue by default) |
| `CURSOR_GOAL_REQUIRE_WAKE` | When `1`/`true`/`yes`/`on`, restore the old strict behavior: `eval validate`/`prompt`/`spawn-config` **refuse** (exit 1) while pursuing without a live wake loop, instead of only warning |
| `CURSOR_GOAL_ALLOW_ANY_WORKDIR` | When set, allow `--workdir` outside the create-time process cwd (still rejects symlink/junction/reparse) |
| `CURSOR_GOAL_DENY_SHELL` | When `1`/`true`/`yes`/`on`, refuse shell-mode validation (argv only) |
| `CURSOR_GOAL_LOG_SECRETS` | When set, DEBUG may log full validation commands (default: never) |
| `CURSOR_GOAL_SKIP_ACL` | When set, skip Windows `icacls` data-dir harden (used by tests) |

## Harness Commands

| Command | Purpose |
|---------|---------|
| `…/run_goal.py parse "<input>"` | Parse `/goal` → JSON |
| `…/run_goal.py manage …` | State lifecycle (`create`/`status`/`doctor`/`pause`/`resume`/`done`/`clear`) |
| `…/run_goal.py eval validate` | Run `validation_command`; persist output |
| `…/run_goal.py eval spawn-config` | JSON Task params for the evaluator |
| `…/run_goal.py eval audit-spawn-config` | JSON Task params for the remaining-work auditor |
| `…/run_goal.py eval prompt\|parse-result\|signal\|check` | Evaluator harness (`parse-result --stdin` / `@file` preferred on Windows) |
| `…/run_goal.py eval audit-prompt\|parse-audit` | Remaining-work auditor harness (`parse-audit --stdin` / `@file` preferred on Windows) |
| `…/run_goal.py stop` / `stop_hook.py` | Cursor stop hook stdin/stdout JSON |
| `…/run_goal.py wake …` | Wake watchdog (`arm`/`tick`/`disarm`/`status`/`loop`) |

State: `~/.cursor-goal/data/goal.json` (or `CURSOR_GOAL_DATA`). Treat that directory as **trusted-user state** — equivalent to shell trust. Create/validate refuse a group/world-writable data dir on Unix. Data-dir paths must not traverse symlink/junction/reparse ancestors (by design). Temporary `HOME` trees under macOS `/var/folders` (symlink to `/private/var/folders`) fail doctor unless you place the data dir on a real path (install-smoke uses `/private/tmp` or `CURSOR_GOAL_SMOKE_BASE`). `validation_command` may be executed by `eval validate` (prefers argv; falls back to `shell=True` / `COMSPEC` on Windows for metacharacters only when the goal has `shell_ok=true` via `--allow-shell` and `CURSOR_GOAL_DENY_SHELL` is unset — new goals default to `shell_ok=false`). Optional `workdir` (schema v1) binds validation cwd. Unix uses `0700` on the data dir and `0600` on state files. Windows best-effort `icacls` strips inheritance then grants the current user full control on the data dir (skip with `CURSOR_GOAL_SKIP_ACL=1`; loud warning if grant fails after strip). Corrupt `goal.json` is quarantined to `goal.json.corrupt.<UTC>`. Exclusive `goal.lock` times out after ~10s on both Unix and Windows. Stop emits always write `last-stop-response.json`.

## Subagent Invocation Pattern

```bash
python3 -u ~/.cursor/skills/goal/scripts/run_goal.py eval validate
EVAL_PROMPT=$(python3 -u ~/.cursor/skills/goal/scripts/run_goal.py eval prompt --work-summary "...")
SPAWN=$(python3 -u ~/.cursor/skills/goal/scripts/run_goal.py eval spawn-config)
# Cursor Task: subagent_type / model / readonly from SPAWN JSON + prompt=$EVAL_PROMPT
# Never use generalPurpose for evaluation.
python3 -u ~/.cursor/skills/goal/scripts/run_goal.py eval parse-result --stdin <<'EOF'
<subagent response>
EOF
# YES auto-records a YES-bound signal; CLEAR from parse-audit is also required → manage done
```

On Windows, pipe the response into `eval parse-result --stdin` (or use `@file`) — do not put long evaluator text on the command line.

## Security notes

- `~/.cursor-goal/data` and `validation_command` are trusted-user local state. If an attacker can write `goal.json`, they can run commands as you.
- Prefer `--test "..."` / simple argv-safe commands; compound shell snippets need `--allow-shell` (cmd.exe on Windows via pinned `COMSPEC` = `%SystemRoot%\System32\cmd.exe` when present, not PowerShell). New goals default to deny-shell; `CURSOR_GOAL_DENY_SHELL=1` / `--deny-shell` still force argv-only. Library `run_validation` defaults to `shell_ok=False`.
- `eval parse-result @file` only accepts paths under the goal data directory or the current working directory.
- Eval signal is a protocol guard bound to the goal content hash with `verdict: YES` — not cryptographic attestation. `manage done --force` and `eval signal --force` exist for recovery and are logged.
- Stop hook does **not** run validation (avoids 30s hook timeouts).
- Turn budget and wake budget are independent (each capped at 500). Default `wake_budget = turn_budget * 10` (min 10). Stop-hook drain is capped at 2000ms.
- See [SECURITY.md](../SECURITY.md) for the full threat model and reporting process.

## Design notes

- **Primary evaluation is in-turn.** After validation, spawn `goal-auditor` then `goal-evaluator`. The `stop`/`subagentStop` hooks are Cursor's documented continuation mechanism, not the evaluator — they only keep the turn loop going.
- `eval validate` defaults to a 600s timeout (`CURSOR_GOAL_VALIDATE_TIMEOUT_SEC`, clamped 25–3600). Timeouts count as failed validation. Do not replace the user's `--test` with a shorter proxy.
- Installer sets `loop_limit: null` so product `turn_budget` governs length (Cursor default would be 5).
- Prefer `--test "..."` for compound validation commands; NL runner hints truncate at `&&` / `|` / `;`.
- **Windows stop-hook mitigation:** installer writes `stop_hook.cmd` (cmd.exe launcher with absolute Python) and the Python hook flushes + waits (~250ms on Windows via `CURSOR_GOAL_STOP_DRAIN_MS`) before exit so Cursor's stdout reader can catch `followup_message` ([Cursor race](https://forum.cursor.com/t/race-condition-silently-disables-hooks-that-exit-quickly/165818)). Residual risk remains until Cursor ships a permanent launcher fix. Always writes `last-stop-response.json`. **Wake watchdog** (`wake loop` + `notify_on_output`) is a best-effort, undocumented supplement that continues goals when followups drop — see [cursor-windows-stop-hook-race.md](cursor-windows-stop-hook-race.md) and [known-limitations.md](known-limitations.md).
