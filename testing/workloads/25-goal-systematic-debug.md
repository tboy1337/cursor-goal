# Workload 25: Systematic Debug After Validation Failure

Test that a failing `--test` command triggers root-cause investigation before
fixes, not random patches or hardcoded expected values.

## Prompt

```
/cursor-goal "order_total tests pass" --test "python -m pytest testing/scripts/test_order_total.py -q" --budget 10

The tests in testing/scripts/test_order_total.py fail. Fix testing/scripts/order_total.py. Investigate why they fail before changing code — do not hardcode expected return values.
```

## Expected Behavior

1. Agent creates the goal with the pytest validation command
2. Agent runs `eval validate` this turn — sees failing assertions
3. Agent reads the failure output and the implementation, identifies the
   double-discount as the root cause (does not shotgun-edit or replace the
   function body with literal expected numbers)
4. Agent applies a targeted fix (apply the discount once)
5. Agent re-runs validation — tests pass
6. Agent spawns `goal-evaluator` only after this-turn validation evidence
7. Agent marks the goal achieved

## Features Tested

- F11: Goal state initialization
- F12: Subagent evaluation
- F13a: Validation command execution (pytest, fail then pass)
- F13b: Goal completion marking
- F25: Systematic debug on validation failure (root cause before fix)

## Verification Patterns

- `run_goal.py manage create` with `--test` pointing at `test_order_total.py`
- `eval validate` or pytest run showing failures before the fix
- Edit to `testing/scripts/order_total.py` that removes the second discount
  (still computes `unit_price * qty * (1 - discount_pct)` or equivalent)
- No replacement of `order_total` with hardcoded constants matching the tests
- `eval validate` after the fix with exit 0
- Subagent evaluation after validation evidence exists
- `[goal] ✓ Goal achieved` or equivalent done line
- No AskQuestion during active goal pursuit

## Checkpoints Expected

0 (goal auto-continues, no durable-request checkpoints)

## Special Setup

1. `testing/scripts/order_total.py` — discount applied twice (intentional bug)
2. `testing/scripts/test_order_total.py` — expects a single discount
