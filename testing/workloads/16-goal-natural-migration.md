# Workload 16: Natural Language API Migration

Test goal evaluation with a natural language condition describing a real-world migration task.

## Prompt

```
/cursor-goal every call site of the old fetch() wrapper has been migrated to the new httpClient and the build succeeds

Migrate testing/scripts/user_service.js and related files from legacy_api.js to http_client.js.
```

## Expected Behavior

1. Agent parses natural language goal condition (no --test or --budget flags)
2. Agent reads legacy_api.js, http_client.js, and user_service.js
3. Agent identifies all legacy fetch call sites
4. Agent migrates each call site to HttpClient
5. Agent verifies build/tests pass
6. Subagent evaluates migration completeness
7. Agent marks goal as achieved

## Features Tested

- F11: Goal state initialization
- F12: Subagent evaluation based on conversation context
- F13b: Goal completion marking
- F15: Natural language condition parsing

## Verification Patterns

- `run_goal.py manage create` without `--test` or `--budget`
- Natural language condition in goal create command
- Subagent "Evaluate goal completion" invoked
- `YES:` or `NO:` evaluation response
- `[goal] ✓ Goal achieved` on completion
- No references to legacyFetch remaining in migrated files

## Checkpoints Expected

0 during goal

## Special Setup

1. `testing/scripts/legacy_api.js` — old fetch wrapper with call sites
2. `testing/scripts/http_client.js` — new HttpClient (stub, ready to use)
3. `testing/scripts/user_service.js` — service layer using legacy fetch
