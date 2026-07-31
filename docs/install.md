# Install cursor-goal

## Requirements

- **Python 3.12+** on `PATH` (`python3` / `python` / Windows `py -3`)
- Cursor IDE 1.7+ for stop-hook auto-continuation (recommended)

## Quick install (full clone required)

Tell your agent:

```
Install the /goal skill from https://github.com/tboy1337/cursor-goal
```

Or from a local clone. The installer copies the Python package and skill files from the repo tree. **Do not** pipe a lone download of `scripts/install-goal.sh` into bash — that fails because the package sources are missing.

```bash
git clone https://github.com/tboy1337/cursor-goal.git
cd cursor-goal
./scripts/install-goal.sh
```

Windows:

```powershell
git clone https://github.com/tboy1337/cursor-goal.git
cd cursor-goal
powershell -NoProfile -ExecutionPolicy Bypass -File .\scripts\install-goal.ps1
```

## What gets installed

| Path | Purpose |
|------|---------|
| `~/.cursor/skills/goal/SKILL.md` | Agent skill protocol |
| `~/.cursor/skills/goal/cursor_goal/` | Python package (vendored) |
| `~/.cursor/skills/goal/scripts/run_goal.py` | CLI entry for agents |
| `~/.cursor/skills/goal/scripts/stop_hook.py` | Cursor stop hook |
| `~/.cursor/skills/goal/VERSION` | Installed package version stamp |
| `~/.cursor/agents/goalKeeper.md` | Worker agent (`model: inherit`) |
| `~/.cursor/agents/goal-evaluator.md` | Readonly evaluator (`model: fast` default) |
| `~/.cursor/hooks.json` | Stop hook registration. Unix: `<absolute-python> -u …/stop_hook.py`. Windows: absolute `…/stop_hook.cmd` (cmd launcher). `loop_limit: null`, `timeout: 30`. Prior file is copied to a timestamped `.bak.<UTC>` |
| `~/.cursor-goal/data/` | Runtime state (`goal.json`, `goal-eval-done`) — trusted-user local state (≡ shell trust) |

Override data directory with `CURSOR_GOAL_DATA` (resolved absolute path).

Override evaluator model with `CURSOR_GOAL_EVAL_MODEL` (default `fast`). See [platform-compatibility.md](platform-compatibility.md).

On upgrade, a previous skill tree is copied to `~/.cursor/skills/goal.bak.<UTC>` before replace. If hook merge fails after files are copied, restore from that backup manually.

## Verify

```bash
python3 -u ~/.cursor/skills/goal/scripts/run_goal.py manage status
python3 -u ~/.cursor/skills/goal/scripts/run_goal.py eval spawn-config
```

Expected status: `[goal] No active goal.`  
Expected spawn-config: JSON with `"subagent_type":"goal-evaluator"` and `"model":"fast"` (unless overridden).

Then in Agent chat: `/goal status`

## Uninstall

```bash
./scripts/uninstall-goal.sh
# optional: ./scripts/uninstall-goal.sh --purge-data
```

```powershell
powershell -NoProfile -ExecutionPolicy Bypass -File .\scripts\uninstall-goal.ps1
# optional: ... -PurgeData
```

## Platform notes

| Platform | Install notes |
|----------|----------------|
| Cursor IDE (Unix/macOS) | Fully supported |
| Cursor IDE (Windows) | Installer writes `stop_hook.cmd` (absolute Python baked in) plus a ~100ms stdout drain delay to mitigate Cursor’s capture race. Prefer in-turn evaluation; set `CURSOR_GOAL_LOG=DEBUG` to write `last-stop-response.json` if diagnosing followups. Unix-style `chmod` privacy on state files is N/A on Windows. |
| WSL | Use `./scripts/install-goal.sh` inside WSL; prefer WSL Cursor for reliable stop followups |

## Contributor install

```bash
pip install -e ".[dev]"
pytest tests -q
# or: py -3 scripts/verify.py
cursor-goal manage status
```
