#!/usr/bin/env python3
"""Fail if package / docs / plugin versions are out of sync.

Checks:
  - pyproject.toml ``version`` == ``cursor_goal.__version__``
  - docs/install.md tagged clone pin ``vX.Y.Z`` matches (when present)
  - README.md tagged clone pin ``vX.Y.Z`` matches (when present)
  - plugins/cursor-goal/.cursor-plugin/plugin.json ``version`` matches (when present)
  - .cursor-plugin/marketplace.json plugin entry version matches (when present)

``--detect-bump`` compares the current pyproject version to a previous
checkout (CI uses ``github.event.before``) and prints a JSON decision for
auto-release. Default invocation remains the sync check.
"""

from __future__ import annotations

import argparse
import json
import logging
import os
import re
import sys
from collections.abc import Mapping, Sequence
from pathlib import Path

LOG = logging.getLogger("check_version_sync")

_TAGGED_CLONE_PIN = re.compile(r"git clone --branch v(\d+\.\d+\.\d+)")
_PYPROJECT_VERSION = re.compile(r'^version\s*=\s*"([^"]+)"', re.MULTILINE)
_SEMVER = re.compile(r"^\d+\.\d+\.\d+$")

DECISION_RELEASE = "release"
DECISION_UNCHANGED = "unchanged"
DECISION_SKIP_EXISTS = "skip_exists"
MAIN_REF = "refs/heads/main"


def parse_pyproject_version_text(text: str) -> str:
    """Return the ``[project].version`` string from pyproject.toml contents."""
    match = _PYPROJECT_VERSION.search(text)
    if match is None:
        raise ValueError("Could not parse version from pyproject.toml")
    version = match.group(1)
    LOG.debug("parsed pyproject version %s", version)
    return version


def _read_pyproject_version(root: Path) -> str:
    text = (root / "pyproject.toml").read_text(encoding="utf-8")
    return parse_pyproject_version_text(text)


def parse_bool_flag(raw: str) -> bool:
    """Parse a CLI boolean (0/1, true/false, yes/no, on/off)."""
    normalized = raw.strip().lower()
    if normalized in {"1", "true", "yes", "on"}:
        return True
    if normalized in {"0", "false", "no", "off", ""}:
        return False
    raise ValueError(f"invalid boolean {raw!r}")


def version_bump_decision(
    *,
    current: str,
    previous: str | None,
    release_exists: bool,
    event_name: str,
    ref: str,
) -> dict[str, str]:
    """Decide whether CI should cut ``vX.Y.Z`` for this push.

    ``release`` only when the event is a push to ``main``, ``current`` is a
    distinct ``X.Y.Z`` from ``previous``, and that tag/release does not
    already exist. Missing previous (new branch, failed fetch) is
    ``unchanged`` so CI never releases from incomplete history.
    """
    tag = f"v{current}"
    result = {
        "decision": DECISION_UNCHANGED,
        "current": current,
        "previous": previous or "",
        "tag": tag,
    }
    LOG.info(
        "version bump inputs event=%s ref=%s current=%s previous=%s release_exists=%s",
        event_name,
        ref,
        current,
        previous,
        release_exists,
    )
    if event_name != "push" or ref != MAIN_REF:
        LOG.info("decision=unchanged (not a push to main)")
        return result
    if not _SEMVER.fullmatch(current):
        raise ValueError(f"current version is not X.Y.Z: {current!r}")
    if previous is None or previous == "":
        LOG.info("decision=unchanged (no previous version)")
        return result
    if previous == current:
        LOG.info("decision=unchanged (version %s not changed)", current)
        return result
    if release_exists:
        result["decision"] = DECISION_SKIP_EXISTS
        LOG.info("decision=skip_exists (%s already released)", tag)
        return result
    result["decision"] = DECISION_RELEASE
    LOG.info("decision=release %s -> %s (%s)", previous, current, tag)
    return result


