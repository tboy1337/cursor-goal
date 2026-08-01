"""Tests for hooks.json merge helpers used by installers."""

from __future__ import annotations

import importlib.util
import json
import shutil
import subprocess
import sys
from pathlib import Path

import pytest

from cursor_goal import __version__
from cursor_goal.hooks_config import (
    HOOK_MARKER,
    build_stop_entry,
    is_goal_stop_hook,
    merge_stop_hook,
    read_hooks_file,
    remove_stop_hooks,
    write_hooks_file,
)


def test_build_stop_entry_has_unbuffered_marker_fields() -> None:
    entry = build_stop_entry(
        "python3 -u /home/u/.cursor/skills/goal/scripts/stop_hook.py"
    )
    assert "-u" in entry["command"]
    assert "stop_hook.py" in entry["command"]
    assert entry["loop_limit"] is None
    assert entry["timeout"] == 30
    assert entry["_cursor_goal"] == HOOK_MARKER


def test_merge_replaces_legacy_bash_hook(tmp_path: Path) -> None:
    data = {
        "version": 1,
        "hooks": {
            "stop": [
                {"command": "~/.cursor/skills/goal/goal-stop.sh", "timeout": 30},
                {"command": "./other.sh"},
            ]
        },
    }
    entry = build_stop_entry("python3 -u /abs/stop_hook.py")
    merged = merge_stop_hook(data, entry)
    stop = merged["hooks"]["stop"]
    assert len(stop) == 2
    assert stop[0]["command"] == "./other.sh"
    assert is_goal_stop_hook(stop[1])
    assert "-u" in stop[1]["command"]


def test_remove_stop_hooks(tmp_path: Path) -> None:
    path = tmp_path / "hooks.json"
    data = merge_stop_hook(
        {"version": 1, "hooks": {"stop": []}},
        build_stop_entry("py -3 -u C:\\stop_hook.py"),
    )
    write_hooks_file(path, data)
    cleaned = remove_stop_hooks(read_hooks_file(path))
    assert cleaned["hooks"]["stop"] == []


def test_is_goal_stop_hook_variants() -> None:
    assert is_goal_stop_hook("not-a-dict") is False
    assert is_goal_stop_hook({"command": "cursor_goal stop"}) is True
    assert is_goal_stop_hook({"command": "cursor-goal stop"}) is True
    assert is_goal_stop_hook({"command": r"C:\x\stop_hook.cmd"}) is True
    assert is_goal_stop_hook({"_cursor_goal": HOOK_MARKER, "command": "x"}) is True
    assert is_goal_stop_hook({"command": "./unrelated.sh"}) is False
    assert is_goal_stop_hook({"command": "stop_hook.py"}, allow_legacy=False) is False
    assert (
        is_goal_stop_hook(
            {"_cursor_goal": HOOK_MARKER, "command": "x"}, allow_legacy=False
        )
        is True
    )


def test_merge_when_hooks_not_dict() -> None:
    data: dict = {"hooks": "bad"}
    entry = build_stop_entry("python3 -u /abs/stop_hook.py")
    merged = merge_stop_hook(data, entry)
    assert isinstance(merged["hooks"], dict)
    assert len(merged["hooks"]["stop"]) == 1


def test_remove_when_hooks_missing_or_invalid() -> None:
    assert remove_stop_hooks({}) == {}
    data = {"hooks": "bad"}
    assert remove_stop_hooks(data)["hooks"] == "bad"


def test_read_hooks_file_rejects_non_object(tmp_path: Path) -> None:
    path = tmp_path / "hooks.json"
    path.write_text("[1,2]\n", encoding="utf-8")
    with pytest.raises(ValueError, match="object"):
        read_hooks_file(path)


