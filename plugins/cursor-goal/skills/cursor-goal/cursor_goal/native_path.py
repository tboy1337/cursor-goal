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

# Bound once at import — ignores later monkeypatches of ``os.name``. UPPER_CASE
# is correct here: this is a constant selecting *which* class to use, not a
# class definition, so pylint's PascalCase class-name check is a false
# positive (no project-wide class-naming regex can distinguish the two).
NATIVE_PATH: type[Path] = (  # pylint: disable=invalid-name
    WindowsPath if os.name == "nt" else PosixPath
)


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
    # WindowsPath / PosixPath are sibling leaf classes (neither subclasses the
    # other), so isinstance() against NATIVE_PATH is exact here — no need for
    # a stricter type() equality check.
    if isinstance(value, NATIVE_PATH):
        return value
    return NATIVE_PATH(os.path.expanduser(str(value)))


def windows_system_root_file(*relative_parts: str) -> Path | None:
    """Return ``%SystemRoot%\\<parts>`` if that file exists.

    Never falls back to PATH. Returns None when SystemRoot is unset or the
    pinned file is missing so a PATH plant cannot substitute icacls,
    taskkill, or powershell.
    """
    if not relative_parts:
        return None
    system_root = (
        os.environ.get("SystemRoot") or os.environ.get("SYSTEMROOT") or ""
    ).strip()
    if not system_root:
        return None
    try:
        candidate = native_path(system_root).joinpath(*relative_parts)
    except (TypeError, ValueError, OSError):
        return None
    try:
        if candidate.is_file():
            return candidate
    except OSError:
        return None
    return None
