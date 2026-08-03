# Troubleshooting

Quick fixes for common install and continuation failures. Also run:

```bash
# Unix / macOS / WSL
python3 -u ~/.cursor/skills/goal/scripts/run_goal.py manage doctor
```

```powershell
# Windows
py -3 -u "$env:USERPROFILE\.cursor\skills\goal\scripts\run_goal.py" manage doctor
```

Stall checklist (Hooks `{}`, no continuation): work the steps below before filing a GitHub issue.

## Hooks Execution Log shows `{}` but the goal should continue

1. Check `~/.cursor-goal/data/last-stop-response.json`. If it contains `followup_message` while Hooks shows `{}`, you hit the [Cursor stdout race](cursor-windows-stop-hook-race.md).
2. Ensure wake is armed and the loop is alive (`manage status` / `manage doctor`). Doctor **FAIL**s while `pursuing` if wake is enabled and `continuation_ready=false` (missing or dead loop). Doctor skips that gate when `CURSOR_GOAL_WAKE=0`.
3. Prefer the `command` from create/resume's `GOAL_WAKE_REQUIRED` line (or `wake status` JSON). Start background Shell with `notify_on_output` matching `^AGENT_GOAL_WAKE`:

```text
# Unix
python3 -u ~/.cursor/skills/goal/scripts/run_goal.py wake loop
```

```powershell
# Windows classic — prefer baked wake_loop.cmd when present
py -3 -u "$env:USERPROFILE\.cursor\skills\goal\scripts\run_goal.py" wake loop
```

4. Confirm `wake status` shows `continuation_ready=true` (and `pid_alive=true`).

## Wake not armed / `continuation_ready=false`

Doctor **hard-fails** when a goal is `pursuing`, wake is enabled, and the loop is missing or dead. `manage status` prints an **ACTION REQUIRED** recovery command, `Continuation ready: false (…)`, and exits **1**. Immediately start `wake loop` as above. If create/resume exited 1 with the goal paused, fix data-dir/ACL issues then `manage resume`. Disable wake only with `CURSOR_GOAL_WAKE=0` (not recommended while the stop race remains).

## Classic + marketplace hooks stacked

Doctor **FAIL**s when both classic `~/.cursor/hooks.json` stop entries and Teams marketplace plugin hooks look configured. Marketplace detection walks `~/.cursor/plugins/cache/**` and `~/.cursor/plugins/local/**` (bounded) for `hooks/hooks.json` goal stop markers, plus `CURSOR_PLUGIN_ROOT`. Pick **one** install path: uninstall classic hooks (`uninstall-goal.*` without necessarily purging data) **or** disable the marketplace plugin — do not run both.

## Classic install VERSION drift

Doctor **FAIL**s when `~/.cursor/skills/goal/VERSION` (or the resolved skill root `VERSION`) is missing on a classic install path or does not match the running package version. Re-run `install-goal.sh` / `install-goal.ps1` (or sync/re-enable the marketplace plugin) so the installed skill tree matches the package.

## `manage doctor` FAIL: insecure data directory

**Unix:** data dir must not be a symlink, must be owned by you, and must not be group/world-writable (`chmod 700`).

**Windows:** data dir must not be a symlink/junction/reparse point. ACL harden failures also FAIL create/validate/stop/wake and doctor — verify only you can access the path, or set `CURSOR_GOAL_SKIP_ACL=1` after manual lockdown.

Override with `CURSOR_GOAL_DATA` to a private directory.

## Marketplace stop/wake fails to find Python (Windows)

**Recommended fix path (pick one):**

1. **Individuals:** prefer classic `install-goal.ps1`, which bakes an absolute interpreter into `stop_hook.cmd` / `wake_loop.cmd` and (when `CURSOR_GOAL_PYTHON` is set) requires an absolute 3.12+ path — same gates as the marketplace launcher.
2. **Teams marketplace:** set an **absolute** `CURSOR_GOAL_PYTHON` (user or system env), then restart Cursor:

```powershell
$env:CURSOR_GOAL_PYTHON = 'C:\Path\To\python.exe'
# Persist for your user, then restart Cursor:
[Environment]::SetEnvironmentVariable('CURSOR_GOAL_PYTHON', 'C:\Path\To\python.exe', 'User')
```

Relative or bare `python` values are rejected. Values with cmd metacharacters (`" & | ^ < >`) are also rejected. Doctor **FAIL**s on Windows when marketplace hooks are detected and `CURSOR_GOAL_PYTHON` is unset or non-absolute — PATH-only resolution is fragile and not treated as success for marketplace installs. Marketplace/template `.cmd` launchers execute the quote-stripped `%CGP%` value (same as classic bake), not the raw env var.

## Wrong installer / WSL mix-up

- Native Windows Cursor → `install-goal.ps1` (not Git Bash `install-goal.sh`).
- WSL Cursor → Unix installer inside WSL `$HOME`, not `/mnt/c/...` mixed with native Windows hooks.

## Git Bash refused on Windows

`install-goal.sh` refuses when it detects native Windows Cursor paths — use PowerShell `install-goal.ps1` so `stop_hook.cmd` is correct.

## Shell-mode validation warning

Doctor warns when validation uses shell mode. New goals default to `shell_ok=false`; create **refuses** shell-metachar `--test` without `--allow-shell`. Prefer argv-safe `--test` or keep deny-shell. Use `--allow-shell` only when compound shell is required.

## Task / evaluator model errors

Some Cursor plans only accept Task `model: "fast"`. Override only when your plan allows:

```bash
export CURSOR_GOAL_EVAL_MODEL=fast
```

## Corrupt `goal.json`

Corrupt files are renamed to `goal.json.corrupt.<UTC>`. Remove or fix, then `manage create` again. See doctor output for lock timeouts.

## Still stuck?

1. `manage status` and `manage doctor`
2. [known-limitations.md](known-limitations.md)
3. [platform-compatibility.md](platform-compatibility.md)
4. Upstream Cursor hook issues: report on the Cursor forum (out of scope for this repo’s security advisories)
