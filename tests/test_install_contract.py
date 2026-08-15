"""Tests for hooks.json merge helpers used by installers."""

from __future__ import annotations

import importlib.util
import json
import os
import shutil
import subprocess
import sys
from pathlib import Path
from types import ModuleType

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


def test_merge_keeps_unmarked_stop_hooks(tmp_path: Path) -> None:
    """Unmarked hooks are left alone; only marked goal hooks are replaced."""
    del tmp_path
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
    assert len(stop) == 3
    assert stop[0]["command"] == "~/.cursor/skills/goal/goal-stop.sh"
    assert stop[1]["command"] == "./other.sh"
    assert is_goal_stop_hook(stop[2])
    assert "-u" in stop[2]["command"]


def test_remove_stop_hooks(tmp_path: Path) -> None:
    path = tmp_path / "hooks.json"
    data = merge_stop_hook(
        {"version": 1, "hooks": {"stop": []}},
        build_stop_entry("py -3 -u C:\\stop_hook.py"),
    )
    write_hooks_file(path, data)
    cleaned = remove_stop_hooks(read_hooks_file(path))
    assert cleaned["hooks"]["stop"] == []


def test_remove_stop_hooks_prefers_marker_over_legacy_substring() -> None:
    data = {
        "version": 1,
        "hooks": {
            "stop": [
                build_stop_entry("python3 -u /abs/stop_hook.py"),
                {"command": "other-tool wrap stop_hook.py"},
            ]
        },
    }
    cleaned = remove_stop_hooks(data)
    remaining = cleaned["hooks"]["stop"]
    assert len(remaining) == 1
    assert "other-tool" in remaining[0]["command"]


def test_is_goal_stop_hook_variants() -> None:
    assert is_goal_stop_hook("not-a-dict") is False
    # Marker required — command substring alone is not enough (3.0 clean break).
    assert is_goal_stop_hook({"command": "cursor_goal stop"}) is False
    assert is_goal_stop_hook({"command": "cursor-goal stop"}) is False
    assert is_goal_stop_hook({"command": r"C:\x\stop_hook.cmd"}) is False
    assert is_goal_stop_hook({"_cursor_goal": HOOK_MARKER, "command": "x"}) is True
    assert is_goal_stop_hook({"command": "./unrelated.sh"}) is False
    assert is_goal_stop_hook({"command": "stop_hook.py"}) is False


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


def test_merge_hooks_at_path_with_subagent_stop(tmp_path: Path) -> None:
    from cursor_goal.hooks_config import merge_hooks_at_path

    path = tmp_path / "hooks.json"
    merge_hooks_at_path(
        path,
        "python3 -u /abs/stop_hook.py",
        subagent_stop_command="python3 -u /abs/stop_hook.py",
    )
    data = read_hooks_file(path)
    assert len(data["hooks"]["stop"]) == 1
    subagent_stop = data["hooks"]["subagentStop"]
    assert len(subagent_stop) == 2
    matchers = {entry["matcher"] for entry in subagent_stop}
    assert matchers == {"goal-evaluator", "goal-auditor"}
    assert {entry["command"] for entry in subagent_stop} == {
        "python3 -u /abs/stop_hook.py"
    }

    # Re-merging (upgrade path) replaces rather than duplicating the marked entries.
    merge_hooks_at_path(
        path,
        "python3 -u /abs/stop_hook.py",
        subagent_stop_command="python3 -u /abs/stop_hook.py",
    )
    data = read_hooks_file(path)
    assert len(data["hooks"]["stop"]) == 1
    assert len(data["hooks"]["subagentStop"]) == 2


def test_remove_hooks_at_path_removes_subagent_stop(tmp_path: Path) -> None:
    from cursor_goal.hooks_config import merge_hooks_at_path, remove_hooks_at_path

    path = tmp_path / "hooks.json"
    merge_hooks_at_path(
        path,
        "python3 -u /abs/stop_hook.py",
        subagent_stop_command="python3 -u /abs/stop_hook.py",
    )
    remove_hooks_at_path(path)
    data = read_hooks_file(path)
    assert data["hooks"]["stop"] == []
    assert data["hooks"]["subagentStop"] == []


def test_merge_subagent_stop_hook_preserves_other_matchers() -> None:
    from cursor_goal.hooks_config import (
        build_subagent_stop_entry,
        is_goal_subagent_stop_hook,
        merge_subagent_stop_hook,
    )

    data = {
        "version": 1,
        "hooks": {
            "subagentStop": [
                {"command": "./user-hook.sh", "matcher": "other-agent"},
            ]
        },
    }
    entry = build_subagent_stop_entry("python3 -u /abs/stop_hook.py")
    merged = merge_subagent_stop_hook(data, entry)
    subagent_stop = merged["hooks"]["subagentStop"]
    assert len(subagent_stop) == 2
    assert any(item.get("command") == "./user-hook.sh" for item in subagent_stop)
    assert is_goal_subagent_stop_hook(subagent_stop[-1])
    assert subagent_stop[-1]["matcher"] == "goal-evaluator"


