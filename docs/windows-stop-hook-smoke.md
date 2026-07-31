# Windows stop-hook smoke checklist (manual)

After `.\scripts\install-goal.ps1`:

1. Confirm `~/.cursor/hooks.json` stop command ends with `stop_hook.cmd`.
2. Confirm `~/.cursor/skills/goal/scripts/stop_hook.cmd` embeds an absolute Python path and `PYTHONUNBUFFERED=1`.
3. Create a pursuing goal:

```powershell
py -3 -u "$env:USERPROFILE\.cursor\skills\goal\scripts\run_goal.py" manage create "smoke continue" --budget 5
```

4. In Cursor Agent, do a tiny edit and let the turn complete (`status=completed`).
5. Expect a `[GOAL]` followup. If missing:
   - View → Output → Hooks — look for stop exit 0 and JSON body
   - Set `$env:CURSOR_GOAL_LOG='DEBUG'`, re-trigger; check
     `%USERPROFILE%\.cursor-goal\data\last-stop-response.json` for `followup_message`
6. Clear:

```powershell
py -3 -u "$env:USERPROFILE\.cursor\skills\goal\scripts\run_goal.py" manage clear
```

Pipe-only check (no Cursor UI):

```powershell
$env:CURSOR_GOAL_STOP_DRAIN_MS = '0'
$payload = '{"status":"completed","loop_count":0}'
$payload | cmd /c "$env:USERPROFILE\.cursor\skills\goal\scripts\stop_hook.cmd"
```

Expect a JSON line with `followup_message` (or `{}` if no pursuing goal).

## Residual risk

Cursor may still drop fast-hook stdout on some Windows builds despite `.cmd` + drain delay.
Prefer in-turn evaluation (`eval spawn-config` + Task `goal-evaluator`). Treat followups as a safety net, not the primary completion path.
