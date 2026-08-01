# cursor-goal (Cursor plugin)

Teams/Enterprise: import this repository as a Team Marketplace (see repo `.cursor-plugin/marketplace.json`).

Individuals: prefer `scripts/install-goal.sh` / `install-goal.ps1` from a full clone or GitHub Release.

Version: **2.1.0** (AGPL-3.0-only).

Marketplace stop hooks register both `stop_hook.cmd` (Windows) and `python3 …/stop_hook.py` (Unix). A singleflight lock ensures only one emits `followup_message`. Also ships a wake watchdog (`wake loop` / `AGENT_GOAL_WAKE`) for continuation when Cursor drops stop-hook stdout. In-turn evaluation remains primary; the stop hook is a safety net.