def test_write_hooks_file_atomic_utf8(tmp_path: Path) -> None:
    path = tmp_path / "hooks.json"
    data = merge_stop_hook(
        {"version": 1, "hooks": {"stop": []}},
        build_stop_entry("python3 -u /abs/stop_hook.py"),
    )
    write_hooks_file(path, data)
    raw = path.read_bytes()
    assert not raw.startswith(b"\xef\xbb\xbf")
    assert path.read_text(encoding="utf-8").startswith("{")
    leftovers = list(tmp_path.glob("hooks.json.*.tmp"))
    assert leftovers == []


def test_normalize_stop_object_and_garbage() -> None:
    from cursor_goal.hooks_config import normalize_stop_hooks

    assert normalize_stop_hooks({"command": "./one.sh"}) == [{"command": "./one.sh"}]
    assert normalize_stop_hooks("bad") == []
    assert normalize_stop_hooks([{"command": "a"}, "skip", 1]) == [{"command": "a"}]


def test_merge_stop_when_stop_is_object() -> None:
    data = {
        "version": 1,
        "hooks": {"stop": {"command": "./legacy-single.sh", "timeout": 10}},
    }
    entry = build_stop_entry("python3 -u /abs/stop_hook.py")
    merged = merge_stop_hook(data, entry)
    stop = merged["hooks"]["stop"]
    assert isinstance(stop, list)
    assert any(item.get("command") == "./legacy-single.sh" for item in stop)
    assert is_goal_stop_hook(stop[-1])


def test_merge_hooks_at_path_existing(tmp_path: Path) -> None:
    from cursor_goal.hooks_config import merge_hooks_at_path

    path = tmp_path / "hooks.json"
    write_hooks_file(
        path, {"version": 1, "hooks": {"stop": [{"command": "./keep.sh"}]}}
    )
    merge_hooks_at_path(path, "python3 -u /abs/stop_hook.py")
    data = read_hooks_file(path)
    assert len(data["hooks"]["stop"]) == 2


def test_merge_and_remove_hooks_at_path(tmp_path: Path) -> None:
    from cursor_goal.hooks_config import merge_hooks_at_path, remove_hooks_at_path

    path = tmp_path / "hooks.json"
    merge_hooks_at_path(path, "python3 -u /abs/stop_hook.py")
    data = read_hooks_file(path)
    assert len(data["hooks"]["stop"]) == 1
    remove_hooks_at_path(path)
    assert read_hooks_file(path)["hooks"]["stop"] == []
    remove_hooks_at_path(tmp_path / "missing.json")  # no-op


def test_read_hooks_file_accepts_bom(tmp_path: Path) -> None:
    path = tmp_path / "hooks.json"
    path.write_bytes(b'\xef\xbb\xbf{"version": 1, "hooks": {"stop": []}}\n')
    data = read_hooks_file(path)
    assert data["version"] == 1


def test_plugin_manifests_and_hooks_contract() -> None:
    root = Path(__file__).resolve().parents[1]
    plugin = root / "plugins" / "cursor-goal"
    market = root / ".cursor-plugin" / "marketplace.json"
    manifest = plugin / ".cursor-plugin" / "plugin.json"
    hooks = plugin / "hooks" / "hooks.json"
    assert market.is_file()
    assert manifest.is_file()
    assert hooks.is_file()

    plugin_data = json.loads(manifest.read_text(encoding="utf-8"))
    assert plugin_data["name"] == "cursor-goal"
    assert plugin_data["version"] == __version__
    assert plugin_data["hooks"] == "./hooks/hooks.json"
    market_data = json.loads(market.read_text(encoding="utf-8"))
    assert market_data["plugins"][0]["source"] == "cursor-goal"
    assert market_data["plugins"][0]["version"] == __version__
    hooks_data = json.loads(hooks.read_text(encoding="utf-8"))
    stop_list = hooks_data["hooks"]["stop"]
    assert isinstance(stop_list, list) and len(stop_list) == 2
    cmds = [entry["command"] for entry in stop_list]
    assert any("stop_hook.cmd" in c for c in cmds)
    assert any("stop_hook.py" in c and "python3" in c for c in cmds)
    for entry in stop_list:
        assert "${CURSOR_PLUGIN_ROOT}" in entry["command"]
        assert entry["loop_limit"] is None
        assert entry["_cursor_goal"] == HOOK_MARKER
    assert (plugin / "skills" / "goal" / "cursor_goal" / "__init__.py").is_file()
    assert (plugin / "agents" / "goalKeeper.md").is_file()
    assert (plugin / "skills" / "goal" / "scripts" / "stop_hook.cmd").is_file()
    assert (plugin / "skills" / "goal" / "scripts" / "wake_loop.sh").is_file()


