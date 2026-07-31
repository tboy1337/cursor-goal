# Workload 14: Goal Without Validation Command

Test goal evaluation using only subagent judgment (no --test command).

## Prompt

```
/goal "add comprehensive JSDoc comments to all exported functions in src/utils.ts"

Add JSDoc comments to every exported function in the file.
```

## Expected Behavior

1. Agent creates goal without validation command
2. Agent reads src/utils.ts and identifies exported functions
3. Agent adds JSDoc comments to each function
4. Agent spawns evaluator subagent — subagent evaluates from conversation context
5. If NO — agent continues adding/improving comments
6. If YES — agent marks goal done

## Features Tested

- F11: Goal state initialization (no validation_command)
- F12: Subagent evaluation based on conversation context only
- F13b: Goal completion marking

## Verification Patterns

- `goal-manage.sh create` without `--test`
- Subagent "Evaluate goal completion" invoked
- `YES:` or `NO:` evaluation response
- `[goal] ✓ Goal achieved` on completion

## Checkpoints Expected

0 during goal
