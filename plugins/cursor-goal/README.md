# cursor-goal (Cursor plugin)

Teams/Enterprise: import this repository as a Team Marketplace (see repo `.cursor-plugin/marketplace.json`).

Individuals: prefer `scripts/install-goal.sh` / `install-goal.ps1` from a full clone or GitHub Release.

Version: **2.10.0** (AGPL-3.0-only). See `docs/teams-agpl.md` for AGPL / Teams notes.

Marketplace stop hooks register both `stop_hook.cmd` (Windows) and `python3 -u "…/stop_hook.py"` (Unix). On each OS one entry typically fails (cmd missing on Unix / python3 often missing on Windows) — **expected Hooks UI noise**, not necessarily a broken install. A singleflight lock ensures only one hook mutates turn state and writes stdout; the loser exits silently (no `{}`, no `last-stop-response.json` overwrite). Also ships a wake watchdog (`wake loop` / `AGENT_GOAL_WAKE`) for continuation when Cursor drops stop-hook stdout. In-turn evaluation remains primary; the stop hook is a safety net.

Set `CURSOR_GOAL_PYTHON` to an **absolute** Python 3.12+ path on Windows Teams installs — `manage doctor` **FAIL**s when marketplace hooks are detected without it (PATH fallback is fragile and not treated as success). Individuals should prefer classic `install-goal.ps1` (absolute interpreter bake). Resolve the harness with `manage harness-cmd` — skill/agent commands work from `${CURSOR_PLUGIN_ROOT}/skills/goal` without a classic install.

Do **not** stack classic `install-goal.*` hooks with marketplace hooks; `manage doctor` **FAIL**s when both look configured — pick one path.
