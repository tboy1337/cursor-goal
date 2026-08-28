"""Installer backup, migrate, prune, and restore helpers.

Keep skill trees out of ``~/.cursor/skills/*.bak.*`` so Cursor does not
discover leftover ``SKILL.md`` files as extra skills. Backups live under
``~/.cursor-goal/backups/``.
"""

from __future__ import annotations

import argparse
import json
import logging
import os
import shutil
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from cursor_goal.logging_config import get_logger
from cursor_goal.native_path import native_path

logger = get_logger("cursor_goal.install_backup")

SKILL_NAME = "cursor-goal"
LEGACY_SKILL_NAME = "goal"
AGENT_NAMES = ("goalKeeper.md", "goal-evaluator.md", "goal-auditor.md")
KEEP_DEFAULT = 1


def resolve_home(home: str | Path) -> Path:
    """Return an absolute home path without requiring it to exist."""
    path = native_path(home)
    logger.debug("resolve_home raw=%s resolved=%s", home, path)
    return path


def backups_root(home: Path) -> Path:
    """``<home>/.cursor-goal/backups``."""
    return resolve_home(home) / ".cursor-goal" / "backups"


def skills_dir(home: Path) -> Path:
    return resolve_home(home) / ".cursor" / "skills"


def agents_dir(home: Path) -> Path:
    return resolve_home(home) / ".cursor" / "agents"


def install_dir(home: Path) -> Path:
    return skills_dir(home) / SKILL_NAME


def legacy_install_dir(home: Path) -> Path:
    return skills_dir(home) / LEGACY_SKILL_NAME


def utc_stamp() -> str:
    """UTC stamp matching the old installer ``YYYYMMDDTHHMMSSZ`` format."""
    return datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")


def _ensure_dir(path: Path) -> Path:
    path.mkdir(parents=True, exist_ok=True)
    return path


def _copy_tree(src: Path, dest: Path) -> None:
    """Copy *src* to *dest*, replacing dest if it exists. Skip ``__pycache__``."""
    if dest.exists():
        shutil.rmtree(dest)
    logger.info("Copying tree src=%s dest=%s", src, dest)

    def _ignore(_directory: str, names: list[str]) -> set[str]:
        return {name for name in names if name == "__pycache__"}

    shutil.copytree(src, dest, ignore=_ignore)


def _unique_dir(parent: Path, name: str) -> Path:
    """Return ``parent/name``, or ``parent/name-N`` if that path exists."""
    candidate = parent / name
    if not candidate.exists():
        return candidate
    index = 2
    while True:
        alt = parent / f"{name}-{index}"
        if not alt.exists():
            return alt
        index += 1


def _skill_has_markdown(path: Path) -> bool:
    return path.is_dir() and (path / "SKILL.md").is_file()


def backup_skill_tree(src: Path, dest_parent: Path, stamp: str) -> Path:
    """Copy *src* into ``dest_parent/<stamp>/`` (or a unique suffix)."""
    dest = _unique_dir(dest_parent, stamp)
    _copy_tree(src, dest)
    logger.info("Backed up skill tree %s -> %s", src, dest)
    return dest


def list_legacy_skill_bak_dirs(home: Path) -> list[Path]:
    """``~/.cursor/skills/{goal,cursor-goal}.bak.*`` directories."""
    parent = skills_dir(home)
    if not parent.is_dir():
        return []
    prefixes = (f"{LEGACY_SKILL_NAME}.bak.", f"{SKILL_NAME}.bak.")
    found = sorted(
        path
        for path in parent.iterdir()
        if path.is_dir() and path.name.startswith(prefixes)
    )
    logger.debug("in-root bak dirs count=%s", len(found))
    return found


def migrate_legacy_bak_dirs(home: Path, stamp: str) -> list[str]:
    """Move in-root ``goal.bak.*`` folders into ``backups/skill/``."""
    dest_parent = _ensure_dir(backups_root(home) / "skill")
    moved: list[str] = []
    for bak in list_legacy_skill_bak_dirs(home):
        dest = _unique_dir(dest_parent, f"{stamp}-{bak.name}")
        logger.info("Migrating leftover backup %s -> %s", bak, dest)
        shutil.move(str(bak), str(dest))
        moved.append(str(dest))
    return moved


