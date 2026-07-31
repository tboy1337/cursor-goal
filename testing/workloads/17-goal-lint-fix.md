# Workload 17: Lint Cleanup

Test goal pursuit with a validation-style natural language condition referencing a lint command.

## Prompt

```
/goal no ESLint errors in src/, verified by eslint src/ --quiet

Fix all lint violations in testing/src/utils.ts.
```

## Expected Behavior

1. Agent creates goal with natural language condition including verification command
2. Agent runs `eslint src/ --quiet` — sees errors (no-console, no-var, no-unused-vars)
3. Agent fixes each violation in utils.ts
4. Agent re-runs eslint — sees clean output
5. Subagent evaluates lint status
6. Agent marks goal as achieved

## Features Tested

- F11: Goal state initialization
- F12: Subagent evaluation
- F13b: Goal completion marking
- F15: Natural language condition parsing

## Verification Patterns

- `goal-manage.sh create` with natural language condition
- `eslint src/` or `eslint testing/src/` executed in transcript
- Lint errors identified and fixed
- Subagent "Evaluate goal completion" invoked
- `[goal] ✓ Goal achieved` on completion

## Checkpoints Expected

0 during goal

## Special Setup

1. `testing/src/utils.ts` — TypeScript file with intentional ESLint violations
2. `testing/.eslintrc.json` — ESLint config with no-console, no-var, no-unused-vars rules
3. ESLint must be installed (`npm install eslint` in testing/)
