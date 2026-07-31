"""Logging setup for the cursor-goal harness."""

from __future__ import annotations

import logging
import os
import sys


def get_logger(name: str = "cursor_goal") -> logging.Logger:
    """Return a module logger configured for stderr diagnostics."""
    logger = logging.getLogger(name)
    if logger.handlers:
        return logger

    level_name = os.environ.get("CURSOR_GOAL_LOG", "WARNING").upper()
    level = getattr(logging, level_name, logging.WARNING)
    logger.setLevel(level)

    handler = logging.StreamHandler(sys.stderr)
    handler.setFormatter(logging.Formatter("[%(name)s] %(levelname)s: %(message)s"))
    logger.addHandler(handler)
    logger.propagate = False
    return logger
