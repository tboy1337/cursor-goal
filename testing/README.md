# Testing cursor-goal

This directory holds **manual / IDE-level** test material. Automated unit
tests live in [`tests/`](../tests) (`pytest tests -q`) and are the primary,
CI-enforced gate (`scripts/verify.py`). Everything here is a supplement for
scenarios that need a real Cursor IDE session — hook delivery, multi-turn
continuation, and subagent `Task` behavior cannot be fully exercised by
`pytest` alone.

## Continuation protocol these tests assume

As of the [Unreleased] changes in [`CHANGELOG.md`](../CHANGELOG.md), the
continuation story is:

1. **`stop` hook (documented, primary).** Fires at the end of every agent
   turn; `followup_message` re-prompts the worker. Sequential dual hook
   entries (marketplace ships both a `cmd` and a `python3` entry) are
   deduped by `generation_id` so a turn is never double-charged.
2. **`subagentStop` hook, `matcher: "goal-evaluator"` (documented, primary).**
   Fires when the evaluator subagent completes; `followup_message` nudges
   the worker to run `eval parse-result` immediately, without waiting for
   the end-of-turn `stop` hook.
3. **Wake watchdog (undocumented, best-effort supplement).** A background
   Shell loop using `notify_on_output`, only useful if you explicitly armed
   it (`wake arm`/`wake loop`). Cursor may reap idle background shells, so
   do not rely on wake alone — it is not required for `eval validate` /
   `prompt` / `spawn-config` to work (see `CURSOR_GOAL_REQUIRE_WAKE=1` in
   [docs/known-limitations.md](../docs/known-limitations.md) if you want the
   old hard-refusal behavior when wake is dead).

When writing or updating a workload, verify hook-driven continuation (1–2)
before treating wake as a requirement — most workloads should pass without
ever arming wake.

## Layout

| Path | Purpose |
|------|---------|
| [`workloads/`](workloads) | Manual regression scripts: paste the `## Prompt` into Cursor, follow `## Expected Behavior`, confirm `## Verification Patterns` appear in the transcript. |
| [`subagent-tests/`](subagent-tests) | Automated `Task`-subagent runner for the subset of features (`F11`–`F24`, see its README) that can be checked in a single-turn subagent without full multi-turn Cursor infrastructure. |
| [`samples/`](samples) | Optional captured transcripts (`.jsonl`/`.txt`) for regex/structured pattern verification against past runs; not the primary pack. |
| [`scripts/`](scripts) | Fixture source files (intentionally broken/incomplete) that workloads point `--test` at, e.g. `fibonacci.py` + `test_fibonacci.py`. |
| [`src/utils.ts`](src) | TypeScript fixture for refactor/lint-style workloads. |

## Running

Automated subagent checks:

```bash
cd testing/subagent-tests
python3 run-subagent-tests.py
```

Pattern checks against captured transcripts (if any are present in
`testing/samples/`):

```bash
./scripts/run-tests.sh
# or a single sample:
python3 testing/scripts/patterns.py testing/samples/12-goal-with-test.jsonl 12-goal-with-test
```

Manual workload (in Cursor): open a workload file under `workloads/`, paste
its `## Prompt` block into a fresh `/goal` session, and check off
`## Verification Patterns` against the transcript. Capture the transcript
into `testing/samples/<workload-id>.jsonl` afterward if you want it to
become a regression sample (see [`samples/README.md`](samples/README.md)).

## Adding a new workload

1. Add `testing/workloads/NN-goal-<name>.md` following the structure of an
   existing workload (`## Prompt`, `## Expected Behavior`,
   `## Features Tested`, `## Verification Patterns`, `## Checkpoints
   Expected`, `## Special Setup` if fixtures are needed).
2. If it needs new fixture files, add them under `testing/scripts/` (or
   `testing/src/` for TypeScript) and reference them by relative path from
   the repo root in `--test`.
3. If the workload exercises a feature not yet in the `F##` numbering used
   across `subagent-tests/README.md` / `samples/README.md` / other
   workloads, allocate the next free `F##` id and use it consistently.
