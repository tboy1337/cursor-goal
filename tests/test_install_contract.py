"""Tests for hooks.json merge helpers used by installers."""

from __future__ import annotations

from pathlib import Path

import pytest

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
