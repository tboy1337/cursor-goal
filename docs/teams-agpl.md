# Teams marketplace and AGPL

cursor-goal is licensed **AGPL-3.0-only** ([COPYING](../COPYING)).

## What marketplace import means

Importing this repository in Cursor Dashboard → Plugins redistributes the plugin tree (skills, agents, hooks, vendored Python package) to users who enable the plugin. That is a form of conveyance under AGPL.

If you modify the plugin and provide it to others over a network (including internal Teams marketplace hosting of a modified fork), AGPL generally requires offering corresponding source under AGPL terms. Have counsel review [COPYING](../COPYING) for your deployment.

## When counsel should review

- Enterprise/Teams redistribution or internal forks
- Combining cursor-goal with proprietary plugins in a way that may create a combined work
- Any plan to change the license or dual-license

## When classic personal install is safer

For a single private individual on one machine, the classic installer (`scripts/install-goal.sh` / `install-goal.ps1`) copies the skill into `~/.cursor` without a Teams marketplace redistribute step. That path is usually simpler for individuals and prefers a baked absolute Python on Windows.

Do **not** stack classic hooks with marketplace hooks — pick one install path.

## Dual stop hooks (expected UI noise)

Marketplace `hooks.json` registers both a Windows `.cmd` stop hook and a Unix `python3` stop hook. On any given OS, one entry typically fails (missing `cmd` on Unix, missing `python3` on many Windows PATH setups). A singleflight lock ensures only one hook mutates state; the Hooks UI may still show a failure on the non-native entry — that is expected, not a broken install, when the other entry succeeds and/or wake is alive.

Set `CURSOR_GOAL_PYTHON` to an **absolute** 3.12+ interpreter on Windows Teams installs.
