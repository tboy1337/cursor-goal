# Subagent-Based Testing for /goal

Automated testing of cursor-goal features via Task subagents.

## What's Tested

Features that can be triggered in a single-turn subagent execution:

| Feature | Name | Mechanism |
|---------|------|-----------|
| F11 | Goal state initialization | `run_goal.py manage create` |
| F12 | Subagent goal evaluation | `Task` call with evaluator prompt |
| F13a | Validation command execution | Shell pytest/eslint |
| F13b | Goal completion marking | `run_goal.py manage done` |
| F15 | Natural language condition parsing | `run_goal.py parse` |
| F17 | Budget inline parsing | `run_goal.py parse` |
| F18 | Goal clear/cancel | `run_goal.py manage clear` |
| F19 | Evaluator uses `subagent_type: goal-evaluator` | `Task` call inspection |
| F20 | Evaluator runs as readonly | `Task` call inspection |
| F21 | Evaluator prompt contains condition | `Task` prompt inspection |
| F22 | No self-assessment (done after eval) | Action ordering |
| F23 | Goal creation non-empty condition | Create command inspection |
| F24 | Done follows evaluator directly | Action ordering |

## What's NOT Tested

Features requiring Cursor IDE infrastructure (stop hooks, multi-turn interaction):

- F13: Stop hook auto-continuation
- F14: Pause/resume lifecycle (requires mid-execution user input)
- F16: Multi-cycle evaluation (depends on F13 for turn chaining)

## Running

```bash
cd testing/subagent-tests
python3 run-subagent-tests.py
```

Results are saved to `results/<timestamp>/`.

Prefer the installed Python harness (legacy `goal-*.sh` scripts are removed):

```bash
python3 -u ~/.cursor/skills/goal/scripts/run_goal.py manage status
python3 -u ~/.cursor/skills/goal/scripts/run_goal.py eval spawn-config
```

Workloads in `workloads.py` invoke `run_goal.py` with `eval spawn-config` so evaluator Task calls use `goal-evaluator` + the configured model (default `composer-2.5`; override with `CURSOR_GOAL_EVAL_MODEL`).
