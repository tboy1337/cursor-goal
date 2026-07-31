"""
A monolithic module that does too many things.

Contains: user management, email sending, logging, config parsing, validation.
This module is intentionally oversized for refactor/split workload testing.
"""

from __future__ import annotations

import re
from typing import Any, Dict, List, Optional, Tuple

# ---------------------------------------------------------------------------
# Module-level constants
# ---------------------------------------------------------------------------

DEFAULT_SMTP_HOST = "localhost"
DEFAULT_SMTP_PORT = 587
DEFAULT_LOG_LEVEL = "INFO"
MAX_USERS = 10_000
SUPPORTED_LOG_LEVELS = ("DEBUG", "INFO", "WARN", "ERROR")
PRIORITY_ORDER = {"low": 0, "medium": 1, "high": 2}
EMAIL_SUBJECT_MAX_LEN = 255
CONFIG_COMMENT_PREFIX = "#"
VALID_BOOL_STRINGS = ("true", "1", "yes", "on")


# ---------------------------------------------------------------------------
# Utility functions
# ---------------------------------------------------------------------------

def normalize_email(email: str) -> str:
    """Return a lowercased, stripped email address."""
    return email.strip().lower()


def truncate_subject(subject: str, max_len: int = EMAIL_SUBJECT_MAX_LEN) -> str:
    """Truncate email subject to maximum allowed length."""
    if len(subject) <= max_len:
        return subject
    return subject[: max_len - 3] + "..."


def merge_user_records(existing: Dict[str, Any], updates: Dict[str, Any]) -> Dict[str, Any]:
    """Shallow-merge user record updates into an existing record."""
    merged = dict(existing)
    merged.update(updates)
    return merged


def format_log_entry(level: str, message: str) -> str:
    """Format a log line for display."""
    return f"[{level}] {message}"


def parse_key_value_line(line: str) -> Optional[Tuple[str, str]]:
    """Parse a single key=value config line, ignoring comments."""
    stripped = line.strip()
    if not stripped or stripped.startswith(CONFIG_COMMENT_PREFIX):
        return None
    if "=" not in stripped:
        return None
    key, val = stripped.split("=", 1)
    return key.strip(), val.strip()


def clamp(value: float, min_val: float, max_val: float) -> float:
    """Clamp numeric value to inclusive range."""
    return max(min_val, min(value, max_val))


def is_valid_log_level(level: str) -> bool:
    """Return True if level is a supported log level."""
    return level.upper() in SUPPORTED_LOG_LEVELS


# ---------------------------------------------------------------------------
# Domain classes
# ---------------------------------------------------------------------------

class UserManager:
    def __init__(self):
        self.users = {}

    def create_user(self, name, email):
        self.users[email] = {"name": name, "email": email, "active": True}
        return self.users[email]

    def delete_user(self, email):
        if email in self.users:
            del self.users[email]

    def get_user(self, email):
        return self.users.get(email)

    def list_users(self):
        return list(self.users.values())

    def deactivate_user(self, email):
        if email in self.users:
            self.users[email]["active"] = False

    def activate_user(self, email):
        if email in self.users:
            self.users[email]["active"] = True


class EmailSender:
    def __init__(self, smtp_host="localhost", smtp_port=587):
        self.smtp_host = smtp_host
        self.smtp_port = smtp_port
        self.sent = []

    def send(self, to, subject, body):
        msg = {"to": to, "subject": subject, "body": body}
        self.sent.append(msg)
        return True

    def send_bulk(self, recipients, subject, body):
        for r in recipients:
            self.send(r, subject, body)

    def get_history(self):
        return self.sent


class Logger:
    def __init__(self, level="INFO"):
        self.level = level
        self.logs = []

    def info(self, msg):
        self.logs.append(("INFO", msg))

    def warn(self, msg):
        self.logs.append(("WARN", msg))

    def error(self, msg):
        self.logs.append(("ERROR", msg))

    def debug(self, msg):
        if self.level == "DEBUG":
            self.logs.append(("DEBUG", msg))

    def get_logs(self, level=None):
        if level:
            return [(l, m) for l, m in self.logs if l == level]
        return self.logs


class ConfigParser:
    def __init__(self):
        self.config = {}

    def load(self, data):
        for line in data.strip().split("\n"):
            if "=" in line and not line.startswith("#"):
                key, val = line.split("=", 1)
                self.config[key.strip()] = val.strip()

    def get(self, key, default=None):
        return self.config.get(key, default)

    def get_int(self, key, default=0):
        try:
            return int(self.config.get(key, default))
        except (ValueError, TypeError):
            return default

    def get_bool(self, key, default=False):
        val = self.config.get(key, "").lower()
        return val in ("true", "1", "yes")

    def keys(self) -> List[str]:
        """Return all config keys."""
        return list(self.config.keys())


class Validator:
    @staticmethod
    def is_email(value):
        return "@" in value and "." in value.split("@")[1]

    @staticmethod
    def is_positive(value):
        return isinstance(value, (int, float)) and value > 0

    @staticmethod
    def is_non_empty(value):
        return bool(value and str(value).strip())

    @staticmethod
    def is_in_range(value, min_val, max_val):
        return min_val <= value <= max_val

    @staticmethod
    def matches_pattern(value, pattern):
        return bool(re.match(pattern, str(value)))
