# Troubleshooting

Quick fixes for common install and continuation failures. cursor-goal is the `/cursor-goal` harness (Codex-style loop). After install, `/cursor-goal` and `/goal` both layer native continuation under maker ≠ checker. Pinning the skill as a Custom Mode is optional (keeps the protocol sticky for the whole chat). Without this skill installed, vanilla `/goal` is still same-model self-audit.

Also run:

```bash
# Unix / macOS / WSL
python3 -u ~/.cursor/skills/cursor-goal/scripts/run_goal.py manage doctor
```

```powershell
# Windows
py -3 -u "$env:USERPROFILE\.cursor\skills\cursor-goal\scripts\run_goal.py" manage doctor
```

Stall checklist (Hooks `{}`, no continuation): work the steps below before filing a GitHub issue.

## Hooks Execution Log shows `{}` but the goal should continue

1. Check `~/.cursor-goal/data/last-stop-response.json`. If it contains `followup_message` while Hooks shows `{}`, you hit the [Cursor stdout race](cursor-windows-stop-hook-race.md).
2. Ensure wake is armed and the loop is alive (`manage status` / `manage doctor`) **unless** `native_continuation` is true (then worker `stop` emitting `{}` is expected; native runtime continues). Doctor **FAIL**s while `pursuing` if wake is enabled, native continuation is off, and `continuation_ready=false` (missing or dead loop). Doctor skips that gate when `CURSOR_GOAL_WAKE=0` or native continuation is on.
3. Prefer the `command` from create/resume's `GOAL_WAKE_REQUIRED` line (or `wake status` JSON). Start background Shell with `notify_on_output` matching `^AGENT_GOAL_WAKE FOLLOWUP_REQUIRED pursuing spawn_goal-auditor`:

```text
# Unix
python3 -u ~/.cursor/skills/cursor-goal/scripts/run_goal.py wake loop
```

```powershell
# Windows classic — prefer baked wake_loop.cmd when present
py -3 -u "$env:USERPROFILE\.cursor\skills\cursor-goal\scripts\run_goal.py" wake loop
```

4. Confirm `wake status` shows `continuation_ready=true` (and `pid_alive=true`).

## Wake not armed / `continuation_ready=false`

Doctor **hard-fails** when a goal is `pursuing`, wake is enabled, **native continuation is off**, and the loop is missing or dead. `manage status` prints an **ACTION REQUIRED** recovery command, `Continuation ready: false (…)`, and exits **1**. Immediately start `wake loop` as above. If create/resume exited 1 with the goal paused, fix data-dir/ACL issues then `manage resume`. Disable wake only with `CURSOR_GOAL_WAKE=0` (not recommended while the stop race remains). Skip wake entirely with `manage create --native` after CreateGoal, or `CURSOR_GOAL_NATIVE=0` to force hooks+wake.

Create with wake enabled prints `Status: paused (awaiting wake arm)` then `Status: pursuing` only after a successful arm/activate — do not treat the early paused line as a failure. For durable diagnostics set `CURSOR_GOAL_LOG_FILE=1` (create and doctor FAIL also print this tip).

## Classic + marketplace hooks stacked

Doctor **FAIL**s when both classic `~/.cursor/hooks.json` stop entries and Teams marketplace plugin hooks look configured. Marketplace detection walks `~/.cursor/plugins/cache/**` and `~/.cursor/plugins/local/**` (bounded) for `hooks/hooks.json` goal stop markers, plus `CURSOR_PLUGIN_ROOT`. Pick **one** install path: uninstall classic hooks (`uninstall-goal.*` without necessarily purging data) **or** disable the marketplace plugin — do not run both.

## Classic install VERSION drift

Doctor **FAIL**s when `~/.cursor/skills/cursor-goal/VERSION` (or the resolved skill root `VERSION`) is missing on a classic install path or does not match the running package version. Re-run `install-goal.sh` / `install-goal.ps1` (or sync/re-enable the marketplace plugin) so the installed skill tree matches the package.

## Extra `goal` / `goal.bak.*` in Customize → Skills

