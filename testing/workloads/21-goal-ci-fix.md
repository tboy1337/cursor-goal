# Workload 21: Fix CI (Multi-Step)

Test goal pursuit for fixing a failing test suite across multiple modules.

## Prompt

```
/goal fix the failing tests in testing/scripts/, the test suite should pass end-to-end

Multiple test files are failing. Diagnose and fix all failures.
```

## Expected Behavior

1. Agent creates goal with natural language condition
2. Agent runs pytest on testing/scripts/ — sees multiple failures
3. Agent diagnoses failures:
   - test_broken.py has wrong assertion
   - test_data_structures.py fails due to NotImplementedError stubs
4. Agent fixes broken test assertion and/or implements missing data structure methods
5. Agent re-runs full test suite — all pass
6. Subagent confirms test suite health
7. Agent marks goal as achieved

## Features Tested

- F11: Goal state initialization
- F12: Subagent evaluation (expect NO then YES across iterations)
- F13: Stop hook auto-continuation if agent ends turn before achieving
- F13a: Validation command execution (pytest)
- F13b: Goal completion marking

## Verification Patterns

- `goal-manage.sh create` with natural language condition
- `pytest testing/scripts/` executed multiple times
- Failures diagnosed and fixed
- Subagent "Evaluate goal completion" invoked
- `YES:` in subagent response after fixes
- `[goal] ✓ Goal achieved` on completion

## Checkpoints Expected

0 during goal (auto-continues until tests pass)

## Special Setup

1. `testing/scripts/test_broken.py` — intentionally failing assertion
2. `testing/scripts/data_structures.py` — stub methods raising NotImplementedError
3. `testing/scripts/test_data_structures.py` — tests that fail against stubs
4. `testing/scripts/test_calculator.py` and `test_fibonacci.py` — passing tests for contrast
