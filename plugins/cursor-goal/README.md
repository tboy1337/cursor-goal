# cursor-goal (Cursor plugin)

Teams/Enterprise: import this repository as a Team Marketplace (see repo `.cursor-plugin/marketplace.json`).

Individuals: prefer `scripts/install-goal.sh` / `install-goal.ps1` from a full clone or GitHub Release.

Version: **1.3.0** (AGPL-3.0-only).

Stop hook uses `${CURSOR_PLUGIN_ROOT}` and `python3` on PATH (Unix/Teams-oriented). On native Windows prefer `install-goal.ps1`, which writes `stop_hook.cmd` with an absolute interpreter. In-turn evaluation remains primary; the stop hook is a safety net.