Cursor [loads every `SKILL.md` under `~/.cursor/skills/`](https://cursor.com/docs/skills). Pre-v5 installers left `goal.bak.<UTC>` siblings that show up as extra skills. Re-run the v5 installer: it migrates those folders to `~/.cursor-goal/backups/` (keep 1) and **deletes** leftover `~/.cursor/skills/goal`. You should see **one** user skill named `cursor-goal`. Cursor's built-in `/goal` under `~/.cursor/skills-cursor/goal` is expected and is not this project.

Seeing both `/goal` and `/cursor-goal` in the product is expected. After install this skill auto-applies for `/goal` as well, so both get maker ≠ checker. Pinning cursor-goal as a Custom Mode is optional if you want the skill sticky for the whole chat. Without this skill installed, vanilla `/goal` is native continuation with same-model self-audit.

Doctor **FAIL**s while `~/.cursor/skills/goal/SKILL.md` still exists (stacked old user skill). Doctor **WARN**s when the built-in `~/.cursor/skills-cursor/goal` is present (layer it; do not overwrite it).

## `manage doctor` FAIL: insecure data directory

**Unix:** data dir must not be a symlink, must be owned by you, and must not be group/world-writable (`chmod 700`).

**Windows:** data dir must not be a symlink/junction/reparse point. ACL harden failures also FAIL create/validate/stop/wake and doctor — verify only you can access the path, or set `CURSOR_GOAL_SKIP_ACL=1` after manual lockdown.

Override with `CURSOR_GOAL_DATA` to a private directory.

## Marketplace stop/wake fails to find Python (Windows)

**Recommended fix path (pick one):**

1. **Individuals:** prefer classic `install-goal.ps1`, which bakes an absolute interpreter into `stop_hook.cmd` / `wake_loop.cmd` and (when `CURSOR_GOAL_PYTHON` is set) requires an absolute 3.12+ path — same gates as the marketplace launcher.
2. **Teams marketplace:** set an **absolute** `CURSOR_GOAL_PYTHON` (user or system env), then restart Cursor:

```powershell
$env:CURSOR_GOAL_PYTHON = 'C:\Path\To\python.exe'
# Persist for your user, then restart Cursor:
[Environment]::SetEnvironmentVariable('CURSOR_GOAL_PYTHON', 'C:\Path\To\python.exe', 'User')
```

Relative or bare `python` values are rejected. Values with cmd metacharacters (`" & | ^ < >`) are also rejected. Doctor **FAIL**s on Windows when marketplace hooks are detected and `CURSOR_GOAL_PYTHON` is unset or non-absolute — PATH-only resolution is fragile and not treated as success for marketplace installs. Marketplace/template `.cmd` launchers execute the quote-stripped `%CGP%` value (same as classic bake), not the raw env var.

## Wrong installer / WSL mix-up

- Native Windows Cursor → `scripts/install-goal.ps1` (not Git Bash `scripts/install-goal.sh`).
- WSL Cursor → Unix installer inside WSL `$HOME`, not `/mnt/c/...` mixed with native Windows hooks.

## Git Bash refused on Windows

`scripts/install-goal.sh` refuses when it detects native Windows Cursor paths — use PowerShell `scripts/install-goal.ps1` so `stop_hook.cmd` is correct.

## Shell-mode validation warning

Doctor warns when validation uses shell mode. New goals default to `shell_ok=false`; create **refuses** shell-metachar `--test` without `--allow-shell`. Prefer argv-safe `--test` or keep deny-shell. Use `--allow-shell` only when compound shell is required.

## Task / evaluator model errors

`CURSOR_GOAL_EVAL_MODEL` must be `inherit` or a real Cursor model ID (e.g. `composer-2.5`, `gpt-5.3-codex`) — see the [subagents model reference](https://cursor.com/docs/subagents.md#model-configuration). `fast` is **not** a model ID (it is only a bracket option on a real model, e.g. `composer-2.5[fast=false]`); setting it as the env var value is a known-invalid legacy setting that now logs a warning and silently falls back to the default (`composer-2.5`) rather than being honored:

```bash
export CURSOR_GOAL_EVAL_MODEL=composer-2.5
```

On legacy request-based Cursor plans without Max Mode, Task subagents may still run on a Cursor-selected model regardless of the requested `model` — `eval spawn-config`'s output reflects the *requested* model, not a runtime guarantee. `manage doctor` hard-fails when `CURSOR_GOAL_EVAL_MODEL` is set to a known-invalid legacy value.

## Corrupt `goal.json`

Corrupt files are renamed to `goal.json.corrupt.<UTC>`. Remove or fix, then `manage create` again. See doctor output for lock timeouts.

## Recovery flags and advanced overrides

These are intentionally undiscoverable from the normal `/cursor-goal` flow — agents
should not need them for a healthy goal. They exist for humans recovering
from a stuck or corrupted state.

- **`manage done --force`** — marks the goal achieved **without** a YES-bound
  evaluator signal. This is a protocol bypass (not cryptographic
  attestation): it logs a warning and prints one to stderr every time it is
  used. Use it only when you, a human, have manually confirmed the condition
  is met and the evaluator signal is unavailable (e.g. you cleared it, or
  the subagent never ran). Agents should prefer `eval parse-result` /
  `eval signal --force` (recovery-only, requires a prior YES parse) over
  `manage done --force`.
- **`eval parse-result ... --allow-cwd`** — when reading a verdict from
  `@path/to/file`, the path must normally resolve under the goal data
  directory. `--allow-cwd` also permits paths under the current working
  directory, for setups where the evaluator's raw output is captured
  outside `~/.cursor-goal/data` (e.g. a custom CI harness). Without it,
  `@file` outside the data dir is refused with
  `@file path must be under the goal data directory`.
- **`wake arm --interval N` / `wake loop --interval N`** — override the wake
  tick interval (seconds) for that invocation only, instead of setting
  `CURSOR_GOAL_WAKE_INTERVAL_S` process-wide. Clamped to `[5, 600]`; an
  explicit `--interval` always wins over the env var.
- **`CURSOR_GOAL_HOME`** — override the resolved skill root (where
  `run_goal.py` is expected), ahead of both the package-parent layout check
  and `CURSOR_PLUGIN_ROOT`. Must be an absolute path. Use `manage
  harness-cmd` to see which path actually won.

## Still stuck?

1. `manage status` and `manage doctor`
2. [known-limitations.md](known-limitations.md)
3. [platform-compatibility.md](platform-compatibility.md)
4. Upstream Cursor hook issues: report on the Cursor forum (out of scope for this repo’s security advisories)
