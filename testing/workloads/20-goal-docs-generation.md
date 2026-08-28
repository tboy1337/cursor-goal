# Workload 20: Documentation Generation

Test goal pursuit for adding docstrings to an undocumented API client module.

## Prompt

```
/cursor-goal every public class and function in testing/scripts/api_client.py has docstrings

Add Google-style docstrings to APIClient and all module-level functions.
```

## Expected Behavior

1. Agent creates goal with natural language condition
2. Agent reads api_client.py and identifies public classes/functions without docstrings
3. Agent adds docstrings to APIClient, build_url, and parse_response
4. Agent includes parameter descriptions and return types
5. Subagent evaluates documentation completeness
6. Agent marks goal as achieved

## Features Tested

- F11: Goal state initialization
- F12: Subagent evaluation
- F13b: Goal completion marking
- F15: Natural language condition parsing

## Verification Patterns

- `run_goal.py manage create` with natural language condition
- Docstrings added to APIClient class and all public methods
- Docstrings added to build_url and parse_response
- Subagent "Evaluate goal completion" invoked
- `[goal] ✓ Goal achieved` on completion

## Checkpoints Expected

0 during goal

## Special Setup

1. `testing/scripts/api_client.py` — APIClient class and helpers with no docstrings
