# Releasing cursor-goal

Checklist for cutting a tagged GitHub Release (`vX.Y.Z`).

## Prerequisites

- Working tree clean and CI green on `main`
- Python 3.12+ with `pip install -e ".[dev]"`

## Steps

1. **Bump the package version only in sources of truth**
   - [`src/cursor_goal/__init__.py`](../src/cursor_goal/__init__.py) (`__version__`)
   - [`pyproject.toml`](../pyproject.toml) (`[project].version`)
   - Update tagged-clone pins in [`docs/install.md`](install.md) and [`README.md`](../README.md) to `vX.Y.Z`
   - Add a section to [`CHANGELOG.md`](../CHANGELOG.md)

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

4. **Commit** the version bump, plugin sync, changelog, and docs pins.

5. **Tag and push** (tag must equal `v` + package version exactly)

   ```bash
   git tag -a vX.Y.Z -m "cursor-goal vX.Y.Z"
   git push origin main
   git push origin vX.Y.Z
   ```

6. The [Release workflow](../.github/workflows/release.yml) runs version/plugin sync, pytest, coverage metrics, builds sdist/wheel, and creates the GitHub Release with checksums.

## Do not

- Edit `plugins/cursor-goal/**` or `.cursor-plugin/marketplace.json` by hand — always regenerate via `sync-plugin-tree.py`
- Push a tag that does not match `__version__` (the workflow fails intentionally)
- Skip `verify.py` before tagging
