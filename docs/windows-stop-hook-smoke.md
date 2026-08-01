# Windows stop-hook smoke checklist (manual)

After `.\scripts\install-goal.ps1`:

0. Run doctor and confirm OK (or OK with warnings):

```powershell
py -3 -u "$env:USERPROFILE\.cursor\skills\goal\scripts\run_goal.py" manage doctor
```

1. Confirm `~/.cursor/hooks.json` stop command ends with `stop_hook.cmd`.
2. Confirm `~/.cursor/skills/goal/scripts/stop_hook.cmd` embeds an absolute Python path and `PYTHONUNBUFFERED=1`.
3. Create a pursuing goal:

```powershell
py -3 -u "$env:USERPROFILE\.cursor\skills\goal\scripts\run_goal.py" manage create "smoke continue" --budget 5
```

Expect `Wake budget: 50` (default `budget * 10`) and schema/status fields via `manage status`.

4. Expect create output to mention wake armed. Start the wake loop in a **background** Cursor Shell with `notify_on_output` matching `^AGENT_GOAL_WAKE`:

```powershell
py -3 -u "$env:USERPROFILE\.cursor\skills\goal\scripts\run_goal.py" wake loop
```

5. In Cursor Agent, do a tiny edit and let the turn complete (`status=completed`).
6. Expect a `[GOAL]` followup **or** an `AGENT_GOAL_WAKE` notification. If stop followup is missing:
   - View → Output → Hooks — look for stop exit 0 and JSON body
   - Check `%USERPROFILE%\.cursor-goal\data\last-stop-response.json` for `payload.followup_message`
   - If the file has `followup_message` but Hooks shows `{}`, that is the Cursor capture race ([research](cursor-windows-stop-hook-race.md))
   - Wake sentinel should still fire within `CURSOR_GOAL_WAKE_INTERVAL_S` (default 15s; first tick is immediate once the loop starts)
7. Clear:

```powershell
py -3 -u "$env:USERPROFILE\.cursor\skills\goal\scripts\run_goal.py" manage clear
```

Pipe-only check (no Cursor UI):

```powershell
$env:CURSOR_GOAL_STOP_DRAIN_MS = '0'
$payload = '{"status":"completed","loop_count":0}'
$payload | cmd /c "$env:USERPROFILE\.cursor\skills\goal\scripts\stop_hook.cmd"
```

Expect a JSON line with `followup_message` (or `{}` if no pursuing goal) and a written `last-stop-response.json`.

Wake tick check:

```powershell
py -3 -u "$env:USERPROFILE\.cursor\skills\goal\scripts\run_goal.py" manage create "wake smoke" --budget 3
py -3 -u "$env:USERPROFILE\.cursor\skills\goal\scripts\run_goal.py" wake tick
# Expect: AGENT_GOAL_WAKE {"prompt":"[GOAL] ..."}
py -3 -u "$env:USERPROFILE\.cursor\skills\goal\scripts\run_goal.py" manage clear
```

## Residual risk

Cursor may still drop fast-hook stdout on some Windows builds despite `.cmd` + drain delay.
Prefer in-turn evaluation (`eval spawn-config` + Task `goal-evaluator`). Treat stop followups as a safety net; treat the wake watchdog as the race-immune backup.
