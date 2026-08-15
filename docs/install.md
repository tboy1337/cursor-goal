# Install cursor-goal

cursor-goal is a **Cursor IDE port of OpenAI Codex `/goal`**. Install it into Cursor; Cursor has no Codex-style idle injection, so this package continues via Cursor hooks plus wake.

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

After install, **restart Cursor** (or reload hooks) so `hooks.json` takes effect, then run `manage doctor` before the first `/goal`.

## What gets installed

| Path | Purpose |
|------|---------|
| `~/.cursor/skills/goal/SKILL.md` | Agent skill protocol |
| `~/.cursor/skills/goal/cursor_goal/` | Python package (vendored) |
| `~/.cursor/skills/goal/scripts/run_goal.py` | CLI entry for agents |
| `~/.cursor/skills/goal/scripts/stop_hook.py` | Cursor stop hook |
| `~/.cursor/skills/goal/scripts/stop_hook.cmd` | Windows stop launcher (classic install: absolute Python baked by `install-goal.ps1`) |
| `~/.cursor/skills/goal/scripts/wake_loop.cmd` | Windows wake launcher (classic install: absolute Python baked, same as stop) |
| `~/.cursor/skills/goal/scripts/wake_loop.sh` | Unix/macOS wake launcher (**required** while pursuing; agents may also call `run_goal.py wake loop` directly) |
| `~/.cursor/skills/goal/VERSION` | Installed package version stamp |
| `~/.cursor/agents/goalKeeper.md` | Worker agent (`model: inherit`) |
| `~/.cursor/agents/goal-evaluator.md` | Readonly evaluator (`model: composer-2.5` default) |
| `~/.cursor/agents/goal-auditor.md` | Readonly remaining-work auditor (`model: inherit`) |
| `~/.cursor/hooks.json` | `stop` **and** `subagentStop` (`matcher: goal-evaluator` and `matcher: goal-auditor`) hook registration, both using the same launcher. Unix: `<absolute-python> -u …/stop_hook.py`. Windows: absolute `…/stop_hook.cmd` (cmd launcher). `loop_limit: null`, `timeout: 30`. Prior file is copied to a timestamped `.bak.<UTC>` |
| `~/.cursor-goal/data/` | Runtime state (`goal.json`, `goal-eval-done`) — trusted-user local state (≡ shell trust) |

Override data directory with `CURSOR_GOAL_DATA` (must be an absolute path; relative paths are rejected).

Override the resolved skill root (where `scripts/run_goal.py` is expected) with `CURSOR_GOAL_HOME` (must be an absolute path). This takes priority over both the package-parent layout check and `CURSOR_PLUGIN_ROOT` — use it when you've placed the skill tree somewhere other than `~/.cursor/skills/goal` or a marketplace plugin cache path. Most installs never need to set this; it exists for custom layouts and CI fixtures. `manage harness-cmd` prints the currently resolved `skill_root` (and this env var's raw value) so you can confirm which path won.

