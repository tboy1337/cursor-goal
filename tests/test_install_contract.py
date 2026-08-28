"""Tests for hooks.json merge helpers used by installers."""

from __future__ import annotations

import importlib.util
import io
import json
import os
import shutil
import subprocess
import sys
from contextlib import redirect_stdout
from pathlib import Path
from types import ModuleType

import pytest
from pytest_mock import MockerFixture

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


@pytest.mark.skipif(os.name == "nt", reason="Unix file mode 0600")
def test_write_hooks_file_unix_private_mode(tmp_path: Path) -> None:
    path = tmp_path / "hooks.json"
    write_hooks_file(path, {"version": 1, "hooks": {"stop": []}})
    assert (path.stat().st_mode & 0o777) == 0o600


def test_write_hooks_file_chmod_tmp_before_payload(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from cursor_goal import hooks_config as hooks_mod

    monkeypatch.setattr(hooks_mod.os, "name", "posix")
    seen: list[int] = []

    def fake_chmod(_path: object, mode: int) -> None:
        seen.append(mode)

    monkeypatch.setattr(hooks_mod.os, "chmod", fake_chmod)
    path = tmp_path / "hooks.json"
    write_hooks_file(path, {"version": 1, "hooks": {"stop": []}})
    assert path.is_file()
    assert seen == [0o600]


def test_write_hooks_file_tmp_chmod_oserror_aborts(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from cursor_goal import hooks_config as hooks_mod

    monkeypatch.setattr(hooks_mod.os, "name", "posix")

    def boom(_path: object, _mode: int) -> None:
        raise OSError("chmod denied")

    monkeypatch.setattr(hooks_mod.os, "chmod", boom)
    path = tmp_path / "hooks.json"
    with pytest.raises(OSError, match="chmod denied"):
        write_hooks_file(path, {"version": 1, "hooks": {"stop": []}})
    assert not path.exists()
    assert list(tmp_path.glob("hooks.json.*.tmp")) == []


def test_write_hooks_file_unix_opens_tmp_exclusive_private(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, mocker: MockerFixture
) -> None:
    from cursor_goal import hooks_config as hooks_mod

    monkeypatch.setattr(hooks_mod.os, "name", "posix")
    spy_open = mocker.spy(hooks_mod.os, "open")
    path = tmp_path / "hooks.json"
    write_hooks_file(path, {"version": 1, "hooks": {"stop": []}})
    assert path.is_file()
    assert spy_open.call_count == 1
    _path, flags, mode = spy_open.call_args.args
    assert flags & os.O_CREAT
    assert flags & os.O_EXCL
    assert flags & os.O_WRONLY
    assert mode == 0o600


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
    assert (plugin / "skills" / "cursor-goal" / "cursor_goal" / "__init__.py").is_file()
    assert (plugin / "agents" / "goalKeeper.md").is_file()
    keeper = (plugin / "agents" / "goalKeeper.md").read_text(encoding="utf-8")
    assert "Verify this turn" in keeper
    assert "manage blocked" in keeper
    assert "CreateGoal" in keeper
    assert "do not call CreateGoal" not in keeper.lower()
    skill = (plugin / "skills" / "cursor-goal" / "SKILL.md").read_text(encoding="utf-8")
    assert "name: cursor-goal" in skill
    assert "disable-model-invocation: true" in skill
    frontmatter = skill.split("---", 2)[1]
    assert "/cursor-goal" in frontmatter
    assert "/goal" not in frontmatter
    assert "Iron law" in skill
    assert "FOLLOWUP_REQUIRED" in skill
    assert "Fidelity" in skill
    assert "Untrusted condition" in skill
    assert "manage update" in skill
    assert "--confirm" in skill
    assert "confirm-pass" in skill.lower()
    assert "CreateGoal" in skill
    assert "UpdateGoal" in skill
    assert "native continuation" in skill.lower()
    evaluator = (plugin / "agents" / "goal-evaluator.md").read_text(encoding="utf-8")
    assert "MISSING EVIDENCE" in evaluator
    assert "strong evidence" not in evaluator.lower()
    assert "untrusted" in evaluator.lower()
    assert "confirm-pass" in evaluator.lower()
    auditor = (plugin / "agents" / "goal-auditor.md").read_text(encoding="utf-8")
    assert "CLEAR:" in auditor
    assert "REMAINING:" in auditor
    assert "CHANGELOG" in auditor
    assert "explore" in auditor.lower()
    assert "EXPLORED" in auditor
    assert "CONFIRM-PASS" in auditor
    assert "tests pass" in auditor.lower()
    assert "untrusted" in auditor.lower()
    assert (plugin / "skills" / "cursor-goal" / "scripts" / "stop_hook.cmd").is_file()
    assert (plugin / "skills" / "cursor-goal" / "scripts" / "wake_loop.sh").is_file()
    stop_cmd = (
        plugin / "skills" / "cursor-goal" / "scripts" / "stop_hook.cmd"
    ).read_text(encoding="utf-8")
    assert "CURSOR_GOAL_PYTHON" in stop_cmd
    assert "absolute path" in stop_cmd
    assert "WARNING" in stop_cmd
    assert '"%CGP%" -u' in stop_cmd
    assert '"%CURSOR_GOAL_PYTHON%" -u' not in stop_cmd
    wake_cmd = (
        plugin / "skills" / "cursor-goal" / "scripts" / "wake_loop.cmd"
    ).read_text(encoding="utf-8")
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
    assert r".cursor\skills\cursor-goal" in ps1
    assert "Invoke-GoalInstallBackup" in ps1
    assert "backup-before" in ps1
    assert ".cursor-goal\\backups" in ps1 or ".cursor-goal/backups" in ps1.replace(
        "\\", "/"
    )


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


def test_verify_isort_invocation_and_utf8_child_env() -> None:
    """verify.py must not use python -m isort (broken on isort 9 / 3.14)."""
    root = Path(__file__).resolve().parents[1]
    script = root / "scripts" / "verify.py"
    spec = importlib.util.spec_from_file_location("cursor_goal_verify", script)
    assert spec is not None and spec.loader is not None
    mod = importlib.util.module_from_spec(spec)
    sys.modules["cursor_goal_verify"] = mod
    spec.loader.exec_module(mod)
    cmd = mod.isort_invocation(sys.executable)
    assert cmd
    joined = " ".join(cmd)
    assert "-m isort" not in joined
    name = Path(cmd[0]).name.lower()
    assert "isort" in name or "from isort.main import main" in joined
    env = mod._child_env()
    assert env["PYTHONUTF8"] == "1"
    assert env["PYTHONIOENCODING"] == "utf-8"
    fmt = mod.pyproject_fmt_invocation(sys.executable)
    assert fmt
    fmt_name = Path(fmt[0]).name.lower()
    assert "pyproject-fmt" in fmt_name or fmt[:3] == [
        sys.executable,
        "-m",
        "pyproject_fmt",
    ]


def test_verify_checks_dev_tools_without_importing_them() -> None:
    """Presence checks must use find_spec so bandit/pyproject_fmt are not executed."""
    root = Path(__file__).resolve().parents[1]
    verify = (root / "scripts" / "verify.py").read_text(encoding="utf-8")
    assert "importlib.util.find_spec" in verify
    assert "__import__(mod" not in verify
    script = root / "scripts" / "verify.py"
    spec = importlib.util.spec_from_file_location(
        "cursor_goal_verify_find_spec", script
    )
    assert spec is not None and spec.loader is not None
    mod = importlib.util.module_from_spec(spec)
    sys.modules["cursor_goal_verify_find_spec"] = mod
    spec.loader.exec_module(mod)
    assert mod.missing_dev_modules(["pytest"]) == []
    absent = "cursor_goal_verify_no_such_module_9f3a"
    assert absent in mod.missing_dev_modules([absent])


def test_verify_help_lists_full_ship_gate() -> None:
    root = Path(__file__).resolve().parents[1]
    script = root / "scripts" / "verify.py"
    completed = subprocess.run(
        [sys.executable, str(script), "--help"],
        cwd=root,
        check=False,
        capture_output=True,
        text=True,
    )
    assert completed.returncode == 0, completed.stderr
    out = completed.stdout.lower()
    for token in (
        "wake-smoke",
        "install-smoke",
        "version-sync",
        "plugin-tree-sync",
        "bandit",
        "pip-audit",
        "complexipy",
    ):
        assert token in out, token


def test_pip_audit_scopes_to_this_project_not_the_environment() -> None:
    """Bare pip-audit scans every installed package, including other git repos."""
    root = Path(__file__).resolve().parents[1]
    req = root / "scripts" / "pip-audit-requirements.txt"
    text = req.read_text(encoding="utf-8")
    assert "-e .[dev]" in text
    verify = (root / "scripts" / "verify.py").read_text(encoding="utf-8")
    assert "pip-audit-requirements.txt" in verify
    ci = (root / ".github" / "workflows" / "ci.yml").read_text(encoding="utf-8")
    rel = (root / ".github" / "workflows" / "release.yml").read_text(encoding="utf-8")
    assert ci.count("pip-audit -r scripts/pip-audit-requirements.txt") == 3
    assert rel.count("pip-audit -r scripts/pip-audit-requirements.txt") == 3


def test_release_and_ci_run_complexipy_and_wake_smoke() -> None:
    """Tag-triggered release must not skip gates that CI ubuntu/harness already run."""
    root = Path(__file__).resolve().parents[1]
    ci = (root / ".github" / "workflows" / "ci.yml").read_text(encoding="utf-8")
    rel = (root / ".github" / "workflows" / "release.yml").read_text(encoding="utf-8")
    assert "complexipy src/cursor_goal -mx 15 --quiet" in ci
    assert "complexipy src/cursor_goal -mx 15 --quiet" in rel
    assert rel.count("python scripts/wake-smoke.py") == 3


def test_dev_extra_does_not_list_unused_autopep8() -> None:
    root = Path(__file__).resolve().parents[1]
    text = (root / "pyproject.toml").read_text(encoding="utf-8")
    assert "autopep8" not in text


def test_contributor_docs_use_portable_python_for_verify() -> None:
    root = Path(__file__).resolve().parents[1]
    install = (root / "docs" / "install.md").read_text(encoding="utf-8")
    readme = (root / "README.md").read_text(encoding="utf-8")
    assert "python3 scripts/verify.py" in install
    assert "python3 scripts/verify.py" in readme


def test_platform_compat_parse_result_path_documents_allow_cwd() -> None:
    root = Path(__file__).resolve().parents[1]
    text = (root / "docs" / "platform-compatibility.md").read_text(encoding="utf-8")
    assert "--allow-cwd" in text
    assert "only accepts paths under the goal data directory" in text


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
        / "cursor-goal"
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
    plugin_parent = root / "plugins" / "cursor-goal" / "skills" / "cursor-goal"
    assert (plugin_parent / "cursor_goal" / "__init__.py").is_file()
    leftover = root / "plugins" / "cursor-goal" / "skills" / "goal"
    assert not leftover.exists(), leftover
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
    assert "plugins/cursor-goal/skills/cursor-goal/cursor_goal" in printed


def test_classic_installers_target_cursor_goal_not_goal() -> None:
    """v5 dest is ~/.cursor/skills/cursor-goal; backups live under ~/.cursor-goal."""
    root = Path(__file__).resolve().parents[1]
    sh_text = (root / "scripts" / "install-goal.sh").read_text(encoding="utf-8")
    ps1_text = (root / "scripts" / "install-goal.ps1").read_text(encoding="utf-8")
    un_sh = (root / "scripts" / "uninstall-goal.sh").read_text(encoding="utf-8")
    un_ps1 = (root / "scripts" / "uninstall-goal.ps1").read_text(encoding="utf-8")
    assert 'INSTALL_DIR="${HOME}/.cursor/skills/cursor-goal"' in sh_text
    assert ".cursor/skills/goal.bak." not in sh_text
    assert "backup-before --manifest" in sh_text
    assert ".cursor-goal/backups" in sh_text
    assert r".cursor\skills\cursor-goal" in ps1_text
    assert "backup-before" in ps1_text
    assert "-Manifest $manifestPath" in ps1_text
    assert "Write-Utf8NoBomFile -Path $StdoutPath" in ps1_text
    assert 'LEGACY_INSTALL_DIR="${HOME}/.cursor/skills/goal"' in un_sh
    assert ".cursor-goal/backups" in un_sh
    assert r".cursor\skills\cursor-goal" in un_ps1
    assert r".cursor\skills\goal" in un_ps1
    assert r".cursor-goal\backups" in un_ps1


def test_classic_installers_hold_window_before_exit() -> None:
    """Direct-run installers wait 10s so a closing console stays readable."""
    root = Path(__file__).resolve().parents[1]
    sh_text = (root / "scripts" / "install-goal.sh").read_text(encoding="utf-8")
    ps1_text = (root / "scripts" / "install-goal.ps1").read_text(encoding="utf-8")
    assert "hold_before_exit" in sh_text
    assert "trap hold_before_exit EXIT" in sh_text
    assert 'sleep "${INSTALL_HOLD_SECONDS}"' in sh_text
    assert "INSTALL_HOLD_SECONDS=10" in sh_text
    assert "Wait-GoalInstallExit" in ps1_text
    assert "Get-GoalInstallHoldDuration" in ps1_text
    assert "Start-Sleep -Seconds $seconds" in ps1_text
    invoke = ps1_text.split("function Invoke-GoalInstall", 1)[1].split(
        "Direct-invocation guard", 1
    )[0]
    assert "Wait-GoalInstallExit" not in invoke
    assert "Start-Sleep" not in invoke


def test_zero_friction_install_docs_and_skill() -> None:
    """Successful install must not require doctor, Skills UI, or Custom Mode pin."""
    root = Path(__file__).resolve().parents[1]
    readme = (root / "README.md").read_text(encoding="utf-8")
    install_md = (root / "docs" / "install.md").read_text(encoding="utf-8")
    skill = (root / ".cursor" / "skills" / "cursor-goal" / "SKILL.md").read_text(
        encoding="utf-8"
    )
    keeper = (root / ".cursor" / "agents" / "goalKeeper.md").read_text(encoding="utf-8")
    sh_text = (root / "scripts" / "install-goal.sh").read_text(encoding="utf-8")
    ps1_text = (root / "scripts" / "install-goal.ps1").read_text(encoding="utf-8")

    after = readme.split("After a successful install:")[1].split("Uninstall")[0]
    assert "Restart Cursor" in after
    assert "/cursor-goal" in after
    assert "Both get the auditor" not in after
    assert "or `/goal" not in after
    assert "Run `manage doctor`" not in after
    assert "confirm a single user skill" not in after.lower()
    assert "Pin **cursor-goal** as a Custom Mode" not in after
    assert "Customize" not in after
    assert "Custom Mode" not in readme
    assert "CURSOR_GOAL_NATIVE=0" not in readme
    assert "do not intercept" in skill.lower()
    assert "Custom Mode" not in skill
    assert "Custom Mode" not in keeper

    assert "run `manage doctor` before the first" not in install_md
    assert "Confirm **one** `cursor-goal` entry" not in install_md
    assert "restart cursor" in install_md.lower()
    assert "or `/goal" not in install_md
    assert "Both get the auditor" not in install_md

    assert "disable-model-invocation: true" in skill
    frontmatter = skill.split("---", 2)[1]
    assert "/cursor-goal" in frontmatter
    assert "/goal" not in frontmatter
    assert "auto-applies for `/goal`" not in skill
    assert "Do not use for vanilla /goal" not in keeper
    assert "Custom Mode paired with /goal" not in keeper

    for label, text in (("sh", sh_text), ("ps1", ps1_text)):
        nxt = text.split("Next steps:", 1)[1]
        assert "Restart Cursor" in nxt, label
        assert "/cursor-goal" in nxt, label
        assert "3) Start wake loop" not in nxt, label
        assert "Custom Mode" not in nxt, label
        numbered = nxt.split("Usage in Cursor agent:", 1)[0]
        numbered = numbered.split("Windows stop hook", 1)[0]
        assert "manage doctor" not in numbered, label
        assert "/goal" not in numbered, label


def test_install_backup_migrates_legacy_and_prunes(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from cursor_goal import install_backup as bak

    home = tmp_path / "home"
    skills = home / ".cursor" / "skills"
    legacy = skills / "goal"
    legacy.mkdir(parents=True)
    (legacy / "SKILL.md").write_text("# old\n", encoding="utf-8")
    sibling = skills / "goal.bak.20200101T000000Z"
    sibling.mkdir()
    (sibling / "SKILL.md").write_text("# bak\n", encoding="utf-8")
    agents = home / ".cursor" / "agents"
    agents.mkdir(parents=True)
    (agents / "goalKeeper.md").write_text("keeper\n", encoding="utf-8")
    hooks = home / ".cursor" / "hooks.json"
    hooks.write_text('{"version":1,"hooks":{"stop":[]}}\n', encoding="utf-8")

    monkeypatch.setattr(bak, "utc_stamp", lambda: "20260828T000000Z")
    manifest = bak.backup_before(home)
    assert manifest["skill_backup_source"] == "goal"
    assert not legacy.exists()
    assert not sibling.exists()
    backup_skill = Path(str(manifest["skill_backup"]))
    assert backup_skill.is_dir()
    assert (backup_skill / "SKILL.md").read_text(encoding="utf-8") == "# old\n"
    assert Path(str(manifest["agents"]["goalKeeper.md"])).is_file()
    assert Path(str(manifest["hooks_backup"])).is_file()

    dest = bak.install_dir(home)
    dest.mkdir(parents=True)
    (dest / "SKILL.md").write_text("# new\n", encoding="utf-8")
    bak.restore_after_failure(home, manifest)
    assert not dest.exists()
    restored = bak.legacy_install_dir(home)
    assert (restored / "SKILL.md").read_text(encoding="utf-8") == "# old\n"
    assert json.loads(hooks.read_text(encoding="utf-8"))["version"] == 1
    assert (agents / "goalKeeper.md").read_text(encoding="utf-8") == "keeper\n"


def test_install_backup_prune_keeps_one_and_cli_roundtrip(
    tmp_path: Path,
) -> None:
    from cursor_goal import install_backup as bak

    home = tmp_path / "home"
    skill_root = bak.backups_root(home) / "skill"
    skill_root.mkdir(parents=True)
    (skill_root / "20260827T000000Z").mkdir()
    (skill_root / "20260828T000000Z").mkdir()
    leftover = bak.legacy_install_dir(home)
    leftover.mkdir(parents=True)
    (leftover / "SKILL.md").write_text("# leftover\n", encoding="utf-8")
    removed = bak.prune_backups(home, keep=1)
    assert removed["skill"] >= 1
    remaining = [path.name for path in skill_root.iterdir() if path.is_dir()]
    assert remaining == ["20260828T000000Z"]
    assert not leftover.exists()

    code = bak.main(["--home", str(home), "uninstall-debris"])
    assert code == 0
    assert not bak.backups_root(home).exists()


def test_install_backup_cli_backup_before_stdout_is_json(tmp_path: Path) -> None:
    from cursor_goal import install_backup as bak

    home = tmp_path / "home"
    home.mkdir()
    buf = io.StringIO()
    with redirect_stdout(buf):
        code = bak.main(["--home", str(home), "backup-before"])
    assert code == 0
    payload = json.loads(buf.getvalue())
    assert "skill_backup" in payload
    assert "agents" in payload
    assert "hooks_backup" in payload


def test_install_backup_current_skill_unique_dir_and_restore(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from cursor_goal import install_backup as bak

    home = tmp_path / "home"
    current = bak.install_dir(home)
    current.mkdir(parents=True)
    (current / "SKILL.md").write_text("# live\n", encoding="utf-8")
    (current / "__pycache__").mkdir()
    (current / "__pycache__" / "x.pyc").write_text("x", encoding="utf-8")
    agents = bak.agents_dir(home)
    agents.mkdir(parents=True)
    for name in bak.AGENT_NAMES:
        (agents / name).write_text(f"{name}\n", encoding="utf-8")
    hooks = home / ".cursor" / "hooks.json"
    hooks.write_text('{"version":1}\n', encoding="utf-8")
    stamp = "20260828T120000Z"
    collide = bak.backups_root(home) / "skill" / stamp
    collide.mkdir(parents=True)
    (bak.backups_root(home) / "skill" / f"{stamp}-2").mkdir()
    hook_collide = bak.backups_root(home) / "hooks" / f"hooks.json.bak.{stamp}"
    hook_collide.parent.mkdir(parents=True)
    hook_collide.write_text("old\n", encoding="utf-8")
    monkeypatch.setattr(bak, "utc_stamp", lambda: stamp)
    manifest = bak.backup_before(home)
    assert manifest["skill_backup_source"] == "cursor-goal"
    assert current.is_dir()
    backup = Path(str(manifest["skill_backup"]))
    assert backup.name == f"{stamp}-3"
    assert not (backup / "__pycache__").exists()
    assert Path(str(manifest["hooks_backup"])).name.endswith(str(os.getpid())) or (
        Path(str(manifest["hooks_backup"])).name != hook_collide.name
    )

    (current / "SKILL.md").write_text("# replaced\n", encoding="utf-8")
    bak.restore_after_failure(home, manifest)
    assert (current / "SKILL.md").read_text(encoding="utf-8") == "# live\n"
    assert json.loads(hooks.read_text(encoding="utf-8")) == {"version": 1}


def test_install_backup_restore_missing_and_new_agents(tmp_path: Path) -> None:
    from cursor_goal import install_backup as bak

    home = tmp_path / "home"
    dest = bak.install_dir(home)
    dest.mkdir(parents=True)
    (dest / "SKILL.md").write_text("# new\n", encoding="utf-8")
    agents = bak.agents_dir(home)
    agents.mkdir(parents=True)
    (agents / "goalKeeper.md").write_text("installed\n", encoding="utf-8")
    (agents / "goal-evaluator.md").write_text("keep\n", encoding="utf-8")
    bak.restore_after_failure(
        home,
        {
            "skill_backup": str(tmp_path / "missing-skill"),
            "skill_backup_source": "cursor-goal",
            "hooks_backup": str(tmp_path / "missing-hooks.json"),
            "agents": {
                "goalKeeper.md": None,
                "goal-evaluator.md": str(tmp_path / "missing-eval.md"),
            },
        },
    )
    assert (dest / "SKILL.md").is_file()
    assert not (agents / "goalKeeper.md").exists()
    assert (agents / "goal-evaluator.md").read_text(encoding="utf-8") == "keep\n"


def test_install_backup_restore_legacy_when_legacy_exists(
    tmp_path: Path,
) -> None:
    from cursor_goal import install_backup as bak

    home = tmp_path / "home"
    backup = bak.backups_root(home) / "skill" / "stamp"
    backup.mkdir(parents=True)
    (backup / "SKILL.md").write_text("# bak\n", encoding="utf-8")
    dest = bak.install_dir(home)
    dest.mkdir(parents=True)
    (dest / "SKILL.md").write_text("# new\n", encoding="utf-8")
    legacy = bak.legacy_install_dir(home)
    legacy.mkdir(parents=True)
    (legacy / "SKILL.md").write_text("# leftover\n", encoding="utf-8")
    bak.restore_after_failure(
        home,
        {
            "skill_backup": str(backup),
            "skill_backup_source": "goal",
            "agents": {},
        },
    )
    assert not dest.exists()
    assert (legacy / "SKILL.md").read_text(encoding="utf-8") == "# bak\n"


def test_install_backup_prune_agents_hooks_and_in_root_bak(
    tmp_path: Path,
) -> None:
    from cursor_goal import install_backup as bak

    home = tmp_path / "home"
    agents_bak = bak.backups_root(home) / "agents"
    (agents_bak / "old").mkdir(parents=True)
    (agents_bak / "new").mkdir()
    hooks_bak = bak.backups_root(home) / "hooks"
    hooks_bak.mkdir(parents=True)
    (hooks_bak / "hooks.json.bak.1").write_text("1\n", encoding="utf-8")
    (hooks_bak / "hooks.json.bak.2").write_text("2\n", encoding="utf-8")
    skills = bak.skills_dir(home)
    skills.mkdir(parents=True)
    (skills / "cursor-goal.bak.old").mkdir()
    live_agents = bak.agents_dir(home)
    live_agents.mkdir(parents=True)
    (live_agents / "goalKeeper.md.bak.old").write_text("x\n", encoding="utf-8")
    with pytest.raises(ValueError, match="keep must be"):
        bak.prune_backups(home, keep=0)
    removed = bak.prune_backups(home, keep=1)
    assert removed["agents"] == 1
    assert removed["hooks"] == 1
    assert not (skills / "cursor-goal.bak.old").exists()
    assert not (live_agents / "goalKeeper.md.bak.old").exists()
    assert bak._sorted_stamp_dirs(tmp_path / "missing") == []
    assert bak._sorted_files(tmp_path / "missing") == []
    assert bak.list_legacy_skill_bak_dirs(home) == []


def test_install_backup_cli_prune_restore_and_bad_manifest(
    tmp_path: Path,
) -> None:
    from cursor_goal import install_backup as bak

    home = tmp_path / "home"
    skill = bak.backups_root(home) / "skill" / "only"
    skill.mkdir(parents=True)
    buf = io.StringIO()
    with redirect_stdout(buf):
        code = bak.main(["--home", str(home), "prune-after", "--keep", "1"])
    assert code == 0
    assert json.loads(buf.getvalue())["skill"] == 0

    manifest = tmp_path / "manifest.json"
    manifest.write_text(
        json.dumps({"skill_backup": None, "agents": "nope"}),
        encoding="utf-8",
    )
    assert bak.main(["--home", str(home), "restore", "--manifest", str(manifest)]) == 0
    assert bak.main(["--home", str(home), "restore"]) == 1
    utf16 = tmp_path / "manifest-utf16.json"
    utf16.write_bytes(json.dumps({"skill_backup": None, "agents": {}}).encode("utf-16"))
    loaded = bak._load_manifest(utf16)
    assert loaded["skill_backup"] is None
    assert bak.main(["--home", str(home), "restore", "--manifest", str(utf16)]) == 0
    written = tmp_path / "written-manifest.json"
    buf_before = io.StringIO()
    with redirect_stdout(buf_before):
        write_code = bak.main(
            ["--home", str(home), "backup-before", "--manifest", str(written)]
        )
    assert write_code == 0
    assert json.loads(written.read_text(encoding="utf-8")) == json.loads(
        buf_before.getvalue()
    )
    assert not written.read_bytes().startswith(b"\xff\xfe")
    bad = tmp_path / "bad.json"
    bad.write_text("[]\n", encoding="utf-8")
    with pytest.raises(ValueError, match="JSON object"):
        bak._load_manifest(bad)
    garbage = tmp_path / "garbage.bin"
    garbage.write_bytes(b"\xff")
    with pytest.raises(ValueError, match="UTF-8 or UTF-16"):
        bak._load_manifest(garbage)
    assert bak.main(["--home", str(home), "restore", "--manifest", str(bad)]) == 1


def test_install_backup_copy_tree_replaces_existing_dest(tmp_path: Path) -> None:
    from cursor_goal import install_backup as bak

    src = tmp_path / "src"
    src.mkdir()
    (src / "a.txt").write_text("a\n", encoding="utf-8")
    dest = tmp_path / "dest"
    dest.mkdir()
    (dest / "old.txt").write_text("old\n", encoding="utf-8")
    bak._copy_tree(src, dest)
    assert (dest / "a.txt").read_text(encoding="utf-8") == "a\n"
    assert not (dest / "old.txt").exists()


def test_install_backup_uninstall_debris_removes_trees(tmp_path: Path) -> None:
    from cursor_goal import install_backup as bak

    home = tmp_path / "home"
    bak.install_dir(home).mkdir(parents=True)
    bak.legacy_install_dir(home).mkdir(parents=True)
    sibling = bak.skills_dir(home) / "goal.bak.old"
    sibling.mkdir()
    agents = bak.agents_dir(home)
    agents.mkdir(parents=True)
    (agents / "goal-evaluator.md.bak.x").write_text("x\n", encoding="utf-8")
    (bak.backups_root(home) / "skill").mkdir(parents=True)
    bak.uninstall_debris(home)
    assert not bak.install_dir(home).exists()
    assert not bak.legacy_install_dir(home).exists()
    assert not sibling.exists()
    assert not bak.backups_root(home).exists()
    assert not (agents / "goal-evaluator.md.bak.x").exists()
