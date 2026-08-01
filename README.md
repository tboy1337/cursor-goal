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

## Requirements

- **Python 3.12+** (`python3`, `python`, or Windows `py -3`)
- Cursor IDE 1.7+ recommended

No `jq` dependency.

## Install

Three supported paths:

| Path | Who | How |
|------|-----|-----|
| **Clone + installer** | Individuals | Full clone or GitHub Release archive → `install-goal.sh` / `install-goal.ps1` |
| **Tagged release** | Individuals | `git clone --branch v2.1.0 …` then installer (see [docs/install.md](docs/install.md)) |
| **Teams marketplace** | Teams/Enterprise | Import this repo in Cursor Dashboard → Plugins (see `.cursor-plugin/marketplace.json`) |

**Agent install (explicit steps):**

1. Clone `https://github.com/tboy1337/cursor-goal` (or download a tagged source archive from GitHub Releases).
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

Security: see [SECURITY.md](SECURITY.md). Platform notes: [docs/platform-compatibility.md](docs/platform-compatibility.md).

Note: an unrelated npm package is also named `cursor-goal`; this project is the Python/AGPL harness at `tboy1337/cursor-goal`.

Verify:

```bash
python3 -u ~/.cursor/skills/goal/scripts/run_goal.py manage status
# → [goal] No active goal.
python3 -u ~/.cursor/skills/goal/scripts/run_goal.py eval spawn-config
# → {"subagent_type":"goal-evaluator","model":"fast","readonly":true}
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
/goal fix bugs, verified by pytest, stop after 15 turns
```

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

**Trust model:** `~/.cursor-goal/data` and `validation_command` are trusted-user local state (equivalent to shell access). Prefer simple argv-safe `--test` commands.

## Platform support

| Platform | Status |
|----------|--------|
| Cursor IDE (Unix) | Reference — harness unit-tested; stop hook verified on Unix |
| Cursor IDE (Windows) | Harness works; `stop_hook.cmd` + drain mitigate the [Cursor capture race](https://forum.cursor.com/t/race-condition-silently-disables-hooks-that-exit-quickly/165818); wake watchdog continues when followups drop |

See [docs/platform-compatibility.md](docs/platform-compatibility.md), [docs/cursor-windows-stop-hook-race.md](docs/cursor-windows-stop-hook-race.md), and [docs/install.md](docs/install.md).

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

See [CHANGELOG.md](CHANGELOG.md) and the maintainer checklist in [docs/release.md](docs/release.md).

## License

[GNU Affero General Public License v3.0](COPYING)
