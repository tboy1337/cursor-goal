"""CLI entry point for cursor-goal."""

from __future__ import annotations

import sys
from collections.abc import Callable

from cursor_goal import __version__
from cursor_goal.evaluate import cmd_eval
from cursor_goal.logging_config import get_logger
from cursor_goal.manage import cmd_manage
from cursor_goal.parse import cmd_parse
from cursor_goal.stop import cmd_stop
from cursor_goal.wake import cmd_wake

logger = get_logger("cursor_goal.cli")


def main(argv: list[str] | None = None) -> int:
    args = list(sys.argv[1:] if argv is None else argv)
    if not args or args[0] in {"-h", "--help", "help"}:
        _print_help()
        return 0 if args and args[0] in {"-h", "--help", "help"} else 1

    if args[0] in {"-V", "--version", "version"}:
        print(f"cursor-goal {__version__}")
        return 0

    command = args[0]
    rest = args[1:]
    dispatch: dict[str, Callable[[list[str]], int]] = {
        "parse": cmd_parse,
        "manage": cmd_manage,
        "eval": cmd_eval,
        "stop": cmd_stop,
        "wake": cmd_wake,
    }
    handler = dispatch.get(command)
    if handler is None:
        print(f"[cursor-goal] Unknown command: {command}", file=sys.stderr)
        _print_help()
        return 1

    logger.info("Running command=%s argc=%s", command, len(rest))
    try:
        return int(handler(rest))
    except KeyboardInterrupt:
        print("[cursor-goal] Interrupted.", file=sys.stderr)
        return 130


def _print_help() -> None:
    print("Usage: cursor-goal <command> [args...]")
    print('  parse "<raw /goal input>"     Parse input to JSON')
    print("  manage <subcommand> [...]     Goal lifecycle")
    print(
        "                                "
        "(create|status|doctor|pause|resume|done|clear)"
    )
    print("  eval <subcommand> [...]       Evaluator harness")
    print(
        "                                "
        "(validate|spawn-config|prompt|parse-result|signal|check)"
    )
    print(
        "  stop                          Cursor stop hook (stdin JSON -> stdout JSON)"
    )
    print("  wake <arm|tick|disarm|status|loop>  Goal wake watchdog")
    print("  --version                     Print package version")


if __name__ == "__main__":
    raise SystemExit(main())
