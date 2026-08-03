# Contributing to cursor-goal

Thanks for considering a contribution. This is a single-maintainer AGPL-3.0
project; small, focused pull requests are the fastest path to merge.

## Before you start

- For anything beyond a small fix, open an issue first describing the
  problem and your proposed approach — this avoids wasted work on changes
  that don't fit the project's trust model (see
  [SECURITY.md](SECURITY.md)) or its single-user, trusted-local design.
- Read [README.md](README.md), [docs/known-limitations.md](docs/known-limitations.md),
  and [docs/troubleshooting.md](docs/troubleshooting.md) so your change lines
  up with documented behavior rather than the undocumented wake watchdog.

## Development setup

```bash
git clone https://github.com/tboy1337/cursor-goal.git
cd cursor-goal
pip install -e ".[dev]"
```

Requires Python 3.12+. On Windows, PowerShell 5.1+ is required for the
PowerShell installer/tests.

## Making changes

1. Create a branch from `main`.
2. Keep changes focused — one logical change per pull request.
3. Add or update tests in [`tests/`](tests) for any behavior change. Tests
   run against an isolated `tmp_path` data dir (`CURSOR_GOAL_DATA`), so
   running the suite never touches your real `~/.cursor-goal/data`.
4. If you touch `src/cursor_goal/**`, keep `plugins/cursor-goal/**` and
   `.cursor-plugin/marketplace.json` in sync by regenerating them — **never**
   hand-edit the plugin tree:

   ```bash
   python scripts/sync-plugin-tree.py
   python scripts/sync-plugin-tree.py --check
   ```

5. Update relevant docs (`README.md`, `docs/*.md`,
   `.cursor/skills/goal/SKILL.md`) in the same PR as the behavior change.
6. Add a bullet under `[Unreleased]` in [CHANGELOG.md](CHANGELOG.md).

## Verification before opening a PR

Run the full local verification pipeline — this mirrors CI:

```bash
python scripts/verify.py
```

This runs formatting checks (`black`, `isort`, `pyproject-fmt`), `mypy`,
`pylint`, the full `pytest` suite, the multi-metric coverage gate (statement/
branch/function/combined ≥ 95%), `bandit`/`pip-audit`, shellcheck, and the
plugin-tree sync check. On Windows, also run:

```powershell
powershell -NoProfile -ExecutionPolicy Bypass -File .\scripts\run-powershell-tests.ps1
```

Individual tools, if you want faster iteration:

```bash
pytest tests -q
python scripts/check_coverage_metrics.py
mypy src
pylint src
black --check src tests
isort --check-only src tests
```

## Code standards

- Full type annotations; `mypy.ini` is strict. `Any` is not an acceptable
  substitute for a real type.
- Imports stay at the top of the module — no inline imports.
- Functions with more than ~10 branches should be refactored into helpers or
  a dispatch table; keep cyclomatic complexity low and nesting shallow
  (prefer early returns / guard clauses).
- Never swallow exceptions with a bare `except: pass`; log or handle
  explicitly. Failure modes in this project are deliberate (fail-open vs.
  fail-closed) — see [SECURITY.md](SECURITY.md) before changing one.
- Add logging at decision points and error paths; this harness is meant to
  be debuggable from `CURSOR_GOAL_LOG_FILE=1` output alone.
- Cross-platform by design: code must work on Windows, macOS, and Linux.
  Avoid POSIX-only APIs without a Windows fallback (see `native_path.py`,
  `wake_process.py` for the existing patterns).
- Remove trailing whitespace with
  `py -m autopep8 --in-place --select=W291,W293 <file>` rather than a full
  reformat pass, so diffs stay minimal.

## Security-sensitive changes

Any change touching data-dir trust boundaries, ACL/permission checks, secret
redaction (`redact_secrets`), the stop/wake hooks' fail-open/fail-closed
behavior, or subprocess execution (`run_validation`) should call out the
security implications explicitly in the PR description and update
[SECURITY.md](SECURITY.md) if the threat model changes.

For vulnerabilities, do **not** open a public issue or PR — see
[SECURITY.md](SECURITY.md#reporting-a-vulnerability).

## Commit / PR style

- Commit messages: short imperative summary line, body explaining *why* when
  not obvious.
- Reference the issue you opened (if any) in the PR description.
- Ensure `git status` is clean of build artifacts (`dist/`, `dist-test/`,
  `.coverage`, `coverage.xml`, `__pycache__/`) before pushing.

## License

By contributing, you agree that your contributions are licensed under the
project's [AGPL-3.0-only](COPYING) license.
