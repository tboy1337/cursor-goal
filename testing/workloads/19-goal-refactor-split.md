# Workload 19: File Splitting Refactor

Test goal pursuit for splitting an oversized monolithic module into focused files.

## Prompt

```
/cursor-goal split testing/scripts/monolith.py into focused modules until each is under 50 lines

Break the monolith into separate modules by responsibility (users, email, logging, config, validation).
```

## Expected Behavior

1. Agent creates goal with natural language condition
2. Agent reads monolith.py and identifies distinct responsibilities
3. Agent creates separate module files (e.g., user_manager.py, email_sender.py, etc.)
4. Agent moves classes and related utilities to appropriate modules
5. Agent ensures each resulting file is under 50 lines
6. Subagent evaluates split completeness
7. Agent marks goal as achieved

## Features Tested

- F11: Goal state initialization
- F12: Subagent evaluation
- F13b: Goal completion marking
- F15: Natural language condition parsing

## Verification Patterns

- `run_goal.py manage create` with natural language condition
- Multiple new .py files created from monolith
- Each new file under 50 lines
- Subagent "Evaluate goal completion" invoked
- `[goal] ✓ Goal achieved` on completion

## Checkpoints Expected

0 during goal

## Special Setup

1. `testing/scripts/monolith.py` — 150+ line monolithic module with 5 classes and utilities
