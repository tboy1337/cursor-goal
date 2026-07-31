"""Cursor stop-hook entrypoint for installed skill tree.

Resolves the vendored ``cursor_goal`` package next to this scripts directory
(or one level up) so the hook works without a global pip install.
"""

from __future__ import annotations

import sys
from pathlib import Path


def _ensure_package_path() -> None:
    scripts_dir = Path(__file__).resolve().parent
    skill_dir = scripts_dir.parent
    for candidate in (skill_dir, scripts_dir, skill_dir.parent.parent.parent / "src"):
        if (candidate / "cursor_goal" / "__init__.py").is_file():
            path = str(candidate)
            if path not in sys.path:
                sys.path.insert(0, path)
            return


_ensure_package_path()

from cursor_goal.stop import cmd_stop  # noqa: E402


def main() -> int:
    return cmd_stop([])


if __name__ == "__main__":
    raise SystemExit(main())
