# cursor-goal

[![License: AGPL-3.0](https://img.shields.io/badge/license-AGPL--3.0-blue.svg)](COPYING)
[![Python 3.12+](https://img.shields.io/badge/python-3.12%2B-blue.svg)](https://www.python.org/downloads/)
[![Coverage ≥95%](https://img.shields.io/badge/coverage-%E2%89%A595%25-brightgreen.svg)](scripts/check_coverage_metrics.py)
[![Cursor IDE](https://img.shields.io/badge/Cursor-IDE-black.svg)](https://cursor.com)

**Set a verifiable condition. Keep working until it's met.**

Autonomous `/goal` loop for Cursor IDE: persist an objective, work across turns, and stop only when the condition is actually true.

**Primary loop:** in-turn subagent evaluation (worker ≠ evaluator model) via the Python harness.  
**Continuation:** Cursor's documented `stop` and `subagentStop` hooks (`followup_message`) keep turns flowing, plus a best-effort wake watchdog (`AGENT_GOAL_WAKE`) that does not depend on hook stdout capture.

```text
/goal all tests in test/auth pass and the lint step is clean
```

## Minimum happy path

1. Clone and install (`scripts/install-goal.sh` / `scripts/install-goal.ps1`), then **restart Cursor**.
2. `manage doctor` — fix any FAIL lines.
3. Create a demo goal (argv-safe `--test`).
4. Start the `GOAL_WAKE_REQUIRED` command in a background Shell with `notify_on_output` matching `^AGENT_GOAL_WAKE`.
5. Confirm `wake status` → `continuation_ready=true`, then work until evaluator YES → `manage done`.

Wake is armed by default while pursuing and recommended as a best-effort supplement, but `eval` commands only **warn** (not refuse) when the loop isn't confirmed alive — see [worked example](#worked-example) and [First run](#first-run-wake-handshake).

## Requirements

- **Python 3.12+** (`python3`, `python`, or Windows `py -3`)
- Cursor IDE 1.7+ recommended

No `jq` dependency.

## Install

Three supported paths:

| Path | Who | How |
|------|-----|-----|
| **Clone + installer** | Individuals | Clone `main` (or a GitHub Release source archive) → `scripts/install-goal.sh` / `scripts/install-goal.ps1` |
| **Tagged release** | Individuals | `git clone --branch v4.0.0 …` then installer (see [docs/install.md](docs/install.md)). |
| **Teams marketplace** | Teams/Enterprise | Import this repo in Cursor Dashboard → Plugins (see `.cursor-plugin/marketplace.json`) |

**Agent install (explicit steps):**

1. Clone `https://github.com/tboy1337/cursor-goal` (default branch `main`). Prefer a tagged source archive only when that tag exists on [GitHub Releases](https://github.com/tboy1337/cursor-goal/releases).
2. Run the OS installer from the repo root (`scripts/install-goal.sh` on Unix/macOS; `scripts/install-goal.ps1` on native Windows).
3. Verify with `manage status` and `eval spawn-config` (commands below).

Do **not** use Git Bash `install-goal.sh` against native Windows Cursor — the script refuses and redirects you to `install-goal.ps1` (required for `stop_hook.cmd`).

Tell your agent:

```
Install the /goal skill from https://github.com/tboy1337/cursor-goal:
1) git clone https://github.com/tboy1337/cursor-goal.git
2) On Windows: powershell -NoProfile -ExecutionPolicy Bypass -File .\scripts\install-goal.ps1
   On Unix/macOS: ./scripts/install-goal.sh
3) Verify with manage status
```

Or from a local clone (the installer needs the package tree — do not curl only `scripts/install-goal.sh`):

```bash
git clone https://github.com/tboy1337/cursor-goal.git
cd cursor-goal
./scripts/install-goal.sh
```

Windows PowerShell:

```powershell
git clone https://github.com/tboy1337/cursor-goal.git
cd cursor-goal
powershell -NoProfile -ExecutionPolicy Bypass -File .\scripts\install-goal.ps1
```

Uninstall: `./scripts/uninstall-goal.sh` or `.\scripts\uninstall-goal.ps1` (add `--purge-data` / `-PurgeData` to remove `~/.cursor-goal`).

`pip install -e ".[dev]"` installs the `cursor-goal` CLI for **development only** — it does **not** register the Cursor skill, agents, or stop hook. Always run the installer (or Teams marketplace import) for Cursor integration.

## First run (wake handshake)

1. Install for your OS (classic installer above, or Teams marketplace import).
2. Run `manage doctor` — fix any FAIL lines before starting a goal. Run `manage harness-cmd` once to resolve the correct `run_goal.py` invocation for your install (classic vs. Teams marketplace) and reuse its printed command/Wake-loop lines for the rest of the session instead of hardcoding a path.
3. In Cursor create a demo goal (argv-safe `--test`, no shell metacharacters):

   - Windows: `/goal "demo done" --test "py -3 -c \"raise SystemExit(0)\""`
   - Unix: `/goal "demo done" --test "python3 -c 'raise SystemExit(0)'"`

4. **Start the wake loop before doing other work** (recommended, best-effort supplement for continuation when Cursor drops `stop`/`subagentStop` hook stdout):
   - Find the create output line starting with `GOAL_WAKE_REQUIRED `.
   - Parse the JSON after that prefix; copy the `command` field.
   - Start that command in a **background** Shell with `notify_on_output` matching `^AGENT_GOAL_WAKE` (same as the JSON `pattern` / `notify_pattern`).
   - Confirm: `wake status` shows `continuation_ready=true` (and usually `pid_alive=true`). `manage status` / `manage doctor` still hard-fail while pursuing without it; `eval validate`/`prompt`/`spawn-config` only warn by default (see [known limitations](docs/known-limitations.md)).
5. Work toward the condition; on evaluator YES run `manage done`. If Hooks UI shows `{}`, rely on the `subagentStop` hook and wake — see [known limitations](docs/known-limitations.md).

Security: see [SECURITY.md](SECURITY.md). Platform notes: [docs/platform-compatibility.md](docs/platform-compatibility.md). Known limits: [docs/known-limitations.md](docs/known-limitations.md). Troubleshooting: [docs/troubleshooting.md](docs/troubleshooting.md). Teams/AGPL: [docs/teams-agpl.md](docs/teams-agpl.md).

Note: an unrelated npm package is also named `cursor-goal`; this project is the Python/AGPL harness at `tboy1337/cursor-goal`.

**Teams / AGPL:** marketplace import redistributes AGPL-3.0 code — review [docs/teams-agpl.md](docs/teams-agpl.md) and [COPYING](COPYING) before enterprise use.

## Worked example

A complete create-to-done session, run by hand from a Unix shell (an agent normally drives this through the `/goal` skill instead of typing commands directly):

```bash
RUN="python3 -u ~/.cursor/skills/goal/scripts/run_goal.py"

# 1. Create a goal with an argv-safe validation command (no shell metacharacters
#    such as `;`/`|`/`&`, so it runs without --allow-shell).
$RUN manage create "a file named ok.txt exists" \
  --test "python3 -c \"exit(0 if __import__('os').path.exists('ok.txt') else 1)\"" \
  --budget 10
# → [goal] Goal created: ... Status: pursuing (or "paused (awaiting wake arm)" then "pursuing")

# 2. Start the wake loop in a background Shell (recommended; copy the `command`
#    from the GOAL_WAKE_REQUIRED line create just printed) with notify_on_output
#    matching ^AGENT_GOAL_WAKE, then confirm it is alive:
$RUN wake status
# → {"armed": true, "pid_alive": true, "continuation_ready": true, ...}

# 3. Do the actual work.
touch ok.txt

# 4. Run validation so its output is persisted for the evaluator.
$RUN eval validate
# → [goal] PASSED (exit 0) ...

# 5. Generate the evaluator prompt and Task spawn config, then run the
#    goal-evaluator subagent yourself (this is what the /goal skill's Task
#    call automates) and capture its raw text response.
$RUN eval prompt > /tmp/prompt.txt
$RUN eval spawn-config
# → {"subagent_type": "goal-evaluator", "model": "composer-2.5", "readonly": true}

# 6. Feed the subagent's verdict text back in (here simulated directly):
echo "YES: ok.txt exists and validation passed" | $RUN eval parse-result --stdin
# → [goal-eval] YES signal recorded automatically. (exit 0)

# 7. Mark the goal done.
$RUN manage done
# → [goal] Goal achieved in N turns: a file named ok.txt exists
```

If the evaluator instead returns NO, `eval parse-result` exits 1 with the reason on stderr — keep working and evaluate again; do not call `manage done`.

Verify (Unix / macOS / WSL):

```bash
python3 -u ~/.cursor/skills/goal/scripts/run_goal.py manage status
# → [goal] No active goal.
python3 -u ~/.cursor/skills/goal/scripts/run_goal.py eval spawn-config
# → {"subagent_type":"goal-evaluator","model":"composer-2.5","readonly":true}
python3 -u ~/.cursor/skills/goal/scripts/run_goal.py manage doctor
```

Verify (Windows PowerShell):

```powershell
py -3 -u "$env:USERPROFILE\.cursor\skills\goal\scripts\run_goal.py" manage status
py -3 -u "$env:USERPROFILE\.cursor\skills\goal\scripts\run_goal.py" eval spawn-config
py -3 -u "$env:USERPROFILE\.cursor\skills\goal\scripts\run_goal.py" manage doctor
```

## Usage

Lifecycle: create a goal, then `status` / `pause` / `resume` / `clear` as needed.

```text
/goal all tests in test/auth pass and the lint step is clean
/goal status
/goal pause | resume | clear
```

Flags / natural language:

```text
/goal "all tests pass" --test "npm test" --budget 20
/goal "compound check" --test "npm test && npm run lint" --allow-shell
/goal fix bugs, verified by pytest, stop after 15 turns
```

## Multi-model (maker ≠ checker)

| Role | Model | Agent |
|------|--------|-------|
| Worker | Session model | `goalKeeper` (`inherit`) |
| Evaluator | `composer-2.5` by default | `goal-evaluator` via `Task` |

Override the evaluator model:

```bash
# Unix
export CURSOR_GOAL_EVAL_MODEL=gpt-5.3-codex
# Windows PowerShell
$env:CURSOR_GOAL_EVAL_MODEL = 'gpt-5.3-codex'
```

Then `eval spawn-config` prints the resolved Task parameters. `fast` is **not** a valid Cursor
model ID (it is only a bracket parameter such as `composer-2.5[fast=false]`) — setting
`CURSOR_GOAL_EVAL_MODEL=fast` is treated as a known-invalid legacy value, logs a warning, and
falls back to the default rather than silently running the checker on the worker's model. On
legacy request-based plans without Max Mode, subagents may still run on Composer regardless of
`model`, so maker != checker cannot be *guaranteed* from frontmatter alone; `manage doctor`
reports the resolved evaluator model so you can confirm what actually ran.

## Architecture

| Layer | Role |
|-------|------|
| `parse` | NL `/goal` input → JSON |
| `manage` | Persist lifecycle in `~/.cursor-goal/data/goal.json` |
| `eval validate` | Run `validation_command`; persist output for prompts |
| `eval spawn-config` | JSON Task params for the evaluator (model + subagent) |
| `eval` | Evaluator prompt, YES-bound signal, YES/NO parse |
| `stop` | Cursor stop hook: turn++, budget, `followup_message` (no validation subprocess) |
| `wake` | Race-immune wake watchdog (`arm`/`loop`/`tick`/`disarm`) via shell notify |
| `SKILL.md` / `goalKeeper.md` / `goal-evaluator.md` | Agent protocol |

CLI (after install):

```bash
python -u ~/.cursor/skills/goal/scripts/run_goal.py parse "..."
python -u ~/.cursor/skills/goal/scripts/run_goal.py manage create "..." [--test "..."] [--budget N]
python -u ~/.cursor/skills/goal/scripts/run_goal.py eval validate|spawn-config|prompt|parse-result|signal|check
```

Developers can also `pip install -e ".[dev]"` and use `cursor-goal` / `python -m cursor_goal`.

**Trust model:** `~/.cursor-goal/data` and `validation_command` are trusted-user local state (equivalent to shell access). Prefer simple argv-safe `--test` commands. Continuation relies primarily on Cursor's documented `stop` and `subagentStop` hooks; **recommended: arm wake** as a best-effort supplement in case a hook's stdout is dropped (see [known limitations](docs/known-limitations.md)).

## Platform support

| Platform | Status |
|----------|--------|
| Cursor IDE (Unix) | Reference — harness unit-tested; stop hook verified on Unix; residual fast-hook race possible — use wake |
| Cursor IDE (Windows) | Harness works; `stop_hook.cmd` + drain mitigate the [Cursor capture race](https://forum.cursor.com/t/race-condition-silently-disables-hooks-that-exit-quickly/165818); wake watchdog continues when followups drop |

See [docs/platform-compatibility.md](docs/platform-compatibility.md), [docs/cursor-windows-stop-hook-race.md](docs/cursor-windows-stop-hook-race.md), [docs/known-limitations.md](docs/known-limitations.md), [docs/troubleshooting.md](docs/troubleshooting.md), and [docs/install.md](docs/install.md).

## Testing

```bash
pip install -e ".[dev]"
pytest tests -q
python scripts/check_coverage_metrics.py   # statement/branch/function/combined >= 95%

# Bash lint
shellcheck --severity=warning scripts/*.sh
# or: ./scripts/run-shellcheck.sh

# PowerShell (Windows): PSScriptAnalyzer + Pester with >=95% command coverage
powershell -NoProfile -ExecutionPolicy Bypass -File .\scripts\run-powershell-tests.ps1

# Full local verification (format, mypy, pylint, pytest, coverage metrics,
# ShellCheck, and on Windows PSScriptAnalyzer/Pester):
py -3 scripts/verify.py
py -3 scripts/verify.py --fix   # apply isort/black, then verify
```

Manual IDE-level regression material (workloads, subagent tests, sample
transcripts) lives in [`testing/`](testing/README.md) — see
[`testing/README.md`](testing/README.md).

## Releases

Maintainer checklist: [docs/release.md](docs/release.md).

## License

[GNU Affero General Public License v3.0](COPYING)
