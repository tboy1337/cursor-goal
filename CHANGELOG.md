# Changelog

All notable changes to `cursor-goal` are documented here. Format follows
[Keep a Changelog](https://keepachangelog.com/en/1.1.0/); versioning follows
[Semantic Versioning](https://semver.org/).

## [4.0.0]

### Fixed

- **Critical: the wake watchdog's liveness probe could kill the process it
  was checking, on Windows.** `_pid_alive()` called `os.kill(pid, 0)`
  unconditionally on all platforms to mean "check whether this PID exists,"
  matching the POSIX idiom where signal `0` sends nothing and only checks
  permissions/existence. On Windows, CPython's `os.kill()` shim has no such
  no-op: for any signal value other than `CTRL_C_EVENT`/`CTRL_BREAK_EVENT`
  (which target console process *groups*, not this PID) it calls
  `TerminateProcess(pid, sig)` — so `os.kill(pid, 0)` unconditionally
  **terminated** the target process (with exit code 0) if it was running and
  accessible, instead of merely probing it. `_pid_alive()` is called
  pervasively (`wake tick`, `disarm`, `run_loop`, `refuse_if_wake_dead`,
  `manage doctor`/`status`) against PIDs read from `wake.pid`, so on Windows
  this could silently kill a live, healthy wake loop (or, in the worst case,
  another live process if `wake.pid` pointed at a reused PID) every time its
  liveness was checked. Windows now uses a real, non-destructive probe
  (`OpenProcess` + `GetExitCodeProcess` via `ctypes`) instead of `os.kill`;
  the POSIX `os.kill(pid, 0)` behavior is unchanged on Unix.
- **Evaluator model default was not a real Cursor model.** `DEFAULT_EVAL_MODEL` was
  `"fast"`, which is not a valid Cursor subagent `model` value (only a bracket
  parameter such as `composer-2.5[fast=false]`); the checker likely ran on the
  worker's model, silently voiding the maker ≠ checker guarantee. Default is now
  `composer-2.5` (Cursor Models pool). `CURSOR_GOAL_EVAL_MODEL=fast` (or another
  known-invalid legacy value) now logs a loud warning and falls back to the
  default instead of being passed through. `manage doctor` reports the resolved
  evaluator model and hard-fails on a legacy `fast` override.
- **Sequential dual stop hooks could double-charge the turn budget.** The
  marketplace plugin registers both a `cmd` and a `python3` `stop` hook entry;
  if Cursor invokes both for the same turn, each independently incremented
  `turns_used` and emitted a `followup_message`. A `generation_id`-keyed
  dedupe stamp now makes the second invocation for the same turn replay the
  cached response instead of re-charging the budget.
- **Wake watchdog could delete a live loop's PID file.** `disarm(kill_loop=False)`
  cleared `wake.pid` without verifying ownership, so a manual `wake tick` could
  make a running wake loop appear dead (`pid_dead`) to `continuation_ready`.
  PID-file clearing is now ownership-guarded (matching PID + token).
- **Wake budget could be charged twice per tick.** A manual `wake tick` run
  alongside an owning wake loop both called `_record_wake_tick()`, burning
  `wake_budget` at 2x. Tick charging is now gated on loop ownership.
- **Hung wake loops survived `disarm`.** The Unix kill path sent a single
  `SIGTERM` with no escalation. `wake_process` now waits a bounded interval
  and escalates to `SIGKILL` if the process is still alive.
- **Budget-exhausted message always blamed the turn budget**, even when
  `wake_budget` was the one that ran out. `_budget_limited_response` now
  branches on which budget tripped.
- **BOM-prefixed `goal.json` / `wake.json` / `wake.pid` quarantined the goal.**
  Files saved by a BOM-emitting Windows editor failed to parse under strict
  `utf-8`. These files are now read with `utf-8-sig` (matching the existing
  `hooks_config.py` behavior).
- **`GoalState.from_dict` defaulted `active` to `False`** instead of matching
  the dataclass default of `True` when the key was absent from a legacy
  `goal.json`.
- **`parse.py` clamped the wake budget with `clamp_turn_budget`** instead of
  a wake-budget-specific clamp, and `cmd_pause` skipped the insecure/ACL
  refusal gate that every sibling command applies. Both are fixed.
- **`install-goal.ps1` / `uninstall-goal.ps1` could crash instead of failing
  cleanly.** Both scripts set `$ErrorActionPreference = "Stop"`, and several
  native Python subprocess calls merged stderr with `2>&1` into the success
  pipeline (e.g. `& $Python.Exe ... manage doctor 2>&1 | ForEach-Object
  { Write-Host $_ }`). Under `Stop`, PowerShell turns the first stderr line
  into a terminating exception the instant it reaches the pipeline — before
  `$LASTEXITCODE` can be checked. This broke exactly the failure path it was
  meant to handle: a real `manage doctor` FAIL (which intentionally writes
  to stderr) crashed the installer with an unhandled `RemoteException`
  instead of printing "manage doctor FAILED" and returning exit code 1. All
  `2>&1`-redirecting call sites in both scripts now locally downgrade
  `$ErrorActionPreference` to `Continue` around the native call and restore
  it afterward.
- Installer rollback now also restores (or removes) `goalKeeper.md` /
  `goal-evaluator.md` agent files when the hooks merge step fails, instead
  of only rolling back the skill tree and `hooks.json`.
- Uninstall no longer unconditionally deletes `goalKeeper.md` /
  `goal-evaluator.md` — it first checks for the `cursor-goal:managed-agent`
  provenance marker and leaves hand-edited or foreign files in place.
  Uninstall also now cleans up stale `hooks.json.bak.*` backup files.

### Added

- **`redact_secrets` recognizes more modern secret formats.** Extended
  beyond generic `key=value` / JWT / AWS-key detection to also cover GitHub
  (`ghp_`/`gho_`/`ghu_`/`ghs_`/`ghr_`/`github_pat_`), OpenAI/Anthropic
  (`sk-`/`sk-proj-`/`sk-ant-`), Slack (`xox[baprs]-`), npm (`npm_`), and
  GitLab (`glpat-`) token formats; whole PEM `-----BEGIN … PRIVATE
  KEY-----` blocks; and `scheme://user:password@host` userinfo in
  credentialed URLs / `DATABASE_URL`-style connection strings (Postgres,
  MySQL, MongoDB, Redis, etc.). This is still a heuristic best-effort list,
  not an exhaustive secret scanner.
- **`subagentStop` hook scoped to `goal-evaluator`.** Cursor's documented
  `subagentStop` hook (with `matcher: "goal-evaluator"`) now provides a
  race-free, documented continuation point exactly when the evaluator's
  verdict lands, instead of relying solely on the end-of-turn `stop` hook or
  the undocumented wake watchdog. The handler defensively re-checks
  `subagent_type == "goal-evaluator"` and never auto-calls `manage done` — it
  only nudges the worker to run `eval parse-result`.
- `CURSOR_GOAL_REQUIRE_WAKE=1` opt-in strict mode: restores the old hard
  refusal of `eval validate` / `prompt` / `spawn-config` when wake is dead.
- `CHANGELOG.md`, `CONTRIBUTING.md`, `CODE_OF_CONDUCT.md`, GitHub issue
  templates, and a pull request template — standard OSS project hygiene
  files that were previously missing.
- Documented previously-undocumented CLI surface: `eval parse-result
  --allow-cwd`, `wake arm|loop --interval N`, `manage done --force`, and the
  `CURSOR_GOAL_HOME` environment variable.
- A worked example session (create → wake handshake → validate → evaluate →
  done) in the README.
- CI now runs `pip-audit` / `bandit` on the Windows and macOS legs (not just
  Linux), adds an explicit Windows PowerShell 5.1 leg alongside the existing
  `pwsh` (PowerShell 7) leg, includes the marketplace skill tree's
  `wake_loop.sh` in both local (`scripts/run-shellcheck.sh`,
  `scripts/verify.py`) and CI ShellCheck runs, and runs the plugin-tree-sync
  check on every Python version in the Linux matrix instead of only 3.12.
- The release workflow now runs the full pytest suite (with the 95%
  multi-metric coverage gate) plus `pip-audit`/`bandit` on the Windows and
  macOS gates, not just Linux, and generates a Sigstore-signed [build
  provenance attestation](https://docs.github.com/en/actions/how-tos/secure-your-work/use-artifact-attestations/use-artifact-attestations)
  for the published wheel/sdist alongside `SHA256SUMS.txt`.

### Changed

- **Wake-dead gate downgraded from a hard refusal to a loud warning by
  default.** `refuse_if_wake_dead()` previously made `eval validate` /
  `prompt` / `spawn-config` exit 1 whenever the wake watchdog was not armed —
  the single biggest onboarding blocker, given that Cursor's documented
  `stop` and `subagentStop` hooks are sufficient for continuation in the
  common case. These commands now print a warning and continue (exit 0);
  `manage status` / `doctor` still surface `continuation_ready` so you can
  see whether wake is actually armed. Set `CURSOR_GOAL_REQUIRE_WAKE=1` to
  restore the old strict behavior.
- **Continuation reliability story rebalanced onto documented Cursor
  contracts.** `stop` and `subagentStop` (both documented, with
  `followup_message` / `loop_limit: null`) are now treated as the primary
  continuation mechanism; the wake watchdog (`notify_on_output`, undocumented
  and subject to background-shell reaping) is now described as a best-effort
  supplement, not a requirement. See
  [docs/known-limitations.md](docs/known-limitations.md).
- Documented the `CURSOR_PLUGIN_ROOT` dependency in the marketplace plugin's
  `hooks.json` and its fallback chain to `~/.cursor/skills/goal` when unset.
- Split oversized modules (`wake.py`, `manage.py`, `state.py`, `doctor.py`)
  along their existing seams, extracted the repeated insecure/ACL "refuse"
  preamble and the three duplicate `_now_iso()` implementations into a
  single `state.now_iso()`, and removed test-only `noqa: F401` re-export
  blocks in favor of tests importing directly from the owning module. This
  clears the remaining `pylint` findings (`too-many-branches`,
  `too-many-statements`, `too-many-return-statements`, `invalid-name`,
  `unidiomatic-typecheck`) with no behavior change.

## [3.0.0]

Clean-break production release — tight wake ownership matching (no
pytest/IDE false-positive kills), stop insecure/ACL emit `{}`, resume disarm
on mutate failure, redacted `eval validate` stdout, YES/done gated on
`pursuing`, tokened `wake.pid` only (marker-only hooks), fail-early Windows
ACL before skill copy, install/CI/docs/onboarding hardening.

## [2.16.0]

Production hardening — install ACL soft-failure hard-fails, doctor/eval/wake
budget redact conditions, absolute `CURSOR_GOAL_LOG_FILE` paths, doctor/wake
ACL force re-harden, `run_validation` defaults to `shell_ok=False`, classic
install `.cmd` CGP metachar parity + private installer temps.

## [2.14.0]

Reliability/security hardening — wake tick fail-closed on persist failure,
transactional create/resume arm, doctor marketplace deep scan + VERSION
sync, wake ownership in `continuation_ready`, create requires `--force` for
any existing goal, marketplace `.cmd` uses `%CGP%`, eval/stop refuse
insecure dirs, probe `OSError` fail-closed, scrub drops
`NODE_PATH`/`MAVEN_OPTS`-class vars. Also: host-native path helpers, doctor
data-dir `ValueError`, wake ownership null-subprocess tolerance, macOS
install-smoke non-symlink `HOME`, `wake-smoke.py` in CI, module splits
(`path_trust` / `doctor` / `wake_process`), clearer first-run wake handshake
docs.

[4.0.0]: https://github.com/tboy1337/cursor-goal/releases/tag/v4.0.0
[3.0.0]: https://github.com/tboy1337/cursor-goal/releases/tag/v3.0.0
[2.16.0]: https://github.com/tboy1337/cursor-goal/releases/tag/v2.16.0
[2.14.0]: https://github.com/tboy1337/cursor-goal/releases/tag/v2.14.0
