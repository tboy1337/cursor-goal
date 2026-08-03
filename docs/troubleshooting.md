# Troubleshooting

Quick fixes for common install and continuation failures. Also run:

```bash
python3 -u ~/.cursor/skills/goal/scripts/run_goal.py manage doctor
```

## Hooks Execution Log shows `{}` but the goal should continue

1. Check `~/.cursor-goal/data/last-stop-response.json`. If it contains `followup_message` while Hooks shows `{}`, you hit the [Cursor stdout race](cursor-windows-stop-hook-race.md).
2. Ensure wake is armed and the loop is alive (`manage status` / `manage doctor`).
3. Start background Shell with `notify_on_output` matching `^AGENT_GOAL_WAKE`:

```text
python3 -u ~/.cursor/skills/goal/scripts/run_goal.py wake loop
```

On Windows classic install, prefer the baked `wake_loop.cmd`.

## Wake not armed / `pid_alive=false`

Doctor warns when a goal is `pursuing` without a live wake loop. Immediately start `wake loop` as above. Disable only with `CURSOR_GOAL_WAKE=0` (not recommended while the stop race remains).

## `manage doctor` FAIL: insecure data directory

**Unix:** data dir must not be a symlink, must be owned by you, and must not be group/world-writable (`chmod 700`).

**Windows:** data dir must not be a symlink/junction/reparse point. ACL harden failures also FAIL create/validate/stop/wake and doctor — verify only you can access the path, or set `CURSOR_GOAL_SKIP_ACL=1` after manual lockdown.

Override with `CURSOR_GOAL_DATA` to a private directory.

## Marketplace stop/wake fails to find Python (Windows)

Set an **absolute** path:

```powershell
$env:CURSOR_GOAL_PYTHON = 'C:\Path\To\python.exe'
```

Relative or bare `python` values are rejected. Prefer classic `install-goal.ps1`, which bakes absolute Python into the launchers.

## Wrong installer / WSL mix-up

- Native Windows Cursor → `install-goal.ps1` (not Git Bash `install-goal.sh`).
- WSL Cursor → Unix installer inside WSL `$HOME`, not `/mnt/c/...` mixed with native Windows hooks.

## Git Bash refused on Windows

`install-goal.sh` refuses when it detects native Windows Cursor paths — use PowerShell `install-goal.ps1` so `stop_hook.cmd` is correct.

## Shell-mode validation warning

Doctor warns when validation uses shell mode. New goals default to `shell_ok=false`; prefer argv-safe `--test` or keep deny-shell. Use `--allow-shell` only when compound shell is required.

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
