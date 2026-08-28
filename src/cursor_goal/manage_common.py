"""Shared manage helpers used by create and other lifecycle commands."""

from __future__ import annotations

from cursor_goal.logging_config import get_logger
from cursor_goal.state import (
    refuse_if_acl_harden_failed,
    refuse_if_data_dir_insecure,
)
from cursor_goal.wake import wake_enabled

logger = get_logger("cursor_goal.manage")


def wake_wanted(*, native_requested: bool = False) -> bool:
    """Whether create should pause-for-wake (native continuation skips wake)."""
    if native_requested:
        return False
    return wake_enabled()


def print_native_continuation_notes(*, achieved: bool = False) -> None:
    """Remind the agent how native CreateGoal/UpdateGoal layers with this harness."""
    if achieved:
        print(
            "[goal] Native continuation: call UpdateGoal with status "
            '"complete" so usage accounting is preserved.'
        )
        return
    print(
        "[goal] Native continuation: CreateGoal/UpdateGoal owns keep-going. "
        "Worker stop followups and wake are off. subagentStop still parses "
        "auditor/evaluator. After manage done, call UpdateGoal complete. "
        "Budgets are advisory (native runtime has no turn budget). On "
        "blocked/budget-limited, the user must pause the native /goal "
        "(CLI Ctrl+C / UI pause)."
    )


def refuse_if_data_dir_unsafe() -> str | None:
    """Return an error message if the data dir is insecure or Windows ACL
    hardening failed, else ``None``. Mirrors the gate every mutating command
    in ``stop.py``/``evaluate.py``/``wake.py`` already applies so a failed
    ACL harden cannot be bypassed just by using ``manage pause``/``resume``/
    ``done``/``clear`` instead of the hook/eval entry points.
    """
    insecure = refuse_if_data_dir_insecure()
    if insecure is not None:
        return insecure
    return refuse_if_acl_harden_failed()
