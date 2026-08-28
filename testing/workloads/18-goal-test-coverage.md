# Workload 18: Add Test Coverage

Test goal pursuit targeting complete test coverage for a module with partial tests.

## Prompt

```
/cursor-goal every exported function in testing/scripts/calculator.py has at least one test, verified by pytest testing/scripts/test_calculator.py

Add missing tests for multiply, divide, power, and modulo in test_calculator.py.
```

## Expected Behavior

1. Agent creates goal with natural language condition and pytest verification
2. Agent reads calculator.py and test_calculator.py
3. Agent identifies untested functions (multiply, divide, power, modulo)
4. Agent writes tests for each missing function
5. Agent runs pytest — all tests pass
6. Subagent confirms full coverage
7. Agent marks goal as achieved

## Features Tested

- F11: Goal state initialization
- F12: Subagent evaluation
- F13a: Validation command execution (pytest)
- F13b: Goal completion marking
- F15: Natural language condition parsing

## Verification Patterns

- `run_goal.py manage create` with natural language condition
- `pytest testing/scripts/test_calculator.py` executed
- Tests added for multiply, divide, power, modulo
- Subagent "Evaluate goal completion" invoked
- `[goal] ✓ Goal achieved` on completion

## Checkpoints Expected

0 during goal

## Special Setup

1. `testing/scripts/calculator.py` — 6 functions, fully implemented
2. `testing/scripts/test_calculator.py` — only tests add and subtract
