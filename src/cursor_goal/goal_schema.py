"""GoalState schema, field setters, and budget clamps."""

from __future__ import annotations

import hashlib
from collections.abc import Callable
from dataclasses import asdict, dataclass
from datetime import UTC, datetime
from typing import Any

from cursor_goal.logging_config import get_logger
from cursor_goal.path_trust import assert_workdir_usable


def now_iso() -> str:
    return datetime.now(UTC).strftime("%Y-%m-%dT%H:%M:%SZ")


logger = get_logger("cursor_goal.state")

SCHEMA_VERSION = 1
SUPPORTED_SCHEMA_VERSIONS = frozenset({1})
MAX_TURN_BUDGET = 500
MAX_FIELD_CHARS = 4000
WAKE_BUDGET_MULTIPLIER = 10
ALLOWED_STATUSES = frozenset(
    {
        "pursuing",
        "paused",
        "blocked",
        "achieved",
        "budget-limited",
        "unknown",
    }
)
_UPDATABLE_FIELDS = frozenset(
    {
        "active",
        "condition",
        "validation_command",
        "created_at",
        "turn_budget",
        "turns_used",
        "wake_ticks",
        "wake_budget",
        "shell_ok",
        "workdir",
        "status",
        "last_reason",
        "last_validation_output",
        "last_validation_exit_code",
        "last_eval_verdict",
        "last_audit_verdict",
        "last_block_reason",
        "block_streak",
        "last_block_turn_key",
        "condition_updated_pending",
        "native_continuation",
    }
)


def clamp_turn_budget(value: int) -> int:
    """Clamp turn budget to [1, MAX_TURN_BUDGET]."""
    if value < 1:
        raise ValueError(f"Budget must be a positive integer, got {value}")
    if value > MAX_TURN_BUDGET:
        logger.warning(
            "turn_budget %s exceeds max %s; clamping", value, MAX_TURN_BUDGET
        )
        return MAX_TURN_BUDGET
    return value


def default_wake_budget(turn_budget: int) -> int:
    """Default wake_budget = clamp(turn_budget * 10, 10, MAX_TURN_BUDGET)."""
    raw = int(turn_budget) * WAKE_BUDGET_MULTIPLIER
    return max(10, min(MAX_TURN_BUDGET, raw))


def clamp_wake_budget(value: int) -> int:
    """Clamp wake budget to [1, MAX_TURN_BUDGET]."""
    if value < 1:
        raise ValueError(f"Wake budget must be a positive integer, got {value}")
    if value > MAX_TURN_BUDGET:
        logger.warning(
            "wake_budget %s exceeds max %s; clamping", value, MAX_TURN_BUDGET
        )
        return MAX_TURN_BUDGET
    return value


def budgets_exhausted(
    turns_used: int,
    turn_budget: int,
    wake_ticks: int,
    wake_budget: int,
) -> bool:
    """True when turn or wake budget is exhausted (independent counters)."""
    return int(turns_used) >= int(turn_budget) or int(wake_ticks) >= int(wake_budget)


def _parse_shell_ok(value: Any) -> bool:
    """Parse shell_ok from JSON / CLI-ish values."""
    if isinstance(value, bool):
        return value
    if isinstance(value, str):
        lowered = value.strip().lower()
        if lowered in {"1", "true", "yes", "on"}:
            return True
        if lowered in {"0", "false", "no", "off"}:
            return False
    if isinstance(value, (int, float)) and value in (0, 1):
        return bool(value)
    raise ValueError(f"shell_ok must be a boolean, got {value!r}")


def _parse_active(value: Any) -> bool:
    if isinstance(value, bool):
        return value
    raise ValueError(
        f"active must be a JSON boolean, got {type(value).__name__}: {value!r}"
    )


def _parse_status(value: Any) -> str:
    status = str(value if value is not None else "unknown")
    if status not in ALLOWED_STATUSES:
        raise ValueError(
            f"invalid status={status!r}; allowed={sorted(ALLOWED_STATUSES)}"
        )
    return status


def _set_active(state: GoalState, value: Any) -> None:
    state.active = _parse_active(value)


