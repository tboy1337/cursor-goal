# cursor-goal

[![License: AGPL-3.0](https://img.shields.io/badge/license-AGPL--3.0-blue.svg)](COPYING)
[![Python 3.12+](https://img.shields.io/badge/python-3.12%2B-blue.svg)](https://www.python.org/downloads/)
[![Coverage ≥95%](https://img.shields.io/badge/coverage-%E2%89%A595%25-brightgreen.svg)](scripts/check_coverage_metrics.py)
[![Cursor IDE](https://img.shields.io/badge/Cursor-IDE-black.svg)](https://cursor.com)

**Set a verifiable condition. Keep working until it's met.**

Autonomous `/goal` loop for Cursor IDE: persist an objective, work across turns, and stop only when the condition is actually true.

**Primary loop:** in-turn subagent evaluation (worker ≠ evaluator model) via the Python harness.  
**Safety nets:** Cursor `stop` hook (`followup_message`) plus a wake watchdog (`AGENT_GOAL_WAKE`) that does not depend on hook stdout capture.

```text
/goal all tests in test/auth pass and the lint step is clean
```

## Minimum happy path

1. Clone and install (`install-goal.sh` / `install-goal.ps1`), then **restart Cursor**.
2. `manage doctor` — fix any FAIL lines.
3. Create a demo goal (argv-safe `--test`).
4. Start the `GOAL_WAKE_REQUIRED` command in a background Shell with `notify_on_output` matching `^AGENT_GOAL_WAKE`.
5. Confirm `wake status` → `continuation_ready=true`, then work until evaluator YES → `manage done`.

Wake is **required** while pursuing (not optional). Details: [First run](#first-run-wake-handshake).

## Requirements

- **Python 3.12+** (`python3`, `python`, or Windows `py -3`)
- Cursor IDE 1.7+ recommended

No `jq` dependency.

## Install

Three supported paths:

| Path | Who | How |
|------|-----|-----|
| **Clone + installer** | Individuals | Clone `main` (or a GitHub Release source archive) → `install-goal.sh` / `install-goal.ps1` |
| **Tagged release** | Individuals | `git clone --branch v3.0.0 …` then installer (see [docs/install.md](docs/install.md)). |
| **Teams marketplace** | Teams/Enterprise | Import this repo in Cursor Dashboard → Plugins (see `.cursor-plugin/marketplace.json`) |

**Agent install (explicit steps):**

1. Clone `https://github.com/tboy1337/cursor-goal` (default branch `main`). Prefer a tagged source archive only when that tag exists on [GitHub Releases](https://github.com/tboy1337/cursor-goal/releases).
2. Run the OS installer from the repo root (`install-goal.sh` on Unix/macOS; `install-goal.ps1` on native Windows).
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
2. Run `manage doctor` — fix any FAIL lines before starting a goal.
3. In Cursor create a demo goal (argv-safe `--test`, no shell metacharacters):

   - Windows: `/goal "demo done" --test "py -3 -c \"raise SystemExit(0)\""`
   - Unix: `/goal "demo done" --test "python3 -c 'raise SystemExit(0)'"`

4. **Start the wake loop before doing other work** (required for continuation when Cursor drops stop-hook stdout):
   - Find the create output line starting with `GOAL_WAKE_REQUIRED `.
   - Parse the JSON after that prefix; copy the `command` field.
   - Start that command in a **background** Shell with `notify_on_output` matching `^AGENT_GOAL_WAKE` (same as the JSON `pattern` / `notify_pattern`).
   - Confirm: `wake status` shows `continuation_ready=true` (and usually `pid_alive=true`).
5. Work toward the condition; on evaluator YES run `manage done`. If Hooks UI shows `{}`, rely on wake — see [known limitations](docs/known-limitations.md).

Security: see [SECURITY.md](SECURITY.md). Platform notes: [docs/platform-compatibility.md](docs/platform-compatibility.md). Known limits: [docs/known-limitations.md](docs/known-limitations.md). Troubleshooting: [docs/troubleshooting.md](docs/troubleshooting.md). Teams/AGPL: [docs/teams-agpl.md](docs/teams-agpl.md).

Note: an unrelated npm package is also named `cursor-goal`; this project is the Python/AGPL harness at `tboy1337/cursor-goal`.

**Teams / AGPL:** marketplace import redistributes AGPL-3.0 code — review [docs/teams-agpl.md](docs/teams-agpl.md) and [COPYING](COPYING) before enterprise use.

Verify (Unix / macOS / WSL):

```bash
python3 -u ~/.cursor/skills/goal/scripts/run_goal.py manage status
# → [goal] No active goal.
python3 -u ~/.cursor/skills/goal/scripts/run_goal.py eval spawn-config
# → {"subagent_type":"goal-evaluator","model":"fast","readonly":true}
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

**3.0.0:** Clean-break production release — tight wake ownership matching (no pytest/IDE false-positive kills), stop insecure/ACL emit `{}`, resume disarm on mutate failure, redacted `eval validate` stdout, YES/done gated on `pursuing`, tokened `wake.pid` only (marker-only hooks), fail-early Windows ACL before skill copy, install/CI/docs/onboarding hardening.

**2.16.0:** Production hardening — install ACL soft-failure hard-fails, doctor/eval/wake budget redact conditions, absolute `CURSOR_GOAL_LOG_FILE` paths, doctor/wake ACL force re-harden, `run_validation` defaults to `shell_ok=False`, classic install `.cmd` CGP metachar parity + private installer temps.

**2.14.0:** Reliability/security hardening — wake tick fail-closed on persist failure, transactional create/resume arm, doctor marketplace deep scan + VERSION sync, wake ownership in continuation_ready, create requires `--force` for any existing goal, marketplace `.cmd` uses `%CGP%`, eval/stop refuse insecure dirs, probe OSError fail-closed, scrub drops `NODE_PATH`/`MAVEN_OPTS`-class vars. Also: host-native path helpers, doctor data-dir `ValueError`, wake ownership null-subprocess tolerance, macOS install-smoke non-symlink HOME, `wake-smoke.py` in CI, module splits (`path_trust` / `doctor` / `wake_process`), clearer first-run wake handshake docs.

## Multi-model (maker ≠ checker)

| Role | Model | Agent |
|------|--------|-------|
| Worker | Session model | `goalKeeper` (`inherit`) |
| Evaluator | `fast` by default | `goal-evaluator` via `Task` |

Override the evaluator model:

```bash
# Unix
export CURSOR_GOAL_EVAL_MODEL=composer-2.5
# Windows PowerShell
$env:CURSOR_GOAL_EVAL_MODEL = 'composer-2.5'
```

Then `eval spawn-config` prints the resolved Task parameters. Some Cursor plans only accept Task `model: "fast"`; use a specific slug only when your plan allows it.

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

**Trust model:** `~/.cursor-goal/data` and `validation_command` are trusted-user local state (equivalent to shell access). Prefer simple argv-safe `--test` commands. **Always arm wake** while pursuing — stop-hook followups are best-effort under a Cursor stdout race (see [known limitations](docs/known-limitations.md)).

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

## Releases

Maintainer checklist: [docs/release.md](docs/release.md).

## License

[GNU Affero General Public License v3.0](COPYING)