def test_merge_audit_subagent_stop_hook_preserves_evaluator() -> None:
    from cursor_goal.hooks_config import (
        build_audit_subagent_stop_entry,
        build_subagent_stop_entry,
        is_goal_audit_subagent_stop_hook,
        is_goal_subagent_stop_hook,
        merge_audit_subagent_stop_hook,
        merge_subagent_stop_hook,
    )

    data: dict[str, object] = {"version": 1, "hooks": {"subagentStop": []}}
    data = merge_subagent_stop_hook(
        data, build_subagent_stop_entry("python3 -u /abs/stop_hook.py")
    )
    data = merge_audit_subagent_stop_hook(
        data, build_audit_subagent_stop_entry("python3 -u /abs/stop_hook.py")
    )
    subagent_stop = data["hooks"]["subagentStop"]
    assert isinstance(subagent_stop, list)
    assert len(subagent_stop) == 2
    matchers = {entry["matcher"] for entry in subagent_stop}
    assert matchers == {"goal-evaluator", "goal-auditor"}
    assert any(is_goal_subagent_stop_hook(entry) for entry in subagent_stop)
    assert any(is_goal_audit_subagent_stop_hook(entry) for entry in subagent_stop)


def test_remove_subagent_stop_hooks_leaves_user_entries() -> None:
    from cursor_goal.hooks_config import (
        build_subagent_stop_entry,
        remove_subagent_stop_hooks,
    )

    data = {
        "version": 1,
        "hooks": {
            "subagentStop": [
                {"command": "./user-hook.sh", "matcher": "other-agent"},
                build_subagent_stop_entry("python3 -u /abs/stop_hook.py"),
            ]
        },
    }
    result = remove_subagent_stop_hooks(data)
    subagent_stop = result["hooks"]["subagentStop"]
    assert len(subagent_stop) == 1
    assert subagent_stop[0]["command"] == "./user-hook.sh"


def test_remove_subagent_stop_hooks_no_hooks_key_is_noop() -> None:
    from cursor_goal.hooks_config import remove_subagent_stop_hooks

    data: dict[str, object] = {"version": 1}
    assert remove_subagent_stop_hooks(data) == data


def test_is_goal_subagent_stop_hook_rejects_non_dict() -> None:
    from cursor_goal.hooks_config import is_goal_subagent_stop_hook

    assert is_goal_subagent_stop_hook("not-a-dict") is False
    assert is_goal_subagent_stop_hook(None) is False


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
    subagent_stop_list = hooks_data["hooks"]["subagentStop"]
    assert isinstance(subagent_stop_list, list) and len(subagent_stop_list) == 4
    matchers = {entry["matcher"] for entry in subagent_stop_list}
    assert matchers == {"goal-evaluator", "goal-auditor"}
    for entry in subagent_stop_list:
        assert "${CURSOR_PLUGIN_ROOT}" in entry["command"]
        assert entry["loop_limit"] is None
        if entry["matcher"] == "goal-evaluator":
            assert entry["_cursor_goal"] == "cursor_goal_subagent_stop_hook"
        else:
            assert entry["_cursor_goal"] == "cursor_goal_subagent_audit_stop_hook"
    # Same launcher commands as stop — cmd_stop() dispatches on payload shape.
    assert {e["command"] for e in subagent_stop_list} == {
        e["command"] for e in stop_list
    }
    assert (plugin / "skills" / "goal" / "cursor_goal" / "__init__.py").is_file()
    assert (plugin / "agents" / "goalKeeper.md").is_file()
    keeper = (plugin / "agents" / "goalKeeper.md").read_text(encoding="utf-8")
    assert "Verify this turn" in keeper
    assert "manage blocked" in keeper
    skill = (plugin / "skills" / "goal" / "SKILL.md").read_text(encoding="utf-8")
    assert "Iron law" in skill
    assert "FOLLOWUP_REQUIRED" in skill
    assert "Fidelity" in skill
    assert "Untrusted condition" in skill
    assert "manage update" in skill
    evaluator = (plugin / "agents" / "goal-evaluator.md").read_text(encoding="utf-8")
    assert "MISSING EVIDENCE" in evaluator
    assert "strong evidence" not in evaluator.lower()
    assert "untrusted" in evaluator.lower()
    auditor = (plugin / "agents" / "goal-auditor.md").read_text(encoding="utf-8")
    assert "CLEAR:" in auditor
    assert "REMAINING:" in auditor
    assert "CHANGELOG" in auditor
    assert "Map the tree" in auditor
    assert "tests pass" in auditor.lower()
    assert "untrusted" in auditor.lower()
    assert (plugin / "skills" / "goal" / "scripts" / "stop_hook.cmd").is_file()
    assert (plugin / "skills" / "goal" / "scripts" / "wake_loop.sh").is_file()
    stop_cmd = (plugin / "skills" / "goal" / "scripts" / "stop_hook.cmd").read_text(
        encoding="utf-8"
    )
    assert "CURSOR_GOAL_PYTHON" in stop_cmd
    assert "absolute path" in stop_cmd
    assert "WARNING" in stop_cmd
    assert '"%CGP%" -u' in stop_cmd
    assert '"%CURSOR_GOAL_PYTHON%" -u' not in stop_cmd
    wake_cmd = (plugin / "skills" / "goal" / "scripts" / "wake_loop.cmd").read_text(
        encoding="utf-8"
    )
    assert "CURSOR_GOAL_PYTHON" in wake_cmd
    assert "absolute path" in wake_cmd
    assert "WARNING" in wake_cmd
    assert '"%CGP%" -u' in wake_cmd
    assert '"%CURSOR_GOAL_PYTHON%" -u' not in wake_cmd
    assert 'findstr /R "[&|<>^]"' in stop_cmd
    assert "unsafe cmd metacharacters" in stop_cmd
    assert 'findstr /R "[&|<>^]"' in wake_cmd


