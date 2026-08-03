# Security Policy

## Supported versions

Security fixes are applied on the latest tagged release of [cursor-goal](https://github.com/tboy1337/cursor-goal) (`main` / current semver).

## Threat model

cursor-goal is a **single-user, trusted-local** Cursor IDE harness.

| Asset | Trust assumption |
|-------|------------------|
| `~/.cursor-goal/data/` (`goal.json`, eval signal, wake files) | Equivalent to shell trust for the interactive user |
| `validation_command` | May be executed by `eval validate` / agent Shell |
| Stop hook | Fail-open; never blocks Cursor on corrupt state; singleflight across dual marketplace entries |
| Eval YES signal | Protocol guard bound to goal content hash — **not** cryptographic attestation |

If another user or process can write your goal data directory, they can cause commands to run as you. Protect the directory (`0700` on Unix; Windows best-effort `icacls /inheritance:r` then grant current user full control). If inheritance strip succeeds but the grant fails, cursor-goal logs a loud error — verify you can still access the data directory.

## Hardening controls

- Prefer argv-safe `--test` commands. New goals default to `shell_ok=false`; pass `--allow-shell` to permit `shell=True` (`COMSPEC`/cmd on Windows) when argv-splitting fails. Library `run_validation` also defaults to `shell_ok=False` (fail closed). `--deny-shell` and `CURSOR_GOAL_DENY_SHELL=1` remain available.
- Create/validate/stop/wake **and** `eval parse-result` / `eval signal` refuse insecure data directories: on Unix, symlink / wrong owner / group/world-writable (stop fails open to `{}`; wake arm/loop/tick refuse; eval writers refuse). On Windows, symlink / junction / reparse point (same refuse behavior). Symlink/reparse checks inspect the **unresolved** configured path (including ancestors) so `Path.resolve()` cannot bypass the trust boundary. Probe `OSError` fails closed (treat as insecure). Stop singleflight acquires only after the same insecure/ACL refuse gates.
- On Windows, create/validate/**stop/wake**/eval writers refuse when ACL harden was attempted and failed (doctor also hard-fails). Inheritance strip failure is a hard fail (path is not marked hardened). If inheritance was stripped and the grant fails, inheritance is restored (`icacls /inheritance:e`) best-effort before recording the failure. `CURSOR_GOAL_SKIP_ACL=1` is **test/emergency-only** after manually locking down the path.
- Exclusive `goal.lock` times out after ~10s (Unix and Windows). Fail-open stop continues are capped and counted against the turn budget.
- Corrupt `goal.json` is quarantined to `goal.json.corrupt.<UTC>`.
- Field length limits: updates reject oversized condition/validation; load clamps oversized strings (with warning) so stop fail-open is not tripped by length alone (`MAX_FIELD_CHARS`).
- Turn budget and wake budget are independent counters (schema v1; recreate goals after upgrades that change the schema).
- `manage done --force` and `eval signal --force` are recovery escapes and are logged; they are not attestation.
- Secret-ish tokens in validation commands **and validation output** / status reasons **and goal conditions** are redacted (heuristic; incomplete by design) on these surfaces: `manage status` / create conflict messages / logs, `manage doctor` goal summary, `eval prompt`, stop live `followup_message` and wake tick/loop/budget prompts, and persisted validation output. Live stop/wake prompts keep a usable condition after scrubbing; `last-stop-response.json` strips condition payloads after `Goal:` / `toward:` markers for disk. Validation subprocesses receive a scrubbed environment (PATH/home/locale/`VIRTUAL_ENV`/Windows `APPDATA`/`LOCALAPPDATA`/common toolchain homes/`CURSOR_GOAL_*` — not ambient API keys, not privilege toggles `CURSOR_GOAL_SKIP_ACL` / `ALLOW_ANY_WORKDIR` / `ALLOW_DEAD_WAKE`, and not `PYTHONPATH`/`PYTHONHOME`/`NODE_PATH`/`NPM_CONFIG_USERCONFIG`/`MAVEN_OPTS`/`SBT_OPTS`). On Windows, shell mode pins `COMSPEC` to `%SystemRoot%\System32\cmd.exe` when that file exists. `CURSOR_GOAL_LOG_SECRETS=1` may log full commands at DEBUG.
- `last-stop-response.json` stores a redacted payload with private file mode when the OS allows. Optional durable logs: `CURSOR_GOAL_LOG_FILE=1` (sentinel → data-dir log) or an **absolute** custom path (relative paths are rejected, same policy as `CURSOR_GOAL_DATA`); when set without `CURSOR_GOAL_LOG`, log level defaults to INFO.
- Wake kill verifies process cmdline (Windows CIM / Unix `/proc` or `ps`) before signaling. Tokened `wake.pid` requires a matching ownership token. Legacy tokenless `wake.pid`: kill only when the ownership probe confirms a wake/goal process; otherwise clear the pid file, write `wake.orphan`, and fail `manage doctor` until resolved.
- When `CURSOR_GOAL_PYTHON` is set (marketplace/classic Windows launchers), it must be an absolute path to Python 3.12+. Classic and marketplace `.cmd` launchers reject unsafe cmd metacharacters (`&|<>^`) in `CURSOR_GOAL_PYTHON` (note: `echo %CGP%| findstr` can still expand nested `%VAR%` sequences — prefer a clean absolute path without percent signs).
- When `CURSOR_GOAL_DATA` is set, it must be an absolute path (same policy as `CURSOR_GOAL_HOME`).
- `--workdir` must not be a symlink/junction/reparse and, unless `CURSOR_GOAL_ALLOW_ANY_WORKDIR=1`, must stay under the create-time process cwd. `eval validate` fails closed if a configured workdir is missing or insecure.
- `manage doctor` reports insecure dirs, Windows ACL harden failures (force re-harden each doctor run), orphan wake markers, stale baked Python in `.cmd` launchers, unsafe `CURSOR_GOAL_PYTHON` metacharacters, installed skill `VERSION` vs package version, PATH vs absolute Python, hook presence, classic+marketplace stacking (hard fail; scans `~/.cursor/plugins/cache/**` and `local/**`), wake health including ownership verification (with exact wake-loop command; hard fail while pursuing without live wake), fail-open continue counter, and shell mode. Installers exit non-zero when doctor hard-fails. Classic Windows install hard-fails when data-dir ACL harden soft-fails.

## Reporting a vulnerability

Please report via [GitHub Security Advisories for tboy1337/cursor-goal](https://github.com/tboy1337/cursor-goal/security/advisories/new).

Include:

1. Affected version / commit
2. Reproduction steps
3. Impact (e.g. unexpected command execution, data disclosure)

Do not file public issues for unfixed vulnerabilities.

## Out of scope

- Multi-tenant / shared-host isolation
- Cursor IDE / Task / stop-hook platform bugs (report upstream when possible)
- Compromised Cursor session model or agent that already has Shell access
