# Releasing cursor-goal

Checklist for cutting a tagged GitHub Release (`vX.Y.Z`).

Tagging `v2.7.0` was the first public 2.x release. Current package version is **2.7.0**.

## Manual Cursor IDE smoke (before tagging)

Harness unit tests do not cover the IDE. After install, smoke in Cursor:

1. `/goal` create with an argv-safe `--test` (e.g. `py -3 -c "raise SystemExit(0)"`)
2. Start `wake loop` in a background Shell with `notify_on_output` matching `^AGENT_GOAL_WAKE`
3. Confirm `wake status` shows `pid_alive=true`
4. Run `eval validate`, spawn `goal-evaluator`, `manage done` on YES
5. On Windows: if Hooks UI shows `{}` while `last-stop-response.json` has `followup_message`, confirm wake still continues the goal

## Prerequisites

- Working tree clean and CI green on `main`
- Python 3.12+ with `pip install -e ".[dev]"`

## Steps

1. **Bump the package version only in sources of truth**
   - [`src/cursor_goal/__init__.py`](../src/cursor_goal/__init__.py) (`__version__`)
   - [`pyproject.toml`](../pyproject.toml) (`[project].version`)
   - Update tagged-clone pins in [`docs/install.md`](install.md) and [`README.md`](../README.md) to `vX.Y.Z`

2. **Regenerate the plugin / marketplace tree**

   ```bash
   python scripts/sync-plugin-tree.py
   python scripts/sync-plugin-tree.py --check
   python scripts/check_version_sync.py
   ```

3. **Verify locally**

   ```bash
   py -3 scripts/verify.py
   # or: python scripts/verify.py
   ```

   On Windows before tagging (or rely on the release workflow Windows gate):

   ```powershell
   powershell -NoProfile -ExecutionPolicy Bypass -File .\scripts\run-powershell-tests.ps1
   powershell -NoProfile -ExecutionPolicy Bypass -File .\scripts\install-smoke.ps1
   ```

4. **Commit** the version bump, plugin sync, and docs pins.

5. **Tag and push** (tag must equal `v` + package version exactly)

   ```bash
   git tag -a vX.Y.Z -m "cursor-goal vX.Y.Z"
   git push origin main
   git push origin vX.Y.Z
   ```

6. The [Release workflow](../.github/workflows/release.yml) runs Unix verify + Windows smoke/Pester, then publishes the GitHub Release (source install path; Teams marketplace imports the **git repo/tag**, not a plugin zip).

## Do not

- Edit `plugins/cursor-goal/**` or `.cursor-plugin/marketplace.json` by hand — always regenerate via `sync-plugin-tree.py`
- Hand-edit `.cursor/skills/goal/scripts/stop_hook.cmd` expecting it to match the installer bake or marketplace variant — those are three intentional roles (see [install.md](install.md))
- Push a tag that does not match `__version__` (the workflow fails intentionally)
- Skip `verify.py` before tagging
