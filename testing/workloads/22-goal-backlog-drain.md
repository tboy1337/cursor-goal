# Workload 22: Issue Backlog Drain

Test goal pursuit for resolving all TODO comments in a codebase.

## Prompt

```
/goal resolve all TODO comments in testing/scripts/todo_app.py

Implement every TODO: priority validation, error handling, soft delete, sorting, case-insensitive search, and completion time tracking.
```

## Expected Behavior

1. Agent creates goal with natural language condition
2. Agent reads todo_app.py and catalogs all TODO comments
3. Agent implements each TODO one by one:
   - Priority validation (low/medium/high)
   - Error on complete() when todo not found
   - Soft delete instead of hard delete
   - Priority sorting in list_pending()
   - Case-insensitive search
   - Average completion time in get_stats()
4. Agent removes or resolves all TODO comments
5. Subagent evaluates backlog completeness
6. Agent marks goal as achieved

## Features Tested

- F11: Goal state initialization
- F12: Subagent evaluation
- F13b: Goal completion marking
- F15: Natural language condition parsing

## Verification Patterns

- `run_goal.py manage create` with natural language condition
- All TODO comments addressed in todo_app.py
- No remaining `# TODO:` comments in file
- Subagent "Evaluate goal completion" invoked
- `[goal] ✓ Goal achieved` on completion

## Checkpoints Expected

0 during goal

## Special Setup

1. `testing/scripts/todo_app.py` — 8 TODO comments across methods
