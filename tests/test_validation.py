"""Tests for cursor_goal.validation."""

from __future__ import annotations

import os
import subprocess
import sys
from typing import Any

import pytest

from cursor_goal.validation import (
    _stream_to_text,
    redact_command,
    run_validation,
    try_split_argv,
)


def test_stream_to_text_none_str_bytes() -> None:
    assert _stream_to_text(None) == ""
    assert _stream_to_text("hello") == "hello"
    assert _stream_to_text(b"bytes") == "bytes"
    assert _stream_to_text(b"\xff") == "\ufffd"


def test_run_validation_empty_command() -> None:
    result = run_validation("   ")
    assert result.exit_code == 1
    assert "empty validation command" in result.output
    assert result.timed_out is False


def test_run_validation_success() -> None:
    # Avoid nested quotes so argv mode works cross-platform.
    result = run_validation(f"{sys.executable} -c print(12345)")
    assert result.exit_code == 0
    assert "12345" in result.output
    assert result.timed_out is False


def test_run_validation_nonzero_exit() -> None:
    # No shell metacharacters (no ';') so argv mode is exercised.
    result = run_validation(f'{sys.executable} -c "raise SystemExit(7)"')
    assert result.exit_code == 7
    assert result.timed_out is False


def test_run_validation_timeout(monkeypatch: pytest.MonkeyPatch) -> None:
    def boom(*_args: Any, **_kwargs: Any) -> None:
        raise subprocess.TimeoutExpired(
            cmd="sleep",
            timeout=0.01,
            output="partial-out",
            stderr=b"partial-err",
        )

    monkeypatch.setattr(subprocess, "run", boom)
    result = run_validation("sleep 99", timeout_sec=0.01)
    assert result.exit_code == 124
    assert result.timed_out is True
    assert "partial-out" in result.output
    assert "partial-err" in result.output


@pytest.mark.parametrize(
    ("command", "expected"),
    [
        ("pytest -q", ["pytest", "-q"]),
        ("echo hi | cat", None),
        ("a && b", None),
        ("", None),
        ('"unterminated', None),
    ],
)
def test_try_split_argv(command: str, expected: list[str] | None) -> None:
    assert try_split_argv(command) == expected


def test_run_validation_uses_argv_when_safe(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    seen: dict[str, Any] = {}

    def fake_run(args: Any, **kwargs: Any) -> Any:
        seen["args"] = args
        seen["shell"] = kwargs.get("shell")

        class Result:
            returncode = 0
            stdout = "ok\n"
            stderr = ""

        return Result()

    monkeypatch.setattr(subprocess, "run", fake_run)
    result = run_validation("pytest -q")
    assert result.exit_code == 0
    assert seen["shell"] is False
    assert seen["args"] == ["pytest", "-q"]


def test_run_validation_uses_shell_for_metacharacters(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    seen: dict[str, Any] = {}

    def fake_run(args: Any, **kwargs: Any) -> Any:
        seen["args"] = args
        seen["shell"] = kwargs.get("shell")

        class Result:
            returncode = 0
            stdout = ""
            stderr = ""

        return Result()

    monkeypatch.setattr(subprocess, "run", fake_run)
    result = run_validation("npm test && npm run lint")
    assert result.exit_code == 0
    assert seen["shell"] is True
    assert seen["args"] == "npm test && npm run lint"


def test_try_split_argv_windows_percent(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(os, "name", "nt")
    assert try_split_argv("echo %PATH%") is None
    assert try_split_argv("echo hi^there") is None


def test_try_split_argv_powershell_brace_expansion() -> None:
    assert try_split_argv('echo "${HOME}/x"') is None
    assert try_split_argv("echo $(whoami)") is None


def test_try_split_argv_windows_strips_quotes(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """posix=False keeps quote chars; Windows path must strip them."""
    monkeypatch.setattr(os, "name", "nt")
    assert try_split_argv('pytest -q "foo bar"') == ["pytest", "-q", "foo bar"]
    assert try_split_argv("pytest -q 'single'") == ["pytest", "-q", "single"]
    assert try_split_argv("pytest -q") == ["pytest", "-q"]


def test_redact_command_hides_secrets() -> None:
    assert "<redacted>" in redact_command("run --token=supersecret")
    assert "<redacted>" in redact_command("run --token supersecret")
    assert "<redacted>" in redact_command("Authorization: Bearer abc.def")
    assert "<redacted>" in redact_command('{"apiKey":"supersecret"}')
    assert redact_command("x" * 250).endswith("…")


def test_scrubbed_validation_env_drops_secrets() -> None:
    from cursor_goal.validation import scrubbed_validation_env

    source = {
        "PATH": "/usr/bin",
        "HOME": "/home/tboy1337",
        "OPENAI_API_KEY": "sk-secret",
        "AWS_SECRET_ACCESS_KEY": "aws-secret",
        "CURSOR_GOAL_DATA": "/tmp/data",
        "CURSOR_GOAL_LOG_SECRETS": "1",
        "LANG": "C",
        "PYTHONPATH": "/evil/inject",
        "PYTHONHOME": "/evil/home",
        "VIRTUAL_ENV": "/home/tboy1337/.venv",
    }
    scrubbed = scrubbed_validation_env(source)
    assert scrubbed["PATH"] == "/usr/bin"
    assert scrubbed["CURSOR_GOAL_DATA"] == "/tmp/data"
    assert scrubbed["VIRTUAL_ENV"] == "/home/tboy1337/.venv"
    assert "OPENAI_API_KEY" not in scrubbed
    assert "AWS_SECRET_ACCESS_KEY" not in scrubbed
    assert "CURSOR_GOAL_LOG_SECRETS" not in scrubbed
    assert "PYTHONPATH" not in scrubbed
    assert "PYTHONHOME" not in scrubbed


def test_redact_secrets_preserves_longer_output() -> None:
    from cursor_goal.validation import redact_secrets

    body = "ok\napi_key=supersecret\n" + ("x" * 500)
    redacted = redact_secrets(body, max_chars=4000)
    assert "supersecret" not in redacted
    assert "<redacted>" in redacted
    assert len(redacted) > 200


def test_run_validation_uses_scrubbed_env(monkeypatch: pytest.MonkeyPatch) -> None:
    seen: dict[str, str] = {}

    def fake_run(*_a: object, **kwargs: object) -> object:
        env = kwargs.get("env")
        assert isinstance(env, dict)
        seen.update(env)

        class Result:
            returncode = 0
            stdout = "ok"
            stderr = ""

        return Result()

    monkeypatch.setenv("LEAK_TOKEN", "should-not-pass")
    monkeypatch.setattr(subprocess, "run", fake_run)
    result = run_validation("echo hi")
    assert result.exit_code == 0
    assert "LEAK_TOKEN" not in seen


def test_run_validation_oserror(monkeypatch: pytest.MonkeyPatch) -> None:
    def boom(*_a: object, **_k: object) -> None:
        raise FileNotFoundError("nope")

    monkeypatch.setattr(subprocess, "run", boom)
    result = run_validation("missing-binary-xyz")
    assert result.exit_code == 127
    assert "could not run" in result.output


def test_run_validation_deny_shell(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("CURSOR_GOAL_DENY_SHELL", "1")
    result = run_validation("echo a && echo b")
    assert result.exit_code == 1
    assert "CURSOR_GOAL_DENY_SHELL" in result.output


def test_run_validation_shell_ok_false() -> None:
    result = run_validation("echo a && echo b", shell_ok=False)
    assert result.exit_code == 1
    assert "shell_ok=false" in result.output
