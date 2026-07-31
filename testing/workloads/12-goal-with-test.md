# Workload 12: Goal with Validation Command

Test the full /goal lifecycle with a validation command that transitions from failing to passing.

## Prompt

```
/goal "fibonacci tests pass" --test "python -m pytest testing/scripts/test_fibonacci.py -q" --budget 10

The test file testing/scripts/test_fibonacci.py has failing tests. Fix the implementation in testing/scripts/fibonacci.py until all tests pass.
```

## Expected Behavior

1. Agent parses /goal command and creates goal.json via run_goal.py manage
2. Agent reads the test file and implementation file
3. Agent runs the test command — sees failures
4. Agent fixes the implementation
5. Agent runs tests again — sees success
6. Agent spawns a readonly subagent to evaluate the goal
7. Subagent returns "YES: all tests passing"
8. Agent marks goal as achieved via run_goal.py manage done
9. Agent reports achievement and stops

## Features Tested

- F11: run_goal.py manage create → goal.json written with status "pursuing"
- F12: Subagent evaluation (expect NO then YES across iterations)
- F13: Stop hook auto-continuation if agent ends turn before achieving
- F13a: Validation command execution (--test "python -m pytest ...")
- F13b: Goal completion marking (run_goal.py manage done)

## Verification Patterns

- `[goal] Goal created` in output
- `run_goal.py manage create` called
- Subagent Task invoked with "Evaluate goal completion"
- `YES:` or `NO:` in subagent response
- `[goal] ✓ Goal achieved` in output
- No AskQuestion during active goal pursuit

## Checkpoints Expected

0 (goal auto-continues, no durable-request checkpoints)

## Special Setup

1. Pre-seed `testing/scripts/fibonacci.py` with a broken implementation
2. `testing/scripts/test_fibonacci.py` has tests that exercise the function