def test_classic_install_ps1_cgp_metachar_parity() -> None:
    """Classic install-goal.ps1 must bake marketplace-parity CGP metachar gates."""
    root = Path(__file__).resolve().parents[1]
    ps1 = (root / "scripts" / "install-goal.ps1").read_text(encoding="utf-8")
    assert ps1.count('findstr /R "[&|<>^]"') >= 2
    assert "unsafe cmd metacharacters" in ps1
    assert "Protect-GoalDataDirAcl" in ps1
    assert "failure_reason" in ps1
    assert "ACL harden failed" in ps1
    assert "Restart Cursor" in ps1
    assert "FOLLOWUP_REQUIRED" in ps1
    assert r"scripts\.tmp" in ps1
    assert "PackageRoot" in ps1
    assert "skill tree not modified" in ps1


def test_marketplace_hooks_and_doctor_fixture(tmp_path: Path) -> None:
    """Marketplace dual hooks fixture without a real Cursor install."""
    root = Path(__file__).resolve().parents[1]
    hooks_src = root / "plugins" / "cursor-goal" / "hooks" / "hooks.json"
    assert hooks_src.is_file()
    data = json.loads(hooks_src.read_text(encoding="utf-8"))
    stop = data["hooks"]["stop"]
    assert len(stop) >= 2
    assert all(item.get("_cursor_goal") == HOOK_MARKER for item in stop)
    dest = tmp_path / "hooks.json"
    write_hooks_file(
        dest,
        {
            "version": 1,
            "hooks": {
                "stop": [
                    build_stop_entry("cmd /c stop_hook.cmd"),
                    build_stop_entry('python3 -u "stop_hook.py"'),
                ]
            },
        },
    )
    loaded = json.loads(dest.read_text(encoding="utf-8"))
    assert len(loaded["hooks"]["stop"]) == 2
    assert all(
        item.get("_cursor_goal") == HOOK_MARKER for item in loaded["hooks"]["stop"]
    )


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
    left.write_bytes(b"3.0.0\n")
    right.write_bytes(b"3.0.0\r\n")
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
    copying = root / "COPYING"
    if copying.is_file():
        shutil.copy2(copying, fake / "COPYING")
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
        "git clone --branch v1.0.0 x\ngit clone --branch v2.16.0 y\n",
        encoding="utf-8",
    )
    with pytest.raises(ValueError, match="Conflicting README"):
        mod._read_tagged_clone_pin(bad, label="README")


def _load_check_version_sync() -> ModuleType:
    """Import scripts/check_version_sync.py as a module for helper tests."""
    root = Path(__file__).resolve().parents[1]
    script = root / "scripts" / "check_version_sync.py"
    spec = importlib.util.spec_from_file_location("check_version_sync", script)
    assert spec is not None and spec.loader is not None
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def test_parse_pyproject_version_text() -> None:
    mod = _load_check_version_sync()
    text = '[project]\nname = "cursor-goal"\nversion = "4.4.0"\n'
    assert mod.parse_pyproject_version_text(text) == "4.4.0"
    with pytest.raises(ValueError, match="Could not parse version"):
        mod.parse_pyproject_version_text("[project]\nname = 'x'\n")


