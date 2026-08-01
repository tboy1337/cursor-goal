#!/usr/bin/env python3
"""Sync plugins/cursor-goal from installer sources of truth.

Source of truth:
  - src/cursor_goal/
  - .cursor/skills/goal/ (SKILL.md + scripts)
  - .cursor/agents/

Generated:
  - plugins/cursor-goal/** (skill, agents, hooks, vendored package)
  - .cursor-plugin/marketplace.json
  - plugins/cursor-goal/.cursor-plugin/plugin.json

Usage:
  python scripts/sync-plugin-tree.py          # write
  python scripts/sync-plugin-tree.py --check  # exit 1 if drift
"""

from __future__ import annotations

import argparse
import filecmp
import json
import re
import shutil
import sys
import tempfile
from pathlib import Path

PLUGIN_NAME = "cursor-goal"
MARKER = "cursor_goal_stop_hook"


def repo_root() -> Path:
    return Path(__file__).resolve().parents[1]


def package_version(root: Path) -> str:
    text = (root / "src" / "cursor_goal" / "__init__.py").read_text(encoding="utf-8")
    match = re.search(r'__version__\s*=\s*"([^"]+)"', text)
    if match is None:
        raise SystemExit("Could not parse __version__")
    return match.group(1)


def _clear_dir(path: Path) -> None:
    if path.exists():
        shutil.rmtree(path)
    path.mkdir(parents=True, exist_ok=True)


def _copy_tree(src: Path, dest: Path) -> None:
    if dest.exists():
        shutil.rmtree(dest)
    shutil.copytree(src, dest)
    for cache in dest.rglob("__pycache__"):
        if cache.is_dir():
            shutil.rmtree(cache, ignore_errors=True)


