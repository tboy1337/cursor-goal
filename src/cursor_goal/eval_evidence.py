"""EXPLORED-block evidence checks for broad remaining-work CLEAR verdicts."""

from __future__ import annotations

import re
from pathlib import Path

from cursor_goal.logging_config import get_logger
from cursor_goal.state import GoalState, assert_workdir_usable
from cursor_goal.validation import is_broad_condition

logger = get_logger("cursor_goal.eval")

BROAD_CLEAR_MIN_CITED_FILES = 6
BROAD_CLEAR_MIN_CITED_DIRS = 2
_AUDIT_LINE = re.compile(r"^(CLEAR|REMAINING):\s*(.*)$", re.IGNORECASE)
_EXPLORED_HEADER = re.compile(r"(?i)^(?:\*{0,2})EXPLORED:(?:\*{0,2})?\s*(.*)$")
_PATH_TOKEN = re.compile(
    r"(?:[A-Za-z]:)?(?:(?:\.{1,2})?[/\\])?(?:[\w.+-]+[/\\])+[\w.+-]+"
)


def extract_explored_block(text: str) -> str | None:
    """Return the EXPLORED section body, or None if the header is missing."""
    lines = text.splitlines()
    start: int | None = None
    header_inline = ""
    for index, line in enumerate(lines):
        match = _EXPLORED_HEADER.match(line.strip())
        if match is None:
            continue
        start = index
        header_inline = (match.group(1) or "").strip()
        break
    if start is None:
        logger.info("extract_explored_block: no EXPLORED header")
        return None
    body_parts: list[str] = []
    if header_inline:
        body_parts.append(header_inline)
    for line in lines[start + 1 :]:
        stripped = line.strip()
        if not stripped:
            continue
        if _AUDIT_LINE.match(stripped) or stripped.upper().startswith("VERDICT:"):
            break
        body_parts.append(line)
    body = "\n".join(body_parts).strip()
    logger.info("extract_explored_block chars=%s", len(body))
    return body


def cited_path_tokens(explored_body: str) -> list[str]:
    """Split an EXPLORED body into unique path-like tokens."""
    tokens: list[str] = []
    for raw in re.split(r"[\s,;]+", explored_body):
        cleaned = raw.strip().strip("`\"'[](){}")
        if cleaned:
            tokens.append(cleaned)
    for match in _PATH_TOKEN.finditer(explored_body):
        tokens.append(match.group(0).strip("`\"'"))
    seen: set[str] = set()
    unique: list[str] = []
    for token in tokens:
        if token in seen:
            continue
        seen.add(token)
        unique.append(token)
    logger.debug("cited_path_tokens count=%s", len(unique))
    return unique


def _audit_evidence_roots(state: GoalState) -> list[Path]:
    """Workdir (if usable) plus cwd — files must resolve under one of these."""
    roots: list[Path] = []
    workdir = (state.workdir or "").strip()
    if workdir:
        try:
            roots.append(Path(assert_workdir_usable(workdir)))
        except ValueError as exc:
            logger.info("audit evidence workdir unusable: %s", exc)
    try:
        roots.append(Path.cwd().resolve())
    except OSError as exc:
        logger.info("audit evidence cwd unusable: %s", exc)
    unique: list[Path] = []
    seen: set[Path] = set()
    for root in roots:
        try:
            resolved = root.resolve()
        except OSError:
            continue
        if resolved in seen:
            continue
        seen.add(resolved)
        unique.append(resolved)
    logger.info("audit evidence roots=%s", [str(p) for p in unique])
    return unique


def _resolve_cited_file(raw: str, roots: list[Path]) -> Path | None:
    """Return *raw* as an existing file under one of *roots*, else None."""
    cleaned = raw.strip().strip("`\"'")
    if not cleaned or cleaned.lower() in {":", "-", "n/a", "na"}:
        return None
    if "://" in cleaned:
        return None
    candidate = Path(cleaned)
    for root in roots:
        try:
            if candidate.is_absolute():
                resolved = candidate.resolve()
            else:
                resolved = (root / candidate).resolve()
            if not resolved.is_file():
                continue
            resolved.relative_to(root)
            return resolved
        except (OSError, ValueError):
            continue
    return None


def existing_explored_files(text: str, roots: list[Path]) -> list[Path]:
    """Existing files cited in the EXPLORED block, unique by resolved path."""
    body = extract_explored_block(text)
    if body is None:
        return []
    found: list[Path] = []
    seen: set[Path] = set()
    for token in cited_path_tokens(body):
        resolved = _resolve_cited_file(token, roots)
        if resolved is None or resolved in seen:
            continue
        seen.add(resolved)
        found.append(resolved)
    logger.info("existing_explored_files count=%s", len(found))
    return found


def broad_clear_evidence_ok(text: str, *, roots: list[Path]) -> tuple[bool, str]:
    """True when a broad CLEAR cites enough existing files in EXPLORED."""
    if extract_explored_block(text) is None:
        return False, "missing EXPLORED block"
    files = existing_explored_files(text, roots)
    if len(files) < BROAD_CLEAR_MIN_CITED_FILES:
        return (
            False,
            f"need {BROAD_CLEAR_MIN_CITED_FILES} existing file cites "
            f"(got {len(files)})",
        )
    dirs = {path.parent for path in files}
    if len(dirs) < BROAD_CLEAR_MIN_CITED_DIRS:
        return (
            False,
            f"cites must span at least {BROAD_CLEAR_MIN_CITED_DIRS} directories "
            f"(got {len(dirs)})",
        )
    return True, ""


def maybe_reject_broad_clear(
    verdict: str, reason: str, result: str, state: GoalState | None
) -> tuple[str, str]:
    """Downgrade a broad CLEAR without EXPLORED evidence to UNCLEAR."""
    if verdict != "CLEAR" or state is None:
        return verdict, reason
    if not is_broad_condition(state.condition):
        return verdict, reason
    ok, why = broad_clear_evidence_ok(result, roots=_audit_evidence_roots(state))
    if ok:
        logger.info("broad CLEAR evidence accepted")
        return verdict, reason
    logger.info("broad CLEAR rejected: %s", why)
    return (
        "UNCLEAR",
        f"Broad CLEAR rejected ({why}). Treat as REMAINING and continue.",
    )