def backup_agents(home: Path, stamp: str) -> dict[str, str | None]:
    """Copy existing managed agent files into ``backups/agents/<stamp>/``.

    Returns a map of agent filename → backup path (or ``None`` if the live
    file did not exist, so rollback can delete rather than restore).
    """
    live_dir = agents_dir(home)
    result: dict[str, str | None] = {}
    dest_dir: Path | None = None
    for name in AGENT_NAMES:
        live = live_dir / name
        if not live.is_file():
            result[name] = None
            continue
        if dest_dir is None:
            dest_dir = _ensure_dir(backups_root(home) / "agents" / stamp)
        dest = dest_dir / name
        shutil.copy2(live, dest)
        logger.info("Backed up agent %s -> %s", live, dest)
        result[name] = str(dest)
    return result


def backup_hooks_file(home: Path, stamp: str) -> str | None:
    """Copy ``hooks.json`` into ``backups/hooks/`` when it exists."""
    hooks = resolve_home(home) / ".cursor" / "hooks.json"
    if not hooks.is_file():
        return None
    dest_dir = _ensure_dir(backups_root(home) / "hooks")
    dest = dest_dir / f"hooks.json.bak.{stamp}"
    if dest.exists():
        dest = dest_dir / f"hooks.json.bak.{stamp}-{os.getpid()}"
    shutil.copy2(hooks, dest)
    logger.info("Backed up hooks.json -> %s", dest)
    return str(dest)


def backup_before(home: Path) -> dict[str, Any]:
    """Snapshot live skill/agents/hooks and migrate leftover ``goal.bak.*``.

    Does **not** delete the live ``cursor-goal`` tree (the installer overwrites
    it in place). Does copy then delete the legacy ``goal`` skill so Customize
    cannot show two user skills.
    """
    stamp = utc_stamp()
    logger.info("backup_before home=%s stamp=%s", home, stamp)
    skill_parent = _ensure_dir(backups_root(home) / "skill")
    current = install_dir(home)
    legacy = legacy_install_dir(home)
    skill_backup: str | None = None
    skill_backup_source: str | None = None
    if _skill_has_markdown(current):
        dest = backup_skill_tree(current, skill_parent, stamp)
        skill_backup = str(dest)
        skill_backup_source = SKILL_NAME
    elif _skill_has_markdown(legacy):
        dest = backup_skill_tree(legacy, skill_parent, f"{stamp}-legacy")
        skill_backup = str(dest)
        skill_backup_source = LEGACY_SKILL_NAME

    migrated = migrate_legacy_bak_dirs(home, stamp)
    if _skill_has_markdown(legacy):
        logger.info("Removing legacy skill tree %s", legacy)
        shutil.rmtree(legacy)
        migrated.append(str(legacy))

    manifest: dict[str, Any] = {
        "stamp": stamp,
        "skill_backup": skill_backup,
        "skill_backup_source": skill_backup_source,
        "legacy_migrated": migrated,
        "agents": backup_agents(home, stamp),
        "hooks_backup": backup_hooks_file(home, stamp),
    }
    logger.info(
        "backup_before done source=%s backup=%s migrated=%s",
        skill_backup_source,
        skill_backup,
        len(migrated),
    )
    return manifest


def _sorted_stamp_dirs(parent: Path) -> list[Path]:
    if not parent.is_dir():
        return []
    return sorted(
        (path for path in parent.iterdir() if path.is_dir()),
        key=lambda path: path.name,
        reverse=True,
    )


def _sorted_files(parent: Path) -> list[Path]:
    if not parent.is_dir():
        return []
    return sorted(
        (path for path in parent.iterdir() if path.is_file()),
        key=lambda path: path.name,
        reverse=True,
    )