def _write_github_output(result: Mapping[str, str]) -> None:
    """Append decision fields to ``GITHUB_OUTPUT`` when running in Actions."""
    path_raw = os.environ.get("GITHUB_OUTPUT")
    if not path_raw:
        LOG.debug("GITHUB_OUTPUT unset; skipping Actions output")
        return
    path = Path(path_raw)
    with path.open("a", encoding="utf-8") as handle:
        for key in ("decision", "tag", "current", "previous"):
            handle.write(f"{key}={result[key]}\n")
    LOG.info("wrote GitHub outputs to %s: %s", path, dict(result))


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
    path = root / "plugins" / "cursor-goal" / "skills" / "cursor-goal" / "VERSION"
    if not path.is_file():
        return None
    version = path.read_text(encoding="utf-8").strip()
    if not version:
        raise ValueError(f"Empty VERSION file at {path}")
    return version


_RELEASE_PROSE = re.compile(
    r"Current package version is \*\*(\d+\.\d+\.\d+)\*\*.*?"
    r"public GitHub tag for this release is \*\*`v(\d+\.\d+\.\d+)`\*\*",
    re.DOTALL,
)


def _read_release_md_versions(root: Path) -> tuple[str, str] | None:
    """Return (package_prose, tag_prose) from docs/release.md when present."""
    path = root / "docs" / "release.md"
    if not path.is_file():
        return None
    text = path.read_text(encoding="utf-8")
    match = _RELEASE_PROSE.search(text)
    if match is None:
        raise ValueError(
            "docs/release.md missing 'Current package version' / public tag prose"
        )
    return match.group(1), match.group(2)


def _cmd_check_sync() -> int:
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

    try:
        release_pair = _read_release_md_versions(root)
    except ValueError as exc:
        errors.append(str(exc))
        release_pair = None
    if release_pair is not None:
        release_pkg, release_tag = release_pair
        if release_pkg != proj:
            errors.append(f"release.md package prose={release_pkg} != package {proj}")
        if release_tag != proj:
            errors.append(f"release.md tag prose=v{release_tag} != package {proj}")

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
    if release_pair is not None:
        extras.append(f"release.md=v{release_pair[1]}")
    suffix = f" ({', '.join(extras)})" if extras else ""
    print(f"version OK {proj}{suffix}")
    return 0


def _cmd_detect_bump(args: argparse.Namespace) -> int:
    root = Path(__file__).resolve().parents[1]
    current_path = args.current_file or (root / "pyproject.toml")
    LOG.info("reading current pyproject from %s", current_path)
    current = parse_pyproject_version_text(current_path.read_text(encoding="utf-8"))
    previous: str | None = None
    if args.previous_file is not None:
        LOG.info("reading previous pyproject from %s", args.previous_file)
        previous = parse_pyproject_version_text(
            args.previous_file.read_text(encoding="utf-8")
        )
    release_exists = parse_bool_flag(args.release_exists)
    result = version_bump_decision(
        current=current,
        previous=previous,
        release_exists=release_exists,
        event_name=args.event_name,
        ref=args.ref,
    )
    _write_github_output(result)
    print(json.dumps(result, sort_keys=True))
    return 0


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="Check version pins, or detect a pyproject version bump."
    )
    parser.add_argument(
        "--detect-bump",
        action="store_true",
        help="Compare current vs previous pyproject version for auto-release",
    )
    parser.add_argument(
        "--current-file",
        type=Path,
        default=None,
        help="pyproject.toml for the current commit (default: repo pyproject.toml)",
    )
    parser.add_argument(
        "--previous-file",
        type=Path,
        default=None,
        help="pyproject.toml from github.event.before (omit if unavailable)",
    )
    parser.add_argument(
        "--event-name",
        default="",
        help="GitHub event name (push, pull_request, ...)",
    )
    parser.add_argument(
        "--ref",
        default="",
        help="GitHub ref (e.g. refs/heads/main)",
    )
    parser.add_argument(
        "--release-exists",
        default="0",
        help="Whether GitHub already has release/tag vX.Y.Z (0/1, true/false)",
    )
    parsed = parser.parse_args(argv)
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s %(levelname)s %(name)s: %(message)s",
    )
    if parsed.detect_bump:
        return _cmd_detect_bump(parsed)
    return _cmd_check_sync()


if __name__ == "__main__":
    raise SystemExit(main())
