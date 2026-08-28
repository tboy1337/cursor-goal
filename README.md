# cursor-goal

[![License: AGPL-3.0](https://img.shields.io/badge/license-AGPL--3.0-blue.svg)](COPYING)
[![Python 3.12+](https://img.shields.io/badge/python-3.12%2B-blue.svg)](https://www.python.org/downloads/)
[![Coverage ≥95%](https://img.shields.io/badge/coverage-%E2%89%A595%25-brightgreen.svg)](scripts/check_coverage_metrics.py)
[![Cursor IDE](https://img.shields.io/badge/Cursor-IDE-black.svg)](https://cursor.com)

**Set a verifiable condition. Keep working until it is met.**

`/cursor-goal` is a Cursor skill that persists a goal on disk, runs your `--test` command, and refuses to mark the work done until a remaining-work auditor says CLEAR and a separate evaluator says YES.

```text
/cursor-goal all tests in test/auth pass and the lint step is clean
```

This skill uses Cursor's native `CreateGoal` / `UpdateGoal` tools automatically so the agent keeps going. Cursor's built-in `/goal` command is unchanged and remains the default.

## Requirements

- Python 3.12+ (`python3`, `python`, or Windows `py -3`)
- Cursor IDE 1.7+

## Install

Clone the repo and run the installer for your OS, then restart Cursor.

Unix / macOS:

```bash
git clone https://github.com/tboy1337/cursor-goal.git
cd cursor-goal
./scripts/install-goal.sh
```

Windows (PowerShell — not Git Bash):

```powershell
git clone https://github.com/tboy1337/cursor-goal.git
cd cursor-goal
powershell -NoProfile -ExecutionPolicy Bypass -File .\scripts\install-goal.ps1
```

Pin a release with `git clone --branch v5.1.10` instead of `main` when you want that tag. Teams/Enterprise can import this repository as a marketplace plugin (see `.cursor-plugin/marketplace.json` and [docs/teams-agpl.md](docs/teams-agpl.md)).

After a successful install:

1. Restart Cursor (or reload hooks) so `hooks.json` takes effect.
2. In Agent chat: `/cursor-goal <verifiable condition>`.

The installer already ran `manage doctor`. If it printed `Doctor: OK` and exited 0, you are done. Re-run doctor only when diagnosing stalls ([docs/troubleshooting.md](docs/troubleshooting.md)).

Uninstall with `./scripts/uninstall-goal.sh` or `.\scripts\uninstall-goal.ps1` (`--purge-data` / `-PurgeData` also removes `~/.cursor-goal`).

`pip install -e ".[dev]"` is for developing this repo. It does not register the Cursor skill, agents, or hooks.

More detail: [docs/install.md](docs/install.md).

## Use

In Cursor:

```text
/cursor-goal all tests in test/auth pass and the lint step is clean
/cursor-goal status
/cursor-goal pause | resume | clear
/cursor-goal blocked missing deploy key
```

With a validation command and a turn budget:

```text
/cursor-goal "all tests pass" --test "npm test" --budget 20
/cursor-goal "compound check" --test "npm test && npm run lint" --allow-shell
```

Cursor's `/goal` command stays the built-in default. This harness runs only when you type `/cursor-goal`. Native `CreateGoal` / `UpdateGoal` are used automatically on `/cursor-goal` when those tools exist.

A pursuing goal finishes only in this order: remaining-work auditor **CLEAR**, evaluator **YES**, then `manage done`. If native continuation is on, call `UpdateGoal complete` after `manage done`.

## Continuation

**Native (default when CreateGoal succeeds).** Cursor keeps the agent working. Skip the wake loop.

**Hooks and wake (when CreateGoal is missing or failed).** Cursor `stop` / `subagentStop` followups continue the turn. After `manage create`, start the printed `GOAL_WAKE_REQUIRED` command in a background Shell with `notify_on_output` matching `^AGENT_GOAL_WAKE FOLLOWUP_REQUIRED pursuing spawn_goal-auditor`, then confirm `wake status` shows `continuation_ready=true`. `manage harness-cmd` prints the right `run_goal.py` invocation for classic vs marketplace installs.

Cloud Agents do not receive classic `~/.cursor/skills` user skills. Use a local IDE install or the Teams plugin.

Limits and Windows hook notes: [docs/known-limitations.md](docs/known-limitations.md), [docs/troubleshooting.md](docs/troubleshooting.md).

## Worked example

An agent normally drives this through the skill. The same loop by hand:

```bash
RUN="python3 -u ~/.cursor/skills/cursor-goal/scripts/run_goal.py"

$RUN manage create "a file named ok.txt exists" \
  --test "python3 -c \"exit(0 if __import__('os').path.exists('ok.txt') else 1)\"" \
  --budget 10

# If create printed GOAL_WAKE_REQUIRED, start that command before continuing.
$RUN wake status

touch ok.txt
$RUN eval validate

$RUN eval audit-spawn-config
echo "CLEAR: ok.txt exists; nothing in-scope remains" | $RUN eval parse-audit --stdin

$RUN eval spawn-config
echo "YES: ok.txt exists and validation passed" | $RUN eval parse-result --stdin

$RUN manage done
```

If the auditor returns REMAINING, implement that list and spawn a new auditor. If the tree changed after CLEAR, audit again. If the evaluator returns NO, keep working — do not call `manage done`.

Check the install:

```bash
python3 -u ~/.cursor/skills/cursor-goal/scripts/run_goal.py manage status
python3 -u ~/.cursor/skills/cursor-goal/scripts/run_goal.py eval spawn-config
python3 -u ~/.cursor/skills/cursor-goal/scripts/run_goal.py manage doctor
```

Windows PowerShell:

```powershell
py -3 -u "$env:USERPROFILE\.cursor\skills\cursor-goal\scripts\run_goal.py" manage status
py -3 -u "$env:USERPROFILE\.cursor\skills\cursor-goal\scripts\run_goal.py" eval spawn-config
py -3 -u "$env:USERPROFILE\.cursor\skills\cursor-goal\scripts\run_goal.py" manage doctor
```

These commands are the **classic** install tree. For a Teams marketplace install, run `manage harness-cmd` and use that `run_goal.py` path instead. Do not stack classic hooks with marketplace hooks.

## Evaluator model

The worker uses the session model. The remaining-work auditor does too (`inherit`). The evaluator defaults to `composer-2.5`.

```bash
export CURSOR_GOAL_EVAL_MODEL=gpt-5.3-codex
```

```powershell
$env:CURSOR_GOAL_EVAL_MODEL = 'gpt-5.3-codex'
```

`eval spawn-config` prints the Task parameters that will be used. `manage doctor` reports the resolved evaluator model.

## Docs

| Doc | Contents |
|-----|----------|
| [docs/install.md](docs/install.md) | Installer paths, what gets copied, tagged releases |
| [docs/troubleshooting.md](docs/troubleshooting.md) | Doctor FAILs, hooks, wake, marketplace |
| [docs/known-limitations.md](docs/known-limitations.md) | Continuation, blocked vs pause, audit rules |
| [docs/cursor-windows-stop-hook-race.md](docs/cursor-windows-stop-hook-race.md) | Upstream stop-hook stdout race |
| [docs/windows-stop-hook-smoke.md](docs/windows-stop-hook-smoke.md) | Manual Windows hook smoke |
| [docs/platform-compatibility.md](docs/platform-compatibility.md) | Windows / Unix / Teams |
| [SECURITY.md](SECURITY.md) | Trust model for `~/.cursor-goal/data` |
| [docs/teams-agpl.md](docs/teams-agpl.md) | Marketplace redistribution under AGPL-3.0 |
| [docs/release.md](docs/release.md) | Cutting `vX.Y.Z` |

## Develop

```bash
pip install -e ".[dev]"
python3 scripts/verify.py            # Windows: py -3 scripts/verify.py
python3 scripts/verify.py --fix      # isort/black, then verify
```

`verify.py` is the local ship gate: isort/black/pyproject-fmt, mypy, pylint, complexipy, bandit, pip-audit, version-sync, plugin-tree-sync, pytest (coverage ≥95% statement/branch/function/combined), wake-smoke, ShellCheck, install-smoke, and on Windows PSScriptAnalyzer/Pester. `pytest tests` alone is not enough. IDE regression material lives in [`testing/`](testing/README.md).

## License

[GNU Affero General Public License v3.0](COPYING)
