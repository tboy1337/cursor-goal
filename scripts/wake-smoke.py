#!/usr/bin/env python3
"""Non-IDE wake smoke: create → arm → start loop → pid_alive → disarm.

Does not require Cursor. Exit 0 on success. IDE E2E remains manual
(see docs/release.md).
"""

from __future__ import annotations

import json
import os
import sys
import tempfile
import threading
import time
from pathlib import Path


def _smoke_temp_base() -> str | None:
    """Prefer a non-symlink temp root (macOS /var/folders → /private/...)."""
    override = (os.environ.get("CURSOR_GOAL_SMOKE_BASE") or "").strip()
    if override:
        return override
    for candidate in ("/private/tmp", "/tmp"):
        path = Path(candidate)
        try:
            if path.is_dir() and not path.is_symlink():
                return str(path)
        except OSError:
            continue
    return None


def main() -> int:
    root = Path(__file__).resolve().parents[1]
    src = root / "src"
    prior = os.environ.get("PYTHONPATH", "")
    os.environ["PYTHONPATH"] = str(src) if not prior else f"{src}{os.pathsep}{prior}"
    if str(src) not in sys.path:
        sys.path.insert(0, str(src))

    os.environ["CURSOR_GOAL_WAKE"] = "1"
    os.environ["CURSOR_GOAL_WAKE_INTERVAL_S"] = "5"
    os.environ["CURSOR_GOAL_ALLOW_DEAD_WAKE"] = "1"
    os.environ["CURSOR_GOAL_SKIP_ACL"] = "1"
    os.environ["CURSOR_GOAL_ALLOW_ANY_WORKDIR"] = "1"

    tmp_kwargs: dict[str, object] = {
        "prefix": "cursor-goal-wake-smoke-",
        "ignore_cleanup_errors": True,
    }
    base = _smoke_temp_base()
    if base:
        tmp_kwargs["dir"] = base

    with tempfile.TemporaryDirectory(**tmp_kwargs) as tmp:
        data = Path(tmp) / "data"
        data.mkdir()
        # Prefer realpath so macOS /var → /private/var ancestors are resolved.
        try:
            data = data.resolve()
        except OSError:
            pass
        os.environ["CURSOR_GOAL_DATA"] = str(data)

        from cursor_goal.cli import main as cli_main
        from cursor_goal.wake import disarm, run_loop, status_report

        def invoke(*args: str) -> int:
            return int(cli_main(list(args)))

        code = invoke("manage", "create", "wake smoke goal", "--budget", "5")
        if code != 0:
            print("wake smoke: manage create failed", file=sys.stderr)
            return 1

        # create already arms when wake is enabled; ensure armed.
        code = invoke("wake", "arm", "--interval", "5")
        if code != 0:
            print("wake smoke: wake arm failed", file=sys.stderr)
            return 1

        loop_error: list[BaseException] = []

        def _loop() -> None:
            try:
                run_loop(interval=5)
            except BaseException as exc:  # noqa: BLE001 — surface thread failure
                loop_error.append(exc)

        thread = threading.Thread(target=_loop, name="wake-smoke-loop", daemon=True)
        thread.start()
        exit_code = 1
        try:
            alive = False
            report: dict[str, object] = {}
            for _ in range(40):
                time.sleep(0.25)
                report = status_report()
                if report.get("pid_alive") is True:
                    alive = True
                    break
            if not alive:
                print(json.dumps(report, indent=2), file=sys.stderr)
                print("wake smoke: pid_alive never became true", file=sys.stderr)
                exit_code = 1
            elif "command" not in report or "continuation_reason" not in report:
                print(json.dumps(report, indent=2), file=sys.stderr)
                print("wake smoke: status missing readiness fields", file=sys.stderr)
                exit_code = 1
            else:
                print("wake-smoke: ok")
                exit_code = 0
        finally:
            try:
                disarm(kill_loop=True)
            except Exception as exc:  # noqa: BLE001 — smoke must not fail on cleanup
                print(f"wake smoke: disarm warning: {exc}", file=sys.stderr)
            thread.join(timeout=3)
            if loop_error:
                print(f"wake smoke: loop error: {loop_error[0]!r}", file=sys.stderr)
                # Do not override a successful readiness check: disarm/kill can
                # interrupt the loop thread with a benign exception.
        return exit_code


if __name__ == "__main__":
    raise SystemExit(main())