def test_sync_plugin_tree_check() -> None:
    root = Path(__file__).resolve().parents[1]
    completed = subprocess.run(
        [sys.executable, str(root / "scripts" / "sync-plugin-tree.py"), "--check"],
        cwd=root,
        check=False,
        capture_output=True,
        text=True,
    )
    assert completed.returncode == 0, completed.stderr


def test_compare_file_ignores_crlf_vs_lf(tmp_path: Path) -> None:
    """Windows write_text CRLF must not false-drift against LF checkouts."""
    root = Path(__file__).resolve().parents[1]
    script = root / "scripts" / "sync-plugin-tree.py"
    spec = importlib.util.spec_from_file_location("sync_plugin_tree", script)
    assert spec is not None and spec.loader is not None
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)

    left = tmp_path / "left" / "VERSION"
    right = tmp_path / "right" / "VERSION"
    left.parent.mkdir()
    right.parent.mkdir()
    left.write_bytes(b"2.1.0\n")
    right.write_bytes(b"2.1.0\r\n")
    assert mod._compare_file(left, right, Path("VERSION")) is None

    right.write_bytes(b"9.9.9\r\n")
    assert mod._compare_file(left, right, Path("VERSION")) == "drift: VERSION"


def test_sync_plugin_tree_check_detects_vendored_drift(tmp_path: Path) -> None:
    """Isolated fake repo: vendored package drift must fail --check (no live-tree mutation)."""
    root = Path(__file__).resolve().parents[1]
    script = root / "scripts" / "sync-plugin-tree.py"
    spec = importlib.util.spec_from_file_location("sync_plugin_tree", script)
    assert spec is not None and spec.loader is not None
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)

    fake = tmp_path / "repo"
    shutil.copytree(root / "src" / "cursor_goal", fake / "src" / "cursor_goal")
    shutil.copytree(root / ".cursor", fake / ".cursor")
    mod.write_plugin(fake)
    assert mod.check_plugin(fake) == 0

    target = (
        fake
        / "plugins"
        / "cursor-goal"
        / "skills"
        / "goal"
        / "cursor_goal"
        / "__init__.py"
    )
    text = target.read_text(encoding="utf-8")
    target.write_text(
        text.replace('__version__ = "', '__version__ = "9.9.9-drift-', 1),
        encoding="utf-8",
    )
    assert mod.check_plugin(fake) == 1


def test_check_version_sync_detects_readme_pin_drift(tmp_path: Path) -> None:
    """README tagged-clone pin must match package version."""
    root = Path(__file__).resolve().parents[1]
    script = root / "scripts" / "check_version_sync.py"
    spec = importlib.util.spec_from_file_location("check_version_sync", script)
    assert spec is not None and spec.loader is not None
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)

    # Smoke: live repo is in sync (README pin included after Phase 1).
    completed = subprocess.run(
        [sys.executable, str(script)],
        cwd=root,
        check=False,
        capture_output=True,
        text=True,
    )
    assert completed.returncode == 0, completed.stderr
    assert "README=v" in completed.stdout

    pin = mod._read_readme_pin(root)
    assert pin == __version__

    # Isolated helper: conflicting pins raise.
    bad = tmp_path / "README.md"
    bad.write_text(
        "git clone --branch v1.0.0 x\ngit clone --branch v2.0.0 y\n",
        encoding="utf-8",
    )
    with pytest.raises(ValueError, match="Conflicting README"):
        mod._read_tagged_clone_pin(bad, label="README")