def _require_field_chars(name: str, value: str) -> str:
    if len(value) > MAX_FIELD_CHARS:
        raise ValueError(
            f"{name} exceeds {MAX_FIELD_CHARS} character limit ({len(value)} chars)"
        )
    return value


def _clamp_field_chars(name: str, value: str) -> str:
    """Truncate oversized fields on load (corrupt/malicious goal.json recovery)."""
    if len(value) <= MAX_FIELD_CHARS:
        return value
    logger.warning(
        "%s exceeds %s chars on load (%s); truncating",
        name,
        MAX_FIELD_CHARS,
        len(value),
    )
    return value[:MAX_FIELD_CHARS]


def _set_condition(state: GoalState, value: Any) -> None:
    state.condition = _require_field_chars("condition", str(value))


def _set_validation_command(state: GoalState, value: Any) -> None:
    state.validation_command = _require_field_chars(
        "validation_command", str(value or "")
    )


def _set_created_at(state: GoalState, value: Any) -> None:
    state.created_at = _require_field_chars("created_at", str(value))


def _set_turn_budget(state: GoalState, value: Any) -> None:
    state.turn_budget = clamp_turn_budget(int(value))


def _set_turns_used(state: GoalState, value: Any) -> None:
    turns = int(value)
    if turns < 0:
        raise ValueError(f"turns_used must be >= 0, got {turns}")
    state.turns_used = turns


def _set_wake_ticks(state: GoalState, value: Any) -> None:
    ticks = int(value)
    if ticks < 0:
        raise ValueError(f"wake_ticks must be >= 0, got {ticks}")
    state.wake_ticks = ticks


def _set_wake_budget(state: GoalState, value: Any) -> None:
    state.wake_budget = clamp_wake_budget(int(value))


def _set_shell_ok(state: GoalState, value: Any) -> None:
    state.shell_ok = _parse_shell_ok(value)


def _parse_workdir(value: Any) -> str:
    """Parse optional workdir; empty string means unset."""
    if value is None:
        return ""
    text = str(value).strip()
    if not text:
        return ""
    return _require_field_chars("workdir", text)


def _set_workdir(state: GoalState, value: Any) -> None:
    parsed = _parse_workdir(value)
    if parsed:
        parsed = assert_workdir_usable(parsed)
    state.workdir = parsed


def _set_status(state: GoalState, value: Any) -> None:
    state.status = _parse_status(value)


def _set_last_reason(state: GoalState, value: Any) -> None:
    state.last_reason = _require_field_chars("last_reason", str(value or ""))


def _set_last_validation_output(state: GoalState, value: Any) -> None:
    state.last_validation_output = _require_field_chars(
        "last_validation_output", str(value or "")
    )


def _set_last_validation_exit_code(state: GoalState, value: Any) -> None:
    if value is None or value == "":
        state.last_validation_exit_code = None
    else:
        state.last_validation_exit_code = int(value)


def _set_last_eval_verdict(state: GoalState, value: Any) -> None:
    state.last_eval_verdict = _require_field_chars(
        "last_eval_verdict", str(value or "")
    )


def _set_last_audit_verdict(state: GoalState, value: Any) -> None:
    state.last_audit_verdict = _require_field_chars(
        "last_audit_verdict", str(value or "")
    )


def _set_last_block_reason(state: GoalState, value: Any) -> None:
    state.last_block_reason = _require_field_chars(
        "last_block_reason", str(value or "")
    )


def _set_block_streak(state: GoalState, value: Any) -> None:
    streak = int(value)
    if streak < 0:
        raise ValueError(f"block_streak must be >= 0, got {streak}")
    state.block_streak = streak


def _set_last_block_turn_key(state: GoalState, value: Any) -> None:
    state.last_block_turn_key = _require_field_chars(
        "last_block_turn_key", str(value or "")
    )


def _set_condition_updated_pending(state: GoalState, value: Any) -> None:
    if isinstance(value, bool):
        state.condition_updated_pending = value
        return
    raise ValueError(
        "condition_updated_pending must be a JSON boolean, got "
        f"{type(value).__name__}: {value!r}"
    )


def _set_native_continuation(state: GoalState, value: Any) -> None:
    if isinstance(value, bool):
        state.native_continuation = value
        return
    raise ValueError(
        "native_continuation must be a JSON boolean, got "
        f"{type(value).__name__}: {value!r}"
    )


