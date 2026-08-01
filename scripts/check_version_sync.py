#!/usr/bin/env python3
"""Fail if package / docs / plugin versions are out of sync.

Checks:
  - pyproject.toml ``version`` == ``cursor_goal.__version__``
  - docs/install.md tagged clone pin ``vX.Y.Z`` matches (when present)
  - README.md tagged clone pin ``vX.Y.Z`` matches (when present)
  - plugins/cursor-goal/.cursor-plugin/plugin.json ``version`` matches (when present)
  - .cursor-plugin/marketplace.json plugin entry version matches (when present)
"""

from __future__ import annotations

import json
import re
import sys
from pathlib import Path

_TAGGED_CLONE_PIN = re.compile(r"git clone --branch v(\d+\.\d+\.\d+)")


def _read_pyproject_version(root: Path) -> str:
    text = (root / "pyproject.toml").read_text(encoding="utf-8")
    match = re.search(r'^version\s*=\s*"([^"]+)"', text, re.MULTILINE)
    if match is None:
        raise ValueError("Could not parse version from pyproject.toml")
    return match.group(1)


def _read_init_version(root: Path) -> str:
    text = (root / "src" / "cursor_goal" / "__init__.py").read_text(encoding="utf-8")
    match = re.search(r'__version__\s*=\s*"([^"]+)"', text)
    if match is None:
        raise ValueError("Could not parse __version__ from __init__.py")
    return match.group(1)


def _read_tagged_clone_pin(path: Path, *, label: str) -> str | None:
    """Return the unique ``git clone --branch vX.Y.Z`` pin from *path*, if any."""
    if not path.is_file():
        return None
    text = path.read_text(encoding="utf-8")
    matches = _TAGGED_CLONE_PIN.findall(text)
    if not matches:
        return None
    unique = set(matches)
    if len(unique) != 1:
        raise ValueError(f"Conflicting {label} version pins: {sorted(unique)}")
    return next(iter(unique))


def _read_docs_pin(root: Path) -> str | None:
    return _read_tagged_clone_pin(root / "docs" / "install.md", label="docs")


def _read_readme_pin(root: Path) -> str | None:
    return _read_tagged_clone_pin(root / "README.md", label="README")


def _read_plugin_version(root: Path) -> str | None:
    path = root / "plugins" / "cursor-goal" / ".cursor-plugin" / "plugin.json"
    if not path.is_file():
        return None
    data = json.loads(path.read_text(encoding="utf-8"))
    version = data.get("version")
    if not isinstance(version, str) or not version:
        raise ValueError(f"Missing version in {path}")
    return version


def _read_marketplace_version(root: Path) -> str | None:
    path = root / ".cursor-plugin" / "marketplace.json"
    if not path.is_file():
        return None
    data = json.loads(path.read_text(encoding="utf-8"))
    plugins = data.get("plugins")
    if not isinstance(plugins, list) or not plugins:
        raise ValueError(f"No plugins listed in {path}")
    versions: set[str] = set()
    for entry in plugins:
        if not isinstance(entry, dict):
            continue
        ver = entry.get("version")
        if isinstance(ver, str) and ver:
            versions.add(ver)
    if not versions:
        return None
    if len(versions) != 1:
        raise ValueError(f"Conflicting marketplace versions: {sorted(versions)}")
    return next(iter(versions))


def _read_marketplace_metadata_version(root: Path) -> str | None:
    path = root / ".cursor-plugin" / "marketplace.json"
    if not path.is_file():
        return None
    data = json.loads(path.read_text(encoding="utf-8"))
    metadata = data.get("metadata")
    if not isinstance(metadata, dict):
        return None
    version = metadata.get("version")
    if version is None:
        return None
    if not isinstance(version, str) or not version:
        raise ValueError(f"Invalid metadata.version in {path}")
    return version


def _read_skill_version_file(root: Path) -> str | None:
    path = root / "plugins" / "cursor-goal" / "skills" / "goal" / "VERSION"
    if not path.is_file():
        return None
    version = path.read_text(encoding="utf-8").strip()
    if not version:
        raise ValueError(f"Empty VERSION file at {path}")
    return version


def main() -> int:
    root = Path(__file__).resolve().parents[1]
    errors: list[str] = []
    try:
        proj = _read_pyproject_version(root)
        init = _read_init_version(root)
    except ValueError as exc:
        print(str(exc), file=sys.stderr)
        return 1

    if proj != init:
        errors.append(f"pyproject={proj} init={init}")

    try:
        docs = _read_docs_pin(root)
    except ValueError as exc:
        errors.append(str(exc))
        docs = None
    if docs is not None and docs != proj:
        errors.append(f"docs pin v{docs} != package {proj}")

    try:
        readme = _read_readme_pin(root)
    except ValueError as exc:
        errors.append(str(exc))
        readme = None
    if readme is not None and readme != proj:
        errors.append(f"README pin v{readme} != package {proj}")

    try:
        plugin = _read_plugin_version(root)
    except ValueError as exc:
        errors.append(str(exc))
        plugin = None
    if plugin is not None and plugin != proj:
        errors.append(f"plugin.json={plugin} != package {proj}")

    try:
        market = _read_marketplace_version(root)
    except ValueError as exc:
        errors.append(str(exc))
        market = None
    if market is not None and market != proj:
        errors.append(f"marketplace={market} != package {proj}")

    try:
        market_meta = _read_marketplace_metadata_version(root)
    except ValueError as exc:
        errors.append(str(exc))
        market_meta = None
    if market_meta is not None and market_meta != proj:
        errors.append(f"marketplace.metadata={market_meta} != package {proj}")

    try:
        skill_ver = _read_skill_version_file(root)
    except ValueError as exc:
        errors.append(str(exc))
        skill_ver = None
    if skill_ver is not None and skill_ver != proj:
        errors.append(f"plugins/.../VERSION={skill_ver} != package {proj}")

    if errors:
        print("version mismatch:", file=sys.stderr)
        for item in errors:
            print(f"  - {item}", file=sys.stderr)
        return 1

    extras = []
    if docs is not None:
        extras.append(f"docs=v{docs}")
    if readme is not None:
        extras.append(f"README=v{readme}")
    if plugin is not None:
        extras.append(f"plugin={plugin}")
    if market is not None:
        extras.append(f"marketplace={market}")
    if market_meta is not None:
        extras.append(f"metadata={market_meta}")
    if skill_ver is not None:
        extras.append(f"VERSION={skill_ver}")
    suffix = f" ({', '.join(extras)})" if extras else ""
    print(f"version OK {proj}{suffix}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
