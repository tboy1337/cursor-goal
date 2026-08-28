# Workload 13: Goal Budget Exhaustion

Test that the turn budget mechanism works correctly — goal should stop when budget is hit.

## Prompt

```
/cursor-goal "implement a full REST API with 10 endpoints" --budget 3

Build a complete REST API.
```

## Expected Behavior

1. Agent creates goal with budget 3
2. Agent works on turn 1 — stop hook increments to 1/3
3. Agent works on turn 2 — stop hook increments to 2/3
4. Agent works on turn 3 — budget hit, stop hook sets status to "budget-limited"
5. Agent receives [GOAL BUDGET] message, wraps up and summarizes

## Features Tested

- F11: Goal state initialization
- F13: Stop hook auto-continuation (turns 1 and 2) + budget limit (turn 3)

## Verification Patterns

- `[GOAL] Turn 1/3` in followup
- `[GOAL] Turn 2/3` in followup
- `[GOAL BUDGET] Turn limit (3) reached` in final followup
- `"status": "budget-limited"` in goal.json

## Checkpoints Expected

0 during goal, 1 after budget-limited (if durable-request installed)