_FIELD_SETTERS: dict[str, Callable[[GoalState, Any], None]] = {
    "active": _set_active,
    "condition": _set_condition,
    "validation_command": _set_validation_command,
    "created_at": _set_created_at,
    "turn_budget": _set_turn_budget,
    "turns_used": _set_turns_used,
    "wake_ticks": _set_wake_ticks,
    "wake_budget": _set_wake_budget,
    "shell_ok": _set_shell_ok,
    "workdir": _set_workdir,
    "status": _set_status,
    "last_reason": _set_last_reason,
    "last_validation_output": _set_last_validation_output,
    "last_validation_exit_code": _set_last_validation_exit_code,
    "last_eval_verdict": _set_last_eval_verdict,
    "last_audit_verdict": _set_last_audit_verdict,
    "last_block_reason": _set_last_block_reason,
    "block_streak": _set_block_streak,
    "last_block_turn_key": _set_last_block_turn_key,
    "condition_updated_pending": _set_condition_updated_pending,
    "native_continuation": _set_native_continuation,
}


def _apply_field(state: GoalState, key: str, value: Any) -> None:
    """Validate and assign a single updatable field."""
    setter = _FIELD_SETTERS.get(key)
    if setter is None:
        raise ValueError(f"unknown goal field: {key}")
    setter(state, value)


