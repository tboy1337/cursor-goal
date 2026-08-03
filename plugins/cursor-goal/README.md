# cursor-goal (Cursor plugin)

Teams/Enterprise: import this repository as a Team Marketplace (see repo `.cursor-plugin/marketplace.json`).

Individuals: prefer `scripts/install-goal.sh` / `install-goal.ps1` from a full clone or GitHub Release.

Version: **4.0.0** (AGPL-3.0-only). License text ships as `COPYING` in this plugin tree. Teams/AGPL notes:
[docs/teams-agpl.md](https://github.com/tboy1337/cursor-goal/blob/main/docs/teams-agpl.md).

## Windows marketplace expectations

Marketplace stop hooks register both `stop_hook.cmd` (Windows) and `python3 -u "…/stop_hook.py"` (Unix). On each OS one entry typically fails (cmd missing on Unix / python3 often missing on Windows) — **expected Hooks UI noise**, not necessarily a broken install. A singleflight lock ensures only one hook mutates turn state and writes stdout; the loser exits silently (no `{}`, no `last-stop-response.json` overwrite). A `generation_id`-keyed dedupe stamp additionally guards *sequential* dual-hook invocations (one hook fully finishes, then the other starts for the same turn) from re-charging `turns_used` or emitting a second followup.

The same launcher command is also registered for the `subagentStop` event (`matcher: "goal-evaluator"`), giving a second, documented, race-free continuation point the instant the evaluator subagent finishes — `cmd_stop` dispatches between the two event shapes based on whether the JSON payload carries `subagent_type`.

`${CURSOR_PLUGIN_ROOT}` is not listed in Cursor's documented hook environment variables; these hook commands rely on it being set by the plugin host at invocation time. `stop_hook.py`'s `_ensure_package_path()` also works if it is unset, by resolving the vendored `cursor_goal` package relative to its own file location (`scripts/`, its parent skill dir, or the repo's `src/` in a source checkout) — the classic `~/.cursor/skills/goal` install path does not depend on `CURSOR_PLUGIN_ROOT` at all.

Set `CURSOR_GOAL_PYTHON` to an **absolute** Python 3.12+ path on Windows Teams installs — `manage doctor` **FAIL**s when marketplace hooks are detected without it (PATH fallback is fragile and not treated as success). Individuals should prefer classic `install-goal.ps1` (absolute interpreter bake). Resolve the harness with `manage harness-cmd` — skill/agent commands work from `${CURSOR_PLUGIN_ROOT}/skills/goal` without a classic install.

Also ships a wake watchdog (`wake loop` / `AGENT_GOAL_WAKE`) for continuation when Cursor drops stop-hook stdout. In-turn evaluation remains primary; the stop hook is a safety net.

Do **not** stack classic `install-goal.*` hooks with marketplace hooks; `manage doctor` **FAIL**s when both look configured — pick one path.
