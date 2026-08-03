#!/usr/bin/env python3
"""Non-IDE wake smoke: create → arm → start loop → pid_alive → tick → disarm.

Does not require Cursor. Exit 0 on success. IDE E2E remains manual
(see docs/release.md).
"""

from __future__ import annotations

import json
import os
import subprocess
import sys
import tempfile
import time
from pathlib import Path


def main() -> int:
    root = Path(__file__).resolve().parents[1]
    src = root / "src"
    env = os.environ.copy()
    prior = env.get("PYTHONPATH", "")
    env["PYTHONPATH"] = str(src) if not prior else f"{src}{os.pathsep}{prior}"
    env["CURSOR_GOAL_WAKE"] = "1"
    env["CURSOR_GOAL_WAKE_INTERVAL_S"] = "5"
    env["CURSOR_GOAL_ALLOW_DEAD_WAKE"] = "1"
    env["CURSOR_GOAL_SKIP_ACL"] = "1"
    env["CURSOR_GOAL_ALLOW_ANY_WORKDIR"] = "1"

    with tempfile.TemporaryDirectory(prefix="cursor-goal-wake-smoke-") as tmp:
        data = Path(tmp) / "data"
        data.mkdir()
        env["CURSOR_GOAL_DATA"] = str(data)

        def run(*args: str) -> subprocess.CompletedProcess[str]:
            return subprocess.run(  # nosec B603
                [sys.executable, "-u", "-m", "cursor_goal", *args],
                cwd=str(root),
                env=env,
                capture_output=True,
                text=True,
                timeout=60,
                check=False,
            )

        created = run("manage", "create", "wake smoke goal", "--budget", "5")
        if created.returncode != 0:
            print(created.stderr or created.stdout, file=sys.stderr)
            return 1

        armed = run("wake", "arm", "--interval", "5")
        if armed.returncode != 0:
            print(armed.stderr or armed.stdout, file=sys.stderr)
            return 1

        loop = subprocess.Popen(  # nosec B603
            [
                sys.executable,
                "-u",
                "-m",
                "cursor_goal",
                "wake",
                "loop",
                "--interval",
                "5",
            ],
            cwd=str(root),
            env=env,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.PIPE,
            text=True,
        )
        try:
            alive = False
            for _ in range(40):
                time.sleep(0.25)
                status = run("wake", "status")
                try:
                    report = json.loads(status.stdout.strip() or "{}")
                except json.JSONDecodeError:
                    continue
                if report.get("pid_alive") is True:
                    alive = True
                    break
            if not alive:
                print(status.stdout, status.stderr, file=sys.stderr)
                print("wake smoke: pid_alive never became true", file=sys.stderr)
                return 1

            tick = run("wake", "tick")
            if tick.returncode != 0:
                print(tick.stderr or tick.stdout, file=sys.stderr)
                return 1
            print("wake-smoke: ok")
            return 0
        finally:
            run("wake", "disarm")
            if loop.poll() is None:
                loop.terminate()
                try:
                    loop.wait(timeout=5)
                except subprocess.TimeoutExpired:
                    loop.kill()


if __name__ == "__main__":
    raise SystemExit(main())
