"""Host-native path helpers resilient to ``os.name`` monkeypatches.

Python 3.13+ ``pathlib.Path`` dispatches to ``WindowsPath`` / ``PosixPath`` based
on the *live* ``os.name``. Tests that set ``os.name = \"nt\"`` on Linux then get
ghost ``WindowsPath`` objects where ``/tmp/...`` is not absolute and
``Path(\"scripts\") / ...`` raises ``UnsupportedOperation``.

Capture the real concrete path class at import time and prefer ``os.path.isabs``
(bound to the host path module) for absolute checks.
"""

from __future__ import annotations

import os
from pathlib import Path, PosixPath, WindowsPath

# Bound once at import — ignores later monkeypatches of ``os.name``.
NATIVE_PATH: type[Path] = WindowsPath if os.name == "nt" else PosixPath


def path_str_is_absolute(value: str) -> bool:
    """Return True when *value* looks like an absolute filesystem path."""
    text = value.strip().strip('"')
    if not text:
        return False
    expanded = os.path.expanduser(text)
    # ``os.path`` stays bound to the host module even when ``os.name`` is mocked.
    if os.path.isabs(expanded):
        return True
    # POSIX absolute (also covers WindowsPath.is_absolute False for /tmp/...).
    if text.startswith("/") and not text.startswith("//"):
        return True
    # Windows drive / UNC without relying solely on Path flavor.
    if len(text) >= 3 and text[1] == ":" and text[2] in "\\/":
        return True
    if text.startswith("\\\\") or text.startswith("//"):
        return True
    return False


def native_path(value: str | Path) -> Path:
    """Build a Path using the host OS flavor (safe under ``os.name`` mocks)."""
    if isinstance(value, Path) and type(value) is NATIVE_PATH:
        return value
    return NATIVE_PATH(os.path.expanduser(str(value)))
