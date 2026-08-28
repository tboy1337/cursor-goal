# Sample Transcripts

Optional historical / captured Cursor agent transcripts for pattern verification.
They are **not** the primary manual regression pack — use [`testing/workloads/`](../workloads/) for real Cursor scenarios (e.g. `12-goal-with-test.md`).

## Naming Convention

Files should be named `<workload-id>.<ext>` matching workload IDs:

- `.jsonl` (preferred) — raw Cursor agent transcript, supports structured checks (F19-F21)
- `.txt` — converted text format, supports regex checks (F11-F18) only

Examples:

- `12-goal-with-test.jsonl`
- `13-goal-budget.jsonl`
- `15-goal-pause-resume.txt`

Legacy transcript names that do not match a workload ID may remain for historical checks only.

## Generating Samples

### JSONL (preferred — enables structured analysis)

Copy directly from Cursor's transcript directory:

```bash
cp ~/.cursor/projects/<workspace>/agent-transcripts/<uuid>/<uuid>.jsonl \
   testing/samples/<workload-id>.jsonl
```

### Text (fallback — regex checks only)

Use the extraction script or manually export from Cursor.

## Running Checks

Optional transcript scoring only — not pytest and not `scripts/verify.py`:

```bash
cd testing && ../scripts/run-tests.sh
```

## Feature Coverage

| Feature | Regex (.txt) | Structured (.jsonl) | What it checks |
|---------|:---:|:---:|---|
| F11-F18 | yes | yes | Goal lifecycle patterns in text |
| F19 | - | yes | Evaluator uses `subagent_type: "goal-evaluator"` |
| F20 | - | yes | Evaluator runs as `readonly: true` |
| F21 | - | yes | Evaluator prompt contains goal condition |
