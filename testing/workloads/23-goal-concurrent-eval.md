# Workload 23: Multiple Evaluation Cycles

Test goal pursuit with multiple YES/NO evaluation cycles and an inline turn budget.

## Prompt

```
/goal implement all missing methods in testing/scripts/data_structures.py until the test suite passes, stop after 8 turns

Implement Stack, Queue, and LinkedList methods to pass test_data_structures.py.
```

## Expected Behavior

1. Agent creates goal with natural language condition and "stop after 8 turns"
2. Agent reads data_structures.py and test_data_structures.py
3. Agent implements methods incrementally (Stack first, then Queue, then LinkedList)
4. Agent runs tests after each batch — partial passes, some failures remain
5. Subagent evaluates — returns NO until all tests pass
6. Agent continues implementing until tests pass
7. Subagent returns YES — agent marks goal achieved
8. Multiple evaluation cycles visible in transcript

## Features Tested

- F11: Goal state initialization
- F12: Subagent evaluation (multiple NO then YES)
- F13: Stop hook auto-continuation
- F16: Multi-cycle evaluation
- F17: Budget inline parsing ("stop after 8 turns")

## Verification Patterns

- `goal-manage.sh create` with natural language condition
- Multiple `NO:` responses before final `YES:`
- `pytest testing/scripts/test_data_structures.py` executed repeatedly
- `[GOAL] Turn N/8` in followup messages
- Subagent "Evaluate goal completion" invoked 2+ times
- `[goal] ✓ Goal achieved` on completion

## Checkpoints Expected

0 during goal

## Special Setup

1. `testing/scripts/data_structures.py` — all methods raise NotImplementedError
2. `testing/scripts/test_data_structures.py` — comprehensive test suite
