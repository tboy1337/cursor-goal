# Workload 15: Goal Pause/Resume Lifecycle

Test that the agent can pause an active goal mid-pursuit and resume it later without losing state.

## Prompt

```
/cursor-goal migrate all console.log calls to a proper logger, stop after 10 turns

The file testing/scripts/app_logger.js uses console.log throughout. Replace with a structured logger module.
```

Midway through execution (after ~3-5 turns), send:

```
/cursor-goal pause
```

Then after a brief pause:

```
/cursor-goal resume
```

## Expected Behavior

1. Agent creates goal with natural language condition and inline turn budget
2. Agent begins migrating console.log calls in app_logger.js
3. User sends `/cursor-goal pause` — agent stops pursuit and saves state
4. User sends `/cursor-goal resume` — agent continues from where it left off
5. Agent completes migration and evaluates goal
6. Subagent confirms logger migration is complete
7. Agent marks goal as achieved

## Features Tested

- F11: Goal state initialization
- F14: Goal pause/resume lifecycle
- F15: Natural language condition parsing (no --test flag)
- F17: Budget inline parsing ("stop after 10 turns")

## Verification Patterns

- `run_goal.py manage create` called with natural language condition
- `/cursor-goal pause` or `run_goal.py manage pause` in transcript
- `/cursor-goal resume` or `run_goal.py manage resume` in transcript
- `"status": "paused"` then back to `"pursuing"` in goal.json
- Subagent "Evaluate goal completion" invoked after resume
- `[goal] ✓ Goal achieved` on completion

## Checkpoints Expected

0 during active goal pursuit (including after resume)

## Special Setup

1. Pre-seed `testing/scripts/app_logger.js` with console.log calls
2. No pre-built logger module — agent should create one
