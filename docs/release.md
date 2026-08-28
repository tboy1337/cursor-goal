# Releasing cursor-goal

Checklist for cutting a tagged GitHub Release (`vX.Y.Z`).

Current package version is **5.1.7**. The public GitHub tag for this release is **`v5.1.7`**. After tagging, README/install pins work with `git clone --branch v5.1.7`.

## Manual Cursor IDE smoke (before pushing a version bump)

Harness unit tests do not cover the IDE. After install, smoke in Cursor:

1. `/cursor-goal` create with an argv-safe `--test` (e.g. `py -3 -c "raise SystemExit(0)"`)
2. Parse `GOAL_WAKE_REQUIRED`; start its `command` in a background Shell with `notify_on_output` matching `^AGENT_GOAL_WAKE FOLLOWUP_REQUIRED pursuing spawn_goal-auditor` (re-handshake existing loops so they attach this longer pattern)
3. Confirm `wake status` shows `continuation_ready=true` / `pid_alive=true`
4. Run `eval validate` (clears any prior CLEAR + YES), spawn a **new** `goal-auditor` (`eval parse-audit` CLEAR on the current tree), then `goal-evaluator`, `manage done` on matching CLEAR + YES
5. On Windows: if Hooks UI shows `{}` while `last-stop-response.json` has `followup_message`, confirm wake still continues the goal

Non-IDE wake smoke: `python scripts/wake-smoke.py` (also run by `scripts/verify.py`)

## Prerequisites

- Working tree clean
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

   On Windows before pushing (or rely on the CI Windows jobs):

   ```powershell
   powershell -NoProfile -ExecutionPolicy Bypass -File .\scripts\run-powershell-tests.ps1
   powershell -NoProfile -ExecutionPolicy Bypass -File .\scripts\install-smoke.ps1
   ```

4. **Commit** the version bump, plugin sync, and docs pins.

5. **Push `main`** (do not also push a `vX.Y.Z` tag on the same bump unless you want the tag-triggered [Release workflow](../.github/workflows/release.yml) to race CI — both paths skip if the GitHub Release already exists, but the extra run is noisy)

   ```bash
   git push origin main
   ```

6. [CI](../.github/workflows/ci.yml) compares this push’s `pyproject.toml` version to `github.event.before`. When the version changed, all CI jobs are green, and `vX.Y.Z` is not already a GitHub Release, the **Publish GitHub Release** job builds the sdist/wheel, writes `SHA256SUMS.txt`, generates a [Sigstore-signed build provenance attestation](https://docs.github.com/en/actions/how-tos/secure-your-work/use-artifact-attestations/use-artifact-attestations), and runs `gh release create vX.Y.Z --target <sha>` (that creates the git tag so `git clone --branch vX.Y.Z` works). `GITHUB_TOKEN` cannot start a second workflow by pushing a tag, so publish happens in the same CI run.

   **Fallback:** a human `git tag -a vX.Y.Z -m "cursor-goal vX.Y.Z"` + `git push origin vX.Y.Z` still runs [release.yml](../.github/workflows/release.yml) (full Linux/Windows/macOS gates, then the same publish steps). The tag must equal `v` + package version exactly.

   Verify a downloaded artifact's provenance after release:

   ```bash
   gh attestation verify cursor_goal-X.Y.Z-py3-none-any.whl --owner tboy1337
   ```

Pushes to `testing`, pull requests, and commits that do not change the pyproject version do not publish. A forgotten `__init__.py` / pin bump still fails `check_version_sync.py` in CI, so there is no release.

## Do not

- Edit `plugins/cursor-goal/**` or `.cursor-plugin/marketplace.json` by hand — always regenerate via `sync-plugin-tree.py`
- Hand-edit `.cursor/skills/cursor-goal/scripts/stop_hook.cmd` expecting it to match the installer bake or marketplace variant — those are three intentional roles (see [install.md](install.md))
- Push a tag that does not match `__version__` (the tag-triggered workflow fails intentionally)
- Skip `verify.py` before pushing a version bump