def write_plugin(root: Path) -> Path:
    version = package_version(root)
    plugin_root = root / "plugins" / PLUGIN_NAME
    skill_src = root / ".cursor" / "skills" / "goal"
    agents_src = root / ".cursor" / "agents"
    pkg_src = root / "src" / "cursor_goal"

    skill_dest = plugin_root / "skills" / "goal"
    agents_dest = plugin_root / "agents"
    hooks_dest = plugin_root / "hooks"
    manifest_dir = plugin_root / ".cursor-plugin"

    skill_dest.mkdir(parents=True, exist_ok=True)
    (skill_dest / "scripts").mkdir(parents=True, exist_ok=True)
    agents_dest.mkdir(parents=True, exist_ok=True)
    hooks_dest.mkdir(parents=True, exist_ok=True)
    manifest_dir.mkdir(parents=True, exist_ok=True)

    shutil.copy2(skill_src / "SKILL.md", skill_dest / "SKILL.md")
    shutil.copy2(
        skill_src / "scripts" / "run_goal.py", skill_dest / "scripts" / "run_goal.py"
    )
    shutil.copy2(
        skill_src / "scripts" / "stop_hook.py", skill_dest / "scripts" / "stop_hook.py"
    )
    # PATH-based Windows launcher (no absolute Python bake) for plugin installs.
    (skill_dest / "scripts" / "stop_hook.cmd").write_text(
        "\r\n".join(
            [
                "@echo off",
                "setlocal",
                "set PYTHONUNBUFFERED=1",
                "where py >nul 2>&1",
                "if %ERRORLEVEL%==0 (",
                '  py -3 -u "%~dp0stop_hook.py"',
                "  exit /b %ERRORLEVEL%",
                ")",
                "where python >nul 2>&1",
                "if %ERRORLEVEL%==0 (",
                '  python -u "%~dp0stop_hook.py"',
                "  exit /b %ERRORLEVEL%",
                ")",
                "where python3 >nul 2>&1",
                "if %ERRORLEVEL%==0 (",
                '  python3 -u "%~dp0stop_hook.py"',
                "  exit /b %ERRORLEVEL%",
                ")",
                "echo [cursor-goal] Python 3.12+ not found on PATH >&2",
                "exit /b 1",
                "",
            ]
        ),
        encoding="utf-8",
        newline="\n",
    )
    _copy_tree(pkg_src, skill_dest / "cursor_goal")
    (skill_dest / "VERSION").write_text(version + "\n", encoding="utf-8")

    shutil.copy2(agents_src / "goalKeeper.md", agents_dest / "goalKeeper.md")
    shutil.copy2(agents_src / "goal-evaluator.md", agents_dest / "goal-evaluator.md")

    hooks = {
        "version": 1,
        "hooks": {
            "stop": [
                {
                    # Prefer python3 on Unix; Windows plugin users should use
                    # stop_hook.cmd via the alternate entry below or ensure python on PATH.
                    "command": (
                        "python3 -u "
                        "${CURSOR_PLUGIN_ROOT}/skills/goal/scripts/stop_hook.py"
                    ),
                    "loop_limit": None,
                    "timeout": 30,
                    "_cursor_goal": MARKER,
                }
            ]
        },
    }
    (hooks_dest / "hooks.json").write_text(
        json.dumps(hooks, indent=2) + "\n", encoding="utf-8"
    )

    plugin_manifest = {
        "name": PLUGIN_NAME,
        "version": version,
        "description": (
            "Autonomous /goal loop for Cursor IDE "
            "(multi-model maker!=checker + stop-hook safety net)"
        ),
        "author": {"name": "tboy1337"},
        "homepage": "https://github.com/tboy1337/cursor-goal",
        "repository": "https://github.com/tboy1337/cursor-goal",
        "license": "AGPL-3.0-only",
        "keywords": ["goal", "agent", "automation", "cursor"],
        "skills": "./skills/",
        "agents": "./agents/",
        "hooks": "./hooks/hooks.json",
    }
    (manifest_dir / "plugin.json").write_text(
        json.dumps(plugin_manifest, indent=2) + "\n", encoding="utf-8"
    )

    market_dir = root / ".cursor-plugin"
    market_dir.mkdir(parents=True, exist_ok=True)
    marketplace = {
        "name": "cursor-goal-marketplace",
        "owner": {"name": "tboy1337"},
        "metadata": {
            "description": "cursor-goal autonomous /goal loop plugin",
            "version": version,
            "pluginRoot": "plugins",
        },
        "plugins": [
            {
                "name": PLUGIN_NAME,
                "source": PLUGIN_NAME,
                "description": plugin_manifest["description"],
                "version": version,
                "license": "AGPL-3.0-only",
                "keywords": plugin_manifest["keywords"],
            }
        ],
    }
    (market_dir / "marketplace.json").write_text(
        json.dumps(marketplace, indent=2) + "\n", encoding="utf-8"
    )

    readme = plugin_root / "README.md"
    readme.write_text(
        "# cursor-goal (Cursor plugin)\n\n"
        "Teams/Enterprise: import this repository as a Team Marketplace "
        "(see repo `.cursor-plugin/marketplace.json`).\n\n"
        "Individuals: prefer `scripts/install-goal.sh` / `install-goal.ps1` "
        "from a full clone or GitHub Release.\n\n"
        f"Version: **{version}** (AGPL-3.0-only).\n\n"
        "Stop hook uses `${CURSOR_PLUGIN_ROOT}` and `python3` on PATH "
        "(Unix/Teams-oriented). On native Windows prefer `install-goal.ps1`, "
        "which writes `stop_hook.cmd` with an absolute interpreter. "
        "In-turn evaluation remains primary; the stop hook is a safety net.\n",
        encoding="utf-8",
    )
    return plugin_root


def _files_to_compare(plugin_root: Path) -> list[Path]:
    """Return critical single-file paths under the plugin root."""
    paths: list[Path] = []
    for pattern in (
        "skills/goal/SKILL.md",
        "skills/goal/scripts/run_goal.py",
        "skills/goal/scripts/stop_hook.py",
        "skills/goal/scripts/stop_hook.cmd",
        "skills/goal/VERSION",
        "agents/goalKeeper.md",
        "agents/goal-evaluator.md",
        "hooks/hooks.json",
        ".cursor-plugin/plugin.json",
        "README.md",
    ):
        paths.append(plugin_root / pattern)
    return paths


