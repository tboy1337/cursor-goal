# Cursor stop-hook stdout capture race

## Summary

Cursor’s hook executor can treat a hook process as finished **before** all stdout is read. Valid JSON (including `followup_message`) is printed and the process exits 0, but the Hooks Execution Log shows `{}` and auto-continuation never runs.

This is a **Cursor IDE bug**, not a cursor-goal logic bug. cursor-goal mitigates the race and provides a **wake watchdog** continuation path that does not depend on hook stdout.

## Confirmed timeline

| Date | Source | Finding |
|------|--------|---------|
| Mar 2026 | [Forum 155078](https://forum.cursor.com/t/stop-hook-followup-message-not-captured-on-windows-execution-log-shows-despite-valid-json-on-stdout/155078) | Windows stop hook prints valid `followup_message`; log shows `{}`. Manual pipe test confirms script output. Dean Rie: PowerShell buffering / launcher capture bug on Cursor’s side. |
| Jul 2026 | [Forum 165818](https://forum.cursor.com/t/race-condition-silently-disables-hooks-that-exit-quickly/165818) | Same class of race on **Linux** for fast hooks (e.g. `preToolUse` deny dropped). Mohit (Cursor): real bug; flush + ~50ms wait “not bulletproof”; fix in progress. |
| Jul 2026 | Reporter reverse-engineering in 165818 | Hook runner appears to return on `exit` while stdout events may still be pending — classic Node `exit` vs `close` mistake. |

As of the research date for this doc, Cursor staff had **not** posted a release where the race is verified gone. Treat stop-hook `followup_message` as best-effort until then.

## Root cause (parent-side)

Node’s `child_process` emits `exit` when the process ends, and `close` when stdio streams are done. Data can still be in the pipe after `exit`. Correct consumers wait for `close` (or fully drain stdout) before finalizing captured output ([nodejs/node#45085](https://github.com/nodejs/node/issues/45085), [Node child_process docs](https://nodejs.org/api/child_process.html)).

On Windows, an extra PowerShell bootstrap layer historically worsened buffering; using `cmd.exe` (`.cmd` launcher) shrinks but does not eliminate the race.

**Only Cursor can permanently fix capture** by draining pipes / waiting for stream close before parsing hook stdout.

## Analogous problems elsewhere

| Ecosystem | Problem | Durable solution |
|-----------|---------|------------------|
| Node.js / Electron | Child exits; stdout still in flight | Listen for `close`, not `exit` |
| DesktopCommanderMCP | `pwsh.exe` AllocConsole steals stdout | Parent spawn: `windowsHide` + explicit `stdio` pipes |
| Claude Code stop hooks | Transcript flush / Windows stdin races | Poll-until-stable; absolute paths; avoid PowerShell JSON pitfalls |
| Claude ralph-loop | Unreliable stop continuation | Do not rely on stop alone |
| Cursor `/loop` skill | Need wake without stop hook | Background shell + `notify_on_output` sentinel |

Parent-side spawn fixes (windowsHide, wait-for-close) apply to **Cursor’s** process launcher, not to scripts we ship. Our durable approach mirrors `/loop`: an independent wake channel.

## What cursor-goal does

### Best-effort stop hook (secondary)

- Windows installer writes `stop_hook.cmd` with absolute Python and `PYTHONUNBUFFERED=1`
- `emit()` flushes stdout, best-effort `fsync`, then drains (`CURSOR_GOAL_STOP_DRAIN_MS`; default ~250ms on Windows, ~100ms elsewhere)
- Always writes `~/.cursor-goal/data/last-stop-response.json` (`ts`, `pid`, `payload`) for diagnosis when this process owns singleflight
- Marketplace dual hooks: the lock **loser exits silently** (no stdout `{}`, no diagnostic overwrite) so Cursor cannot clobber a real followup

### Wake watchdog (tertiary, race-immune)

- `wake arm` / `wake loop` / `wake tick` / `wake disarm`
- Background loop emits `AGENT_GOAL_WAKE {…}` while the goal is `pursuing`
- Agent arms Shell with `notify_on_output` on `^AGENT_GOAL_WAKE` — **no hook stdout required**
- Disable with `CURSOR_GOAL_WAKE=0`

### Primary protocol

In-turn `goal-evaluator` Task evaluation. Never end a pursuing turn idle waiting for the stop hook.

## Verify a future Cursor fix

Use [windows-stop-hook-smoke.md](windows-stop-hook-smoke.md):

1. Create a pursuing goal; let a tiny agent turn complete.
2. Hooks Execution Log should show the same `followup_message` as `last-stop-response.json`.
3. If the file has `followup_message` but Hooks shows `{}`, the race is still present.
4. With wake armed, continuation should still occur via `AGENT_GOAL_WAKE` even when Hooks shows `{}`.

## Related docs

- [platform-compatibility.md](platform-compatibility.md)
- [windows-stop-hook-smoke.md](windows-stop-hook-smoke.md)
- [install.md](install.md)