@dataclass
class GoalState:  # pylint: disable=too-many-instance-attributes
    active: bool = True
    condition: str = ""
    validation_command: str = ""
    created_at: str = ""
    turn_budget: int = 20
    turns_used: int = 0
    wake_ticks: int = 0
    wake_budget: int = 200
    shell_ok: bool = False
    workdir: str = ""
    status: str = "pursuing"
    last_reason: str = ""
    last_validation_output: str = ""
    last_validation_exit_code: int | None = None
    last_eval_verdict: str = ""
    last_audit_verdict: str = ""
    last_block_reason: str = ""
    block_streak: int = 0
    last_block_turn_key: str = ""
    condition_updated_pending: bool = False
    native_continuation: bool = False
    schema_version: int = SCHEMA_VERSION

    def to_dict(self) -> dict[str, Any]:
        data = asdict(self)
        data["schema_version"] = SCHEMA_VERSION
        return data

    @classmethod
    def from_dict(  # pylint: disable=too-many-branches,too-many-statements,too-many-locals
        cls, data: dict[str, Any]
    ) -> GoalState:
        try:
            turn_budget = clamp_turn_budget(int(data.get("turn_budget", 20)))
            turns_used = int(data.get("turns_used", 0))
            wake_ticks = int(data.get("wake_ticks", 0))
            schema_version = int(data.get("schema_version", SCHEMA_VERSION))
        except (TypeError, ValueError) as exc:
            raise ValueError(f"invalid goal.json numeric fields: {exc}") from exc
        if turns_used < 0:
            raise ValueError(f"turns_used must be >= 0, got {turns_used}")
        if wake_ticks < 0:
            raise ValueError(f"wake_ticks must be >= 0, got {wake_ticks}")
        if schema_version not in SUPPORTED_SCHEMA_VERSIONS:
            raise ValueError(
                f"unsupported schema_version={schema_version}; "
                f"supported={sorted(SUPPORTED_SCHEMA_VERSIONS)} "
                "(clear ~/.cursor-goal/data/goal.json or recreate the goal)"
            )
        try:
            block_streak = int(data.get("block_streak", 0) or 0)
        except (TypeError, ValueError) as exc:
            raise ValueError(f"invalid block_streak: {exc}") from exc
        if block_streak < 0:
            raise ValueError(f"block_streak must be >= 0, got {block_streak}")
        if "condition_updated_pending" not in data:
            condition_updated_pending = False
        else:
            try:
                condition_updated_pending = _parse_active(
                    data.get("condition_updated_pending")
                )
            except ValueError as exc:
                raise ValueError(f"invalid condition_updated_pending: {exc}") from exc

        if "native_continuation" not in data:
            native_continuation = False
        else:
            try:
                native_continuation = _parse_active(data.get("native_continuation"))
            except ValueError as exc:
                raise ValueError(f"invalid native_continuation: {exc}") from exc

        wake_budget_raw = data.get("wake_budget")
        if wake_budget_raw is None or wake_budget_raw == "":
            wake_budget = default_wake_budget(turn_budget)
        else:
            try:
                wake_budget = clamp_wake_budget(int(wake_budget_raw))
            except (TypeError, ValueError) as exc:
                raise ValueError(f"invalid wake_budget: {exc}") from exc

        if "shell_ok" not in data:
            shell_ok = False
        else:
            try:
                shell_ok = _parse_shell_ok(data.get("shell_ok"))
            except ValueError as exc:
                raise ValueError(f"invalid shell_ok: {exc}") from exc

        workdir_raw = data.get("workdir", "")
        try:
            workdir = _clamp_field_chars("workdir", str(workdir_raw or "").strip())
        except (TypeError, ValueError) as exc:
            raise ValueError(f"invalid workdir: {exc}") from exc

        # Clamp counters that exceed their budgets (corrupt / race).
        if turns_used > turn_budget:
            logger.warning(
                "turns_used %s > turn_budget %s; clamping",
                turns_used,
                turn_budget,
            )
            turns_used = turn_budget
        if wake_ticks > wake_budget:
            logger.warning(
                "wake_ticks %s > wake_budget %s; clamping",
                wake_ticks,
                wake_budget,
            )
            wake_ticks = wake_budget

        exit_raw = data.get("last_validation_exit_code", None)
        exit_code: int | None
        if exit_raw is None or exit_raw == "":
            exit_code = None
        else:
            try:
                exit_code = int(exit_raw)
            except (TypeError, ValueError) as exc:
                raise ValueError(f"invalid last_validation_exit_code: {exc}") from exc
        active_raw = data.get("active", True)
        try:
            active = _parse_active(active_raw)
        except ValueError as exc:
            raise ValueError(str(exc)) from exc
        status = _parse_status(data.get("status", "unknown"))
        # Clamp oversized strings on load so stop fail-open is not tripped by
        # length alone; updates still reject via _require_field_chars setters.
        condition = _clamp_field_chars("condition", str(data.get("condition", "")))
        validation_command = _clamp_field_chars(
            "validation_command", str(data.get("validation_command") or "")
        )
        if (
            status == "pursuing"
            and active
            and budgets_exhausted(turns_used, turn_budget, wake_ticks, wake_budget)
        ):
            status = "budget-limited"
            active = False
        return cls(
            active=active,
            condition=condition,
            validation_command=validation_command,
            created_at=_clamp_field_chars(
                "created_at", str(data.get("created_at", ""))
            ),
            turn_budget=turn_budget,
            turns_used=turns_used,
            wake_ticks=wake_ticks,
            wake_budget=wake_budget,
            shell_ok=shell_ok,
            workdir=workdir,
            status=status,
            last_reason=_clamp_field_chars(
                "last_reason", str(data.get("last_reason") or "")
            ),
            last_validation_output=_clamp_field_chars(
                "last_validation_output",
                str(data.get("last_validation_output") or ""),
            ),
            last_validation_exit_code=exit_code,
            last_eval_verdict=_clamp_field_chars(
                "last_eval_verdict", str(data.get("last_eval_verdict") or "")
            ),
            last_audit_verdict=_clamp_field_chars(
                "last_audit_verdict", str(data.get("last_audit_verdict") or "")
            ),
            last_block_reason=_clamp_field_chars(
                "last_block_reason", str(data.get("last_block_reason") or "")
            ),
            block_streak=block_streak,
            last_block_turn_key=_clamp_field_chars(
                "last_block_turn_key",
                str(data.get("last_block_turn_key") or ""),
            ),
            condition_updated_pending=condition_updated_pending,
            native_continuation=native_continuation,
            schema_version=SCHEMA_VERSION,
        )

    def content_hash(self) -> str:
        """Stable hash binding eval signals to this goal identity."""
        payload = (
            f"{self.condition}\0{self.created_at}\0{self.validation_command}"
        ).encode("utf-8")
        return hashlib.sha256(payload).hexdigest()[:32]
