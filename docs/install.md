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

On upgrade, a previous skill tree is copied to `~/.cursor/skills/goal.bak.<UTC>` before replace. If hook merge fails after files are copied, the installer restores that backup automatically when one exists.

`pip install -e .` provides the `cursor-goal` console script for contributors; it does **not** install the Cursor skill, agents, or hooks. Use the scripts above for Cursor.

### Install from a tagged release

```bash
git clone --branch v1.3.0 https://github.com/tboy1337/cursor-goal.git
cd cursor-goal
./scripts/install-goal.sh   # or install-goal.ps1 on Windows
```

Or download the source archive from the GitHub Release for that tag and run the same installer from the extracted tree.

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

## Teams marketplace (plugin)

On Cursor **Teams** / **Enterprise**, admins can import this repository as a Team Marketplace:

1. Push (or fork) `tboy1337/cursor-goal` on GitHub.
2. Dashboard → Settings → Plugins → Import Marketplace → paste the repo URL.
3. Cursor reads [`.cursor-plugin/marketplace.json`](../.cursor-plugin/marketplace.json) and the plugin under `plugins/cursor-goal/`.

The plugin ships skill, agents, vendored harness, and a stop hook that uses `${CURSOR_PLUGIN_ROOT}` plus `python3` on `PATH` (no absolute Python bake). Prefer in-turn evaluation; the stop hook remains a safety net.

Keep the plugin tree in sync after editing skill/agents/package sources:

```bash
python scripts/sync-plugin-tree.py
python scripts/sync-plugin-tree.py --check
```

**Individuals** should still use the clone + installer path above. Plugin import does not replace `install-goal.*` for classic `~/.cursor/skills/goal` installs.

License remains **AGPL-3.0-only** for both distribution paths.

## Platform notes

| Platform | Install notes |
|----------|----------------|
| Cursor IDE (Unix/macOS) | Fully supported |
| Cursor IDE (Windows) | Use `install-goal.ps1` only. Writes `stop_hook.cmd` (absolute Python baked in) plus a ~100ms stdout drain delay to mitigate Cursor’s capture race. Prefer in-turn evaluation; set `CURSOR_GOAL_LOG=DEBUG` to write `last-stop-response.json` if diagnosing followups. Re-run the installer after moving/upgrading Python. `install-goal.sh` from Git Bash is refused. |
| Teams marketplace | Import this repo; requires `python3`/`python`/`py` on PATH for the plugin stop hook |
| WSL | Use `./scripts/install-goal.sh` inside WSL with a WSL home for WSL Cursor. Do not point `$HOME` at `/mnt/c/...` for native Windows Cursor — use `install-goal.ps1` instead. |

## Contributor install

```bash
pip install -e ".[dev]"
pytest tests -q
# or: py -3 scripts/verify.py
cursor-goal manage status
```