def test_parse_bool_flag() -> None:
    mod = _load_check_version_sync()
    assert mod.parse_bool_flag("1") is True
    assert mod.parse_bool_flag("true") is True
    assert mod.parse_bool_flag("YES") is True
    assert mod.parse_bool_flag("0") is False
    assert mod.parse_bool_flag("false") is False
    assert mod.parse_bool_flag("") is False
    with pytest.raises(ValueError, match="invalid boolean"):
        mod.parse_bool_flag("maybe")


@pytest.mark.parametrize(
    ("event_name", "ref", "current", "previous", "release_exists", "expected"),
    [
        ("push", "refs/heads/main", "4.4.0", "4.3.0", False, "release"),
        ("push", "refs/heads/main", "4.4.0", "4.4.0", False, "unchanged"),
        ("push", "refs/heads/main", "4.4.0", "4.3.0", True, "skip_exists"),
        ("push", "refs/heads/testing", "4.4.0", "4.3.0", False, "unchanged"),
        ("pull_request", "refs/heads/main", "4.4.0", "4.3.0", False, "unchanged"),
        ("push", "refs/heads/main", "4.4.0", None, False, "unchanged"),
        ("push", "refs/heads/main", "4.4.0", "", False, "unchanged"),
        ("push", "refs/heads/main", "5.0.0", "4.4.0", False, "release"),
    ],
)
def test_version_bump_decision_table(
    event_name: str,
    ref: str,
    current: str,
    previous: str | None,
    release_exists: bool,
    expected: str,
) -> None:
    mod = _load_check_version_sync()
    result = mod.version_bump_decision(
        current=current,
        previous=previous,
        release_exists=release_exists,
        event_name=event_name,
        ref=ref,
    )
    assert result["decision"] == expected
    assert result["current"] == current
    assert result["previous"] == (previous or "")
    assert result["tag"] == f"v{current}"


def test_version_bump_decision_rejects_non_semver() -> None:
    mod = _load_check_version_sync()
    with pytest.raises(ValueError, match="not X.Y.Z"):
        mod.version_bump_decision(
            current="4.4.0rc1",
            previous="4.4.0",
            release_exists=False,
            event_name="push",
            ref="refs/heads/main",
        )


def test_detect_bump_cli_writes_github_output(tmp_path: Path) -> None:
    root = Path(__file__).resolve().parents[1]
    script = root / "scripts" / "check_version_sync.py"
    current = tmp_path / "current.toml"
    previous = tmp_path / "previous.toml"
    current.write_text('version = "4.5.0"\n', encoding="utf-8")
    previous.write_text('version = "4.4.0"\n', encoding="utf-8")
    github_output = tmp_path / "github_output"
    env = {**os.environ, "GITHUB_OUTPUT": str(github_output)}
    completed = subprocess.run(
        [
            sys.executable,
            str(script),
            "--detect-bump",
            "--current-file",
            str(current),
            "--previous-file",
            str(previous),
            "--event-name",
            "push",
            "--ref",
            "refs/heads/main",
            "--release-exists",
            "0",
        ],
        cwd=str(root),
        env=env,
        capture_output=True,
        text=True,
        check=False,
        timeout=30,
    )
    assert completed.returncode == 0, completed.stderr
    payload = json.loads(completed.stdout.strip().splitlines()[-1])
    assert payload["decision"] == "release"
    assert payload["tag"] == "v4.5.0"
    written = github_output.read_text(encoding="utf-8")
    assert "decision=release\n" in written
    assert "tag=v4.5.0\n" in written
    assert "current=4.5.0\n" in written
    assert "previous=4.4.0\n" in written


def test_plugin_vendored_package_imports() -> None:
    """Marketplace tree is a runnable copy of src/, not just a sync check."""
    root = Path(__file__).resolve().parents[1]
    plugin_parent = root / "plugins" / "cursor-goal" / "skills" / "goal"
    assert (plugin_parent / "cursor_goal" / "__init__.py").is_file()
    env = {**os.environ, "PYTHONPATH": str(plugin_parent)}
    completed = subprocess.run(
        [
            sys.executable,
            "-c",
            (
                "import sys; sys.path.insert(0, "
                + repr(str(plugin_parent))
                + "); import cursor_goal; from cursor_goal import __version__; "
                "assert __version__; print(cursor_goal.__file__)"
            ),
        ],
        cwd=str(root),
        env=env,
        capture_output=True,
        text=True,
        check=False,
        timeout=30,
    )
    assert completed.returncode == 0, completed.stderr
    printed = completed.stdout.strip().replace("\\", "/")
    assert "plugins/cursor-goal/skills/goal/cursor_goal" in printed
