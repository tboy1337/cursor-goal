# Subagent Test Results — 2026-05-26

## Key Finding

**Subagents cannot spawn nested `Task` subagents.** The `Task` tool is not available inside a `generalPurpose` subagent context. This means features that depend on observing `Task` call structure (F12, F19, F20, F21) cannot be tested via subagents.

## Revised Feature Classification

| Category | Features | Count |
|----------|----------|-------|
| **Subagent-testable** | F11, F13a, F13b, F15, F17, F18, F22, F23, F24 | 9 |
| **Requires parent-agent** | F12, F19, F20, F21 (need Task tool for evaluator subagent) | 4 |
| **Requires Cursor IDE** | F13 (stop hook), F14 (pause/resume), F16 (multi-cycle) | 3 |

## Per-Workload Results

| Workload | Pass Rate | Passed | Expected | Notes |
|----------|-----------|--------|----------|-------|
| W1-goal-with-test | 60% | 6 | 10 | F12/F19/F20/F21 fail (no Task tool) |
| W2-natural-language-goal | 63% | 5 | 8 | F12/F19/F20 fail (no Task tool) |
| W3-budget-parsing | **100%** | 3 | 3 | All shell-based features pass |
| W4-goal-clear | **100%** | 3 | 3 | Create + clear lifecycle works |
| W5-no-test-goal | 50% | 4 | 8 | F12/F19/F20/F21 fail (no Task tool) |
| W6-evaluator-correctness | 25% | 1 | 4 | Specifically tests Task structure — inherently untestable |

## Feature Coverage

| Feature | Name | Tested | Passed | Verdict |
|---------|------|--------|--------|---------|
| F11 | Goal state initialization | 5 | 5 | PASS |
| F12 | Subagent evaluation | 4 | 0 | BLOCKED (no Task tool) |
| F13a | Validation command execution | 1 | 1 | PASS |
| F13b | Goal completion marking | 4 | 4 | PASS |
| F15 | Natural language condition parsing | 1 | 1 | PASS |
| F17 | Budget inline parsing | 1 | 1 | PASS |
| F18 | Goal clear/cancel | 1 | 1 | PASS |
| F19 | Evaluator correct subagent_type | 4 | 0 | BLOCKED (no Task tool) |
| F20 | Evaluator runs readonly | 4 | 0 | BLOCKED (no Task tool) |
| F21 | Evaluator prompt has condition | 3 | 0 | BLOCKED (no Task tool) |
| F22 | No self-assessment | 4 | 4 | PASS |
| F23 | Non-empty condition | 6 | 6 | PASS |
| F24 | Done follows evaluator | 2 | 2 | PASS |

## Positive Findings

All subagents correctly followed the /goal protocol:
1. Used `goal-parse.sh` to extract structured args
2. Called `goal-manage.sh create` with correct parameters
3. Performed the actual work (code fixes, validation)
4. Generated evaluator prompts via `goal-eval.sh prompt`
5. Called `goal-eval.sh parse-result` on YES/NO responses
6. Called `goal-eval.sh signal` then `goal-manage.sh done` in sequence

The protocol adherence is high — the only failures are due to infrastructure limitations (no nested Task calls), not skill behavior.

## Recommendations

1. **For F12/F19/F20/F21**: Test via the parent agent directly (not subagent). The parent can spawn evaluator Tasks and inspect the transcript.
2. **For F13/F14/F16**: Use `cursor-agent` sessions with `run-workload.sh`.
3. **Consider**: A "parent-agent test harness" that runs /goal workloads as the main agent (not delegated to subagents) to capture Task call structure.
