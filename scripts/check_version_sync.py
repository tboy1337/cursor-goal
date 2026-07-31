#!/usr/bin/env python3
"""Fail if pyproject.toml version != cursor_goal.__version__."""

from __future__ import annotations

import re
import sys
from pathlib import Path


def main() -> int:
    root = Path(__file__).resolve().parents[1]
    pyproject = (root / "pyproject.toml").read_text(encoding="utf-8")
    init = (root / "src" / "cursor_goal" / "__init__.py").read_text(encoding="utf-8")
    match_proj = re.search(r'^version\s*=\s*"([^"]+)"', pyproject, re.MULTILINE)
    match_init = re.search(r'__version__\s*=\s*"([^"]+)"', init)
    if match_proj is None or match_init is None:
        print("Could not parse versions", file=sys.stderr)
        return 1
    if match_proj.group(1) != match_init.group(1):
        print(
            f"version mismatch: pyproject={match_proj.group(1)} "
            f"init={match_init.group(1)}",
            file=sys.stderr,
        )
        return 1
    print(f"version OK {match_proj.group(1)}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
