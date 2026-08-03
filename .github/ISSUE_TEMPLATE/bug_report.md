---
name: Bug report
about: Report a defect in cursor-goal
title: "[Bug] "
labels: bug
assignees: ''
---

## Describe the bug

A clear, concise description of what is broken.

## Environment

- `cursor-goal` version (`manage --version` or `pip show cursor-goal`):
- Install method: marketplace plugin / classic installer (`scripts/install-goal.sh` or `.ps1`) / `pip install -e`
- OS: Windows / macOS / Linux (+ version)
- Shell: PowerShell / bash / zsh (+ version)
- Python version (`python --version`):
- Cursor IDE version:

## `manage doctor` output

Paste the full output of:

```bash
python -u ~/.cursor/skills/goal/scripts/run_goal.py manage doctor
```

(or the Windows equivalent). Redact any paths/usernames you don't want to
share; secret-ish values should already be redacted by the tool.

## Steps to reproduce

1.
2.
3.

## Expected behavior

What you expected to happen.

## Actual behavior

What actually happened. Include exact error text/exit codes.

## Logs

If you can reproduce with `CURSOR_GOAL_LOG_FILE=1` set, attach or paste the
relevant log lines (redact anything sensitive first).

## Additional context

Anything else that might help — e.g. whether the wake watchdog was armed,
whether this happens on a fresh goal or only a specific one, etc.
