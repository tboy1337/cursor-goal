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
SUBAGENT_STOP_MARKER = "cursor_goal_subagent_stop_hook"
AUDIT_SUBAGENT_STOP_MARKER = "cursor_goal_subagent_audit_stop_hook"
SUBAGENT_STOP_MATCHER = "goal-evaluator"
AUDIT_SUBAGENT_STOP_MATCHER = "goal-auditor"


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
    _clear_dir(skill_dest / "scripts")
    agents_dest.mkdir(parents=True, exist_ok=True)
    hooks_dest.mkdir(parents=True, exist_ok=True)
    manifest_dir.mkdir(parents=True, exist_ok=True)

    shutil.copy2(skill_src / "SKILL.md", skill_dest / "SKILL.md")
    for script_name in (
        "run_goal.py",
        "stop_hook.py",
        "stop_hook.cmd",
        "wake_loop.sh",
        "wake_loop.cmd",
    ):
        src = skill_src / "scripts" / script_name
        if src.is_file():
            shutil.copy2(src, skill_dest / "scripts" / script_name)
    _copy_tree(pkg_src, skill_dest / "cursor_goal")
    # newline="\n" forces LF on Windows so --check matches git (eol=lf).
    (skill_dest / "VERSION").write_text(version + "\n", encoding="utf-8", newline="\n")

    shutil.copy2(agents_src / "goalKeeper.md", agents_dest / "goalKeeper.md")
    shutil.copy2(agents_src / "goal-evaluator.md", agents_dest / "goal-evaluator.md")
    shutil.copy2(agents_src / "goal-auditor.md", agents_dest / "goal-auditor.md")

    plugin_root_var = "${CURSOR_PLUGIN_ROOT}/skills/goal/scripts"
    # The same stop_hook.cmd / stop_hook.py launcher handles both event
    # shapes at runtime (cmd_stop dispatches on the "subagent_type" key), so
    # subagentStop reuses the identical commands, scoped via "matcher" plus a
    # defensive subagent_type check inside handle_subagent_stop().
    hooks = {
        "version": 1,
        "hooks": {
            "stop": [
                {
                    "command": (f'cmd /c "{plugin_root_var}/stop_hook.cmd"'),
                    "loop_limit": None,
                    "timeout": 30,
                    "_cursor_goal": MARKER,
                },
                {
                    "command": (f'python3 -u "{plugin_root_var}/stop_hook.py"'),
                    "loop_limit": None,
                    "timeout": 30,
                    "_cursor_goal": MARKER,
                },
            ],
            "subagentStop": [
                {
                    "command": (f'cmd /c "{plugin_root_var}/stop_hook.cmd"'),
                    "loop_limit": None,
                    "timeout": 30,
                    "matcher": SUBAGENT_STOP_MATCHER,
                    "_cursor_goal": SUBAGENT_STOP_MARKER,
                },
                {
                    "command": (f'python3 -u "{plugin_root_var}/stop_hook.py"'),
                    "loop_limit": None,
                    "timeout": 30,
                    "matcher": SUBAGENT_STOP_MATCHER,
                    "_cursor_goal": SUBAGENT_STOP_MARKER,
                },
                {
                    "command": (f'cmd /c "{plugin_root_var}/stop_hook.cmd"'),
                    "loop_limit": None,
                    "timeout": 30,
                    "matcher": AUDIT_SUBAGENT_STOP_MATCHER,
                    "_cursor_goal": AUDIT_SUBAGENT_STOP_MARKER,
                },
                {
                    "command": (f'python3 -u "{plugin_root_var}/stop_hook.py"'),
                    "loop_limit": None,
                    "timeout": 30,
                    "matcher": AUDIT_SUBAGENT_STOP_MATCHER,
                    "_cursor_goal": AUDIT_SUBAGENT_STOP_MARKER,
                },
            ],
        },
    }
    (hooks_dest / "hooks.json").write_text(
        json.dumps(hooks, indent=2) + "\n", encoding="utf-8", newline="\n"
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
        json.dumps(plugin_manifest, indent=2) + "\n",
        encoding="utf-8",
        newline="\n",
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
        json.dumps(marketplace, indent=2) + "\n",
        encoding="utf-8",
        newline="\n",
    )

    # Ship AGPL license text with the marketplace plugin subtree.
    copying_src = root / "COPYING"
    if copying_src.is_file():
        shutil.copy2(copying_src, plugin_root / "COPYING")

    readme = plugin_root / "README.md"
    readme.write_text(
        "# cursor-goal (Cursor plugin)\n\n"
        "Teams/Enterprise: import this repository as a Team Marketplace "
        "(see repo `.cursor-plugin/marketplace.json`).\n\n"
        "Individuals: prefer `scripts/install-goal.sh` / `install-goal.ps1` "
        "from a full clone or GitHub Release.\n\n"
        f"Version: **{version}** (AGPL-3.0-only). License text ships as "
        "`COPYING` in this plugin tree. Teams/AGPL notes:\n"
        "[docs/teams-agpl.md](https://github.com/tboy1337/cursor-goal/blob/main/docs/teams-agpl.md).\n\n"
        "## Windows marketplace expectations\n\n"
        "Marketplace stop hooks register both `stop_hook.cmd` (Windows) and "
        '`python3 -u "…/stop_hook.py"` (Unix). On each OS one entry typically '
        "fails (cmd missing on Unix / python3 often missing on Windows) — "
        "**expected Hooks UI noise**, not necessarily a broken install. A "
        "singleflight lock ensures only one hook mutates turn state and writes "
        "stdout; the loser exits silently (no `{}`, no "
        "`last-stop-response.json` overwrite). A `generation_id`-keyed dedupe "
        "stamp additionally guards *sequential* dual-hook invocations (one hook "
        "fully finishes, then the other starts for the same turn) from "
        "re-charging `turns_used` or emitting a second followup.\n\n"
        "The same launcher command is also registered for the "
        '`subagentStop` event (`matcher: "goal-evaluator"` and '
        '`matcher: "goal-auditor"`), giving a documented, race-free '
        "continuation point the instant the evaluator or remaining-work "
        "auditor subagent finishes — `cmd_stop` dispatches between the two "
        "event shapes based on whether the JSON payload carries "
        "`subagent_type`.\n\n"
        "`${CURSOR_PLUGIN_ROOT}` is not listed in Cursor's documented hook "
        "environment variables; these hook commands rely on it being set by "
        "the plugin host at invocation time. `stop_hook.py`'s "
        "`_ensure_package_path()` also works if it is unset, by resolving the "
        "vendored `cursor_goal` package relative to its own file location "
        "(`scripts/`, its parent skill dir, or the repo's `src/` in a source "
        "checkout) — the classic `~/.cursor/skills/goal` install path does not "
        "depend on `CURSOR_PLUGIN_ROOT` at all.\n\n"
        "Set `CURSOR_GOAL_PYTHON` to an **absolute** Python 3.12+ path on "
        "Windows Teams installs — `manage doctor` **FAIL**s when marketplace "
        "hooks are detected without it (PATH fallback is fragile and not "
        "treated as success). Individuals should prefer classic "
        "`install-goal.ps1` (absolute interpreter bake). Resolve the "
        "harness with `manage harness-cmd` — skill/agent commands work from "
        "`${CURSOR_PLUGIN_ROOT}/skills/goal` without a classic install.\n\n"
        "Also ships a wake watchdog (`wake loop` / `AGENT_GOAL_WAKE`) for "
        "continuation when Cursor drops stop-hook stdout. In-turn evaluation "
        "remains primary; the stop hook is a safety net.\n\n"
        "Do **not** stack classic `install-goal.*` hooks with marketplace "
        "hooks; `manage doctor` **FAIL**s when both look configured — pick "
        "one path.\n",
        encoding="utf-8",
        newline="\n",
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
        "skills/goal/scripts/wake_loop.cmd",
        "skills/goal/scripts/wake_loop.sh",
        "skills/goal/VERSION",
        "agents/goalKeeper.md",
        "agents/goal-evaluator.md",
        "agents/goal-auditor.md",
        "hooks/hooks.json",
        ".cursor-plugin/plugin.json",
        "README.md",
        "COPYING",
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


def _normalize_newlines(text: str) -> str:
    """Normalize CRLF/CR to LF for cross-platform text comparison."""
    return text.replace("\r\n", "\n").replace("\r", "\n")


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
    # .cmd embeds CRLF payloads intentionally — compare raw bytes.
    if left.suffix.lower() == ".cmd":
        if not filecmp.cmp(left, right, shallow=False):
            return f"drift: {rel_key.as_posix()}"
        return None
    # Text: ignore CRLF vs LF so Windows runners do not false-positive.
    left_text = _normalize_newlines(left.read_text(encoding="utf-8"))
    right_text = _normalize_newlines(right.read_text(encoding="utf-8"))
    if left_text != right_text:
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
        copying = root / "COPYING"
        if copying.is_file():
            shutil.copy2(copying, fake / "COPYING")
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