def _iter_vendored_files(pkg_root: Path) -> list[Path]:
    """List files under the vendored cursor_goal package (skip __pycache__)."""
    if not pkg_root.is_dir():
        return []
    files: list[Path] = []
    for path in sorted(pkg_root.rglob("*")):
        if not path.is_file():
            continue
        if "__pycache__" in path.parts:
            continue
        files.append(path)
    return files


def _compare_file(left: Path, right: Path, rel_key: Path) -> str | None:
    """Return a mismatch label, or None when files match."""
    if not right.is_file():
        return f"missing: {rel_key.as_posix()}"
    if left.suffix == ".json":
        left_data = json.loads(left.read_text(encoding="utf-8"))
        right_data = json.loads(right.read_text(encoding="utf-8"))
        if left_data != right_data:
            return f"drift: {rel_key.as_posix()}"
        return None
    if not filecmp.cmp(left, right, shallow=False):
        return f"drift: {rel_key.as_posix()}"
    return None


def check_plugin(root: Path) -> int:
    """Rebuild into a temp tree and compare critical files + vendored package."""
    version = package_version(root)
    plugin_root = root / "plugins" / PLUGIN_NAME
    if not plugin_root.is_dir():
        print("plugins/cursor-goal missing; run sync-plugin-tree.py", file=sys.stderr)
        return 1
    market = root / ".cursor-plugin" / "marketplace.json"
    if not market.is_file():
        print("marketplace.json missing", file=sys.stderr)
        return 1

    with tempfile.TemporaryDirectory(prefix="cg-plugin-check-") as tmp:
        # Write into a fake root that mirrors layout.
        fake = Path(tmp)
        (fake / "src").mkdir()
        shutil.copytree(root / "src" / "cursor_goal", fake / "src" / "cursor_goal")
        shutil.copytree(root / ".cursor", fake / ".cursor")
        write_plugin(fake)
        expected = fake / "plugins" / PLUGIN_NAME
        mismatches: list[str] = []
        for rel in _files_to_compare(expected):
            rel_key = rel.relative_to(expected)
            left = expected / rel_key
            right = plugin_root / rel_key
            label = _compare_file(left, right, rel_key)
            if label is not None:
                mismatches.append(label)

        expected_pkg = expected / "skills" / "goal" / "cursor_goal"
        actual_pkg = plugin_root / "skills" / "goal" / "cursor_goal"
        expected_files = {
            p.relative_to(expected_pkg).as_posix(): p
            for p in _iter_vendored_files(expected_pkg)
        }
        actual_files = {
            p.relative_to(actual_pkg).as_posix(): p
            for p in _iter_vendored_files(actual_pkg)
        }
        for rel_posix, left in expected_files.items():
            right = actual_files.get(rel_posix)
            if right is None:
                mismatches.append(f"missing: skills/goal/cursor_goal/{rel_posix}")
                continue
            label = _compare_file(
                left, right, Path("skills/goal/cursor_goal") / rel_posix
            )
            if label is not None:
                mismatches.append(label)
        for rel_posix in sorted(set(actual_files) - set(expected_files)):
            mismatches.append(f"extra: skills/goal/cursor_goal/{rel_posix}")

        market_data = json.loads(market.read_text(encoding="utf-8"))
        if market_data.get("metadata", {}).get("version") != version:
            mismatches.append("marketplace metadata.version drift")
        plugins = market_data.get("plugins") or []
        if not plugins or plugins[0].get("version") != version:
            mismatches.append("marketplace plugin version drift")
        pkg = json.loads(
            (plugin_root / ".cursor-plugin" / "plugin.json").read_text(encoding="utf-8")
        )
        if pkg.get("version") != version:
            mismatches.append("plugin.json version drift")
        if mismatches:
            print("plugin tree out of sync:", file=sys.stderr)
            for item in mismatches:
                print(f"  - {item}", file=sys.stderr)
            print("Run: python scripts/sync-plugin-tree.py", file=sys.stderr)
            return 1
    print(f"plugin tree OK (version {version})")
    return 0


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--check",
        action="store_true",
        help="Verify plugins/cursor-goal matches sources (no write).",
    )
    args = parser.parse_args(argv)
    root = repo_root()
    if args.check:
        return check_plugin(root)
    path = write_plugin(root)
    print(f"Synced plugin tree -> {path}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
