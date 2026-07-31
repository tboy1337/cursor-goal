# Sample Transcripts

Place agent session transcripts here for automated pattern verification.

## Naming Convention

Files should be named `<workload-id>.<ext>`:
- `.jsonl` (preferred) — raw Cursor agent transcript, supports structured checks (F19-F21)
- `.txt` — converted text format, supports regex checks (F11-F18) only

Examples:
- `12-goal-with-test.jsonl`
- `goal-en-subtitle-fix.jsonl`

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
