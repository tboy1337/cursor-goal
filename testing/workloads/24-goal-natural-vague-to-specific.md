# Workload 24: Vague Goal to Specific Interpretation

Test that the agent handles ambiguous natural language goals by asking for specifics or making reasonable interpretations.

## Prompt

```
/cursor-goal make the calculator robust

The calculator in testing/scripts/calculator.py needs hardening.
```

## Expected Behavior

1. Agent creates goal with vague natural language condition
2. Agent either:
   - Asks clarifying questions about what "robust" means, OR
   - Interprets "robust" reasonably (input validation, edge cases, error handling)
3. Agent reads calculator.py and identifies gaps
4. Agent implements improvements (type checking, edge cases, better error messages)
5. Agent adds or updates tests for edge cases
6. Subagent evaluates whether calculator is now robust
7. Agent marks goal as achieved

## Features Tested

- F11: Goal state initialization
- F12: Subagent evaluation
- F15: Natural language condition parsing (vague/ambiguous condition)

## Verification Patterns

- `run_goal.py manage create` with vague natural language condition
- Agent interprets or clarifies "robust" before acting
- Improvements to calculator.py (validation, error handling, etc.)
- Subagent "Evaluate goal completion" invoked
- `[goal] ✓ Goal achieved` or reasonable partial completion with explanation

## Checkpoints Expected

0-1 (may ask one clarifying question before pursuing)

## Special Setup

1. `testing/scripts/calculator.py` — basic implementation without input validation
2. `testing/scripts/test_calculator.py` — minimal tests (partial coverage)