def prune_backups(home: Path, *, keep: int = KEEP_DEFAULT) -> dict[str, int]:
    """Keep the newest *keep* skill/agent/hooks backups; delete the rest."""
    if keep < 1:
        raise ValueError(f"keep must be >= 1, got {keep}")
    root = backups_root(home)
    removed = {"skill": 0, "agents": 0, "hooks": 0}
    skill_dirs = _sorted_stamp_dirs(root / "skill")
    for extra in skill_dirs[keep:]:
        logger.info("Pruning old skill backup %s", extra)
        shutil.rmtree(extra, ignore_errors=True)
        removed["skill"] += 1
    agent_dirs = _sorted_stamp_dirs(root / "agents")
    for extra in agent_dirs[keep:]:
        logger.info("Pruning old agent backup %s", extra)
        shutil.rmtree(extra, ignore_errors=True)
        removed["agents"] += 1
    hook_files = _sorted_files(root / "hooks")
    for extra in hook_files[keep:]:
        logger.info("Pruning old hooks backup %s", extra)
        extra.unlink(missing_ok=True)
        removed["hooks"] += 1

    leftover_goal = legacy_install_dir(home)
    if leftover_goal.exists():
        logger.info("Removing leftover legacy skill %s", leftover_goal)
        shutil.rmtree(leftover_goal, ignore_errors=True)
    for bak in list_legacy_skill_bak_dirs(home):
        logger.info("Removing leftover in-root bak %s", bak)
        shutil.rmtree(bak, ignore_errors=True)
        removed["skill"] += 1
    agents = agents_dir(home)
    if agents.is_dir():
        for name in AGENT_NAMES:
            for bak in agents.glob(f"{name}.bak.*"):
                logger.info("Removing leftover agent bak %s", bak)
                bak.unlink(missing_ok=True)
    logger.info("prune_backups keep=%s removed=%s", keep, removed)
    return removed


def _restore_skill(home: Path, manifest: dict[str, Any]) -> None:
    dest = install_dir(home)
    backup_raw = manifest.get("skill_backup")
    source = manifest.get("skill_backup_source")
    if not isinstance(backup_raw, str) or not backup_raw:
        logger.error("No skill backup in manifest; leaving %s as installed", dest)
        return
    backup = native_path(backup_raw)
    if not backup.is_dir():
        logger.error("Skill backup missing at %s", backup)
        return
    if dest.exists():
        shutil.rmtree(dest)
    if source == LEGACY_SKILL_NAME:
        legacy = legacy_install_dir(home)
        if legacy.exists():
            shutil.rmtree(legacy)
        logger.warning("Restoring legacy skill from %s -> %s", backup, legacy)
        shutil.move(str(backup), str(legacy))
        return
    logger.warning("Restoring skill from %s -> %s", backup, dest)
    shutil.move(str(backup), str(dest))


def _restore_hooks(home: Path, manifest: dict[str, Any]) -> None:
    hooks_raw = manifest.get("hooks_backup")
    if not isinstance(hooks_raw, str) or not hooks_raw:
        return
    bak = native_path(hooks_raw)
    hooks_live = resolve_home(home) / ".cursor" / "hooks.json"
    if not bak.is_file():
        logger.error("hooks backup missing at %s", bak)
        return
    logger.warning("Restoring hooks.json from %s", bak)
    shutil.copy2(bak, hooks_live)


def _restore_one_agent(live_dir: Path, name: str, bak_raw: object) -> None:
    live = live_dir / name
    if isinstance(bak_raw, str) and bak_raw:
        bak = native_path(bak_raw)
        if bak.is_file():
            logger.warning("Restoring agent %s from %s", name, bak)
            shutil.move(str(bak), str(live))
        return
    if live.is_file():
        logger.warning("Removing agent %s installed this run (no prior file)", name)
        live.unlink()


def _restore_agents(home: Path, manifest: dict[str, Any]) -> None:
    agents = manifest.get("agents")
    if not isinstance(agents, dict):
        return
    live_dir = agents_dir(home)
    for name in AGENT_NAMES:
        _restore_one_agent(live_dir, name, agents.get(name))