Override evaluator model with `CURSOR_GOAL_EVAL_MODEL` (default `composer-2.5`; must be `inherit` or a real [Cursor model ID](https://cursor.com/docs/subagents.md#model-configuration) — `fast` is not one). See [platform-compatibility.md](platform-compatibility.md).

On upgrade, a previous skill tree is copied to `~/.cursor/skills/goal.bak.<UTC>` before replace. If hook merge fails after files are copied, the installer restores that backup automatically when one exists.

`pip install -e .` provides the `cursor-goal` console script for contributors; it does **not** install the Cursor skill, agents, or hooks. Use the scripts above for Cursor.

### Install from a tagged release

Package version **4.4.2** pins the clone branch below. Use it when tag `v4.4.2` exists on [GitHub Releases](https://github.com/tboy1337/cursor-goal/releases). If `git clone --branch` fails, clone `main` with the Quick install steps above ([release.md](release.md)).

```bash
git clone --branch v4.4.2 https://github.com/tboy1337/cursor-goal.git
cd cursor-goal
./scripts/install-goal.sh   # or install-goal.ps1 on Windows
```

Or download the source archive from the GitHub Release for that tag and run the same installer from the extracted tree.

## Verify

Unix / macOS / WSL:

```bash
python3 -u ~/.cursor/skills/goal/scripts/run_goal.py manage status
python3 -u ~/.cursor/skills/goal/scripts/run_goal.py eval spawn-config
python3 -u ~/.cursor/skills/goal/scripts/run_goal.py eval audit-spawn-config
python3 -u ~/.cursor/skills/goal/scripts/run_goal.py manage doctor
```

Windows PowerShell:

```powershell
py -3 -u "$env:USERPROFILE\.cursor\skills\goal\scripts\run_goal.py" manage status
py -3 -u "$env:USERPROFILE\.cursor\skills\goal\scripts\run_goal.py" eval spawn-config
py -3 -u "$env:USERPROFILE\.cursor\skills\goal\scripts\run_goal.py" eval audit-spawn-config
py -3 -u "$env:USERPROFILE\.cursor\skills\goal\scripts\run_goal.py" manage doctor
```

Expected status: `[goal] No active goal.`  
Expected spawn-config: JSON with `"subagent_type":"goal-evaluator"` and `"model":"composer-2.5"` (unless overridden).  
Expected audit-spawn-config: JSON with `"subagent_type":"goal-auditor"` and `"model":"inherit"`.  
Doctor should print `Doctor: OK` (fix any `FAIL` lines before starting a goal).

Then in Agent chat: `/goal status`

If continuation stalls after install, see [troubleshooting.md](troubleshooting.md) and [known-limitations.md](known-limitations.md).

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

The plugin ships skill, agents, vendored harness, and marketplace hooks that register both `stop_hook.cmd` (Windows via `cmd /c`) and `python3 -u "…/stop_hook.py"` (Unix) for **both** the `stop` and `subagentStop` (`matcher: goal-evaluator` and `matcher: goal-auditor`) events — `cmd_stop()` dispatches between the two event shapes based on the JSON payload (`subagentStop` payloads carry `subagent_type`). On each OS one entry per event typically fails (expected). A singleflight lock plus a `generation_id`-keyed dedupe stamp ensure only one hook instance mutates turn state and writes stdout per turn; the loser exits silently (no `{}`, no `last-stop-response.json` overwrite). Prefer in-turn evaluation; the two hooks are the documented continuation safety net (see [known-limitations.md](known-limitations.md)). Classic `install-goal.ps1` still writes a single absolute `stop_hook.cmd` and `wake_loop.cmd` (best path on native Windows).

Resolve harness commands with `manage harness-cmd` (works from `${CURSOR_PLUGIN_ROOT}/skills/goal` without a classic install). Do **not** stack classic installer hooks with marketplace hooks — pick one path. `manage doctor` **FAIL**s when both look configured.

### Three `stop_hook.cmd` roles (do not conflate)

| Variant | Where | Role |
|---------|-------|------|
| Source PATH template | `.cursor/skills/goal/scripts/stop_hook.cmd` | PATH/`CURSOR_GOAL_PYTHON` discovery; not what classic Windows install leaves on disk |
| Classic install bake | Written by `install-goal.ps1` → `~/.cursor/skills/goal/scripts/stop_hook.cmd` | Absolute Python + `stop_hook.py` (re-run installer after moving Python) |
| Marketplace / plugin | Regenerated by `sync-plugin-tree.py` under `plugins/…/stop_hook.cmd` | `CURSOR_GOAL_PYTHON` then PATH discovery (`py`/`python`/`python3`) for Teams plugin installs |

Do **not** hand-edit diverging copies — regenerate via installer or `sync-plugin-tree.py`.

**Windows:** Marketplace dual-entry hooks can resolve Python via absolute `CURSOR_GOAL_PYTHON` or PATH, but **`manage doctor` FAILs** on Windows marketplace installs unless `CURSOR_GOAL_PYTHON` is set to an **absolute** Python 3.12+ path — PATH fallback alone is not treated as success. Individuals should prefer `install-goal.ps1` for an absolute interpreter bake and the stdout drain mitigation. This plugin is **AGPL-3.0-only** — see [known-limitations.md](known-limitations.md#license-teams--redistribution).

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
| Cursor IDE (Unix/macOS) | Supported — harness unit-tested; `stop`/`subagentStop` hooks work but Cursor may still drop `followup_message` stdout (upstream race). **Recommended:** arm `wake loop` with `notify_on_output` matching `^AGENT_GOAL_WAKE FOLLOWUP_REQUIRED pursuing spawn_goal-auditor` while pursuing as a best-effort supplement (see [known-limitations.md](known-limitations.md)). |
| Cursor IDE (Windows) | Use `install-goal.ps1` only. Writes absolute-baked `stop_hook.cmd` and `wake_loop.cmd`, plus a ~250ms stdout drain delay to mitigate Cursor’s capture race. Always writes redacted `last-stop-response.json`. Prefer in-turn evaluation and the documented hooks; arming `wake loop` with `notify_on_output` is a recommended, best-effort supplement (see [cursor-windows-stop-hook-race.md](cursor-windows-stop-hook-race.md)). Re-run the installer after moving/upgrading Python. `install-goal.sh` from Git Bash is refused. |
| Teams marketplace | Import this repo; dual stop entries (`stop_hook.cmd` + `python3`) with singleflight. Set absolute `CURSOR_GOAL_PYTHON` on Windows, or prefer classic `install-goal.ps1` for individuals. Do not stack with classic hooks. |
| WSL | Use `./scripts/install-goal.sh` inside WSL with a WSL home for WSL Cursor. Do not point `$HOME` at `/mnt/c/...` for native Windows Cursor — use `install-goal.ps1` instead. |

## Contributor install

```bash
pip install -e ".[dev]"
pytest tests -q
# or: py -3 scripts/verify.py
cursor-goal manage status
```