def restore_after_failure(home: Path, manifest: dict[str, Any]) -> None:
    """Roll back skill + agent files using a ``backup_before`` manifest."""
    logger.info("restore_after_failure home=%s", home)
    _restore_skill(home, manifest)
    _restore_hooks(home, manifest)
    _restore_agents(home, manifest)


def uninstall_debris(home: Path) -> None:
    """Remove v4 leftover skill trees, in-root bak dirs, and backup root."""
    for path in (install_dir(home), legacy_install_dir(home)):
        if path.exists():
            logger.info("uninstall removing skill %s", path)
            shutil.rmtree(path, ignore_errors=True)
    for bak in list_legacy_skill_bak_dirs(home):
        logger.info("uninstall removing bak %s", bak)
        shutil.rmtree(bak, ignore_errors=True)
    root = backups_root(home)
    if root.exists():
        logger.info("uninstall removing backup root %s", root)
        shutil.rmtree(root, ignore_errors=True)
    agents = agents_dir(home)
    if agents.is_dir():
        for name in AGENT_NAMES:
            for bak in agents.glob(f"{name}.bak.*"):
                bak.unlink(missing_ok=True)


def _decode_manifest_text(raw: bytes) -> str:
    """Decode installer JSON, including UTF-16 from PowerShell ``1>`` redirects."""
    for encoding in ("utf-8-sig", "utf-16"):
        try:
            text = raw.decode(encoding)
        except UnicodeDecodeError:
            logger.debug("manifest decode failed encoding=%s", encoding)
            continue
        logger.debug("manifest decoded encoding=%s bytes=%s", encoding, len(raw))
        return text
    raise ValueError("manifest is not valid UTF-8 or UTF-16")


def _emit_json(payload: dict[str, Any], dest: Path | None) -> None:
    """Print JSON on stdout; optionally write UTF-8 to *dest* (installer rollback)."""
    text = json.dumps(payload, ensure_ascii=False) + "\n"
    sys.stdout.write(text)
    sys.stdout.flush()
    if dest is None:
        return
    dest.parent.mkdir(parents=True, exist_ok=True)
    dest.write_text(text, encoding="utf-8")
    logger.info("Wrote UTF-8 JSON %s", dest)


def _load_manifest(path: Path) -> dict[str, Any]:
    logger.info("Loading backup manifest %s", path)
    data = json.loads(_decode_manifest_text(path.read_bytes()))
    if not isinstance(data, dict):
        raise ValueError(f"manifest must be a JSON object, got {type(data).__name__}")
    return data


def main(argv: list[str] | None = None) -> int:
    """CLI: backup-before, prune-after, restore, or uninstall-debris."""
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--home", required=True, help="User home (install target)")
    parser.add_argument(
        "action",
        choices=("backup-before", "prune-after", "restore", "uninstall-debris"),
    )
    parser.add_argument(
        "--manifest",
        default="",
        help="UTF-8 JSON path (written by backup-before; required for restore)",
    )
    parser.add_argument(
        "--keep",
        type=int,
        default=KEEP_DEFAULT,
        help="Backups to keep after a successful install (default 1)",
    )
    args = parser.parse_args(argv)
    home = resolve_home(args.home)
    logger.info("install_backup action=%s home=%s", args.action, home)
    if args.action == "backup-before":
        dest = native_path(args.manifest) if args.manifest else None
        _emit_json(backup_before(home), dest)
        return 0
    if args.action == "prune-after":
        _emit_json(prune_backups(home, keep=args.keep), None)
        return 0
    if args.action == "restore":
        if not args.manifest:
            print("restore requires --manifest", file=sys.stderr)
            return 1
        try:
            restore_after_failure(home, _load_manifest(native_path(args.manifest)))
        except (OSError, ValueError, json.JSONDecodeError) as exc:
            logger.error("restore failed: %s", exc)
            return 1
        return 0
    uninstall_debris(home)
    return 0


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO)
    raise SystemExit(main())
