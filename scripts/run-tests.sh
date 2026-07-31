#!/usr/bin/env bash
# Run all pattern checks against sample transcripts
# Usage: ./scripts/run-tests.sh [workload-id]
#
# Supports .txt (converted) and .jsonl (raw) sample files.

set -euo pipefail
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_ROOT="$(cd "${SCRIPT_DIR}/.." && pwd)"
TESTING_DIR="${REPO_ROOT}/testing"

echo "=== cursor-goal Test Suite ==="
echo ""

SAMPLES_DIR="${TESTING_DIR}/samples"
if [ ! -d "$SAMPLES_DIR" ]; then
    echo "No sample transcripts found in ${SAMPLES_DIR}"
    echo "Run workloads in Cursor first, then copy transcripts here."
    echo ""
    echo "Available workloads:"
    for f in "${TESTING_DIR}/workloads/"*.md; do
        echo "  $(basename "$f" .md)"
    done
    exit 0
fi

TOTAL=0
PASSED=0
FAILED_LIST=""

for sample in "${SAMPLES_DIR}"/*.txt "${SAMPLES_DIR}"/*.jsonl; do
    [ -f "$sample" ] || continue
    BASENAME=$(basename "$sample")
    WORKLOAD="$BASENAME"
    echo "Testing: $BASENAME"
    RESULT=$(python3 "${TESTING_DIR}/scripts/patterns.py" "$sample" "$WORKLOAD" 2>/dev/null) || {
        echo "  SKIP (no matching workload features)"
        continue
    }
    PASS_RATE=$(echo "$RESULT" | python3 -c "import sys,json; print(json.load(sys.stdin)['pass_rate'])" 2>/dev/null) || PASS_RATE="0"
    EXPECTED=$(echo "$RESULT" | python3 -c "import sys,json; print(json.load(sys.stdin)['features_expected'])" 2>/dev/null) || EXPECTED="?"
    PASS_COUNT=$(echo "$RESULT" | python3 -c "import sys,json; print(json.load(sys.stdin)['features_passed'])" 2>/dev/null) || PASS_COUNT="?"
    TOTAL=$((TOTAL + 1))
    if [ "$(echo "$PASS_RATE == 1.0" | bc -l 2>/dev/null || echo 0)" = "1" ]; then
        echo "  PASS (${PASS_COUNT}/${EXPECTED})"
        PASSED=$((PASSED + 1))
    else
        echo "  PARTIAL (${PASS_COUNT}/${EXPECTED})"
        FAILED_LIST="${FAILED_LIST}  ${WORKLOAD}.${BASENAME##*.}\n"
        echo "$RESULT" | python3 -c "
import sys, json
d = json.load(sys.stdin)
for fid, r in sorted(d['details'].items()):
    status = 'PASS' if r['found'] else 'FAIL'
    detail = r.get('detail', f'count={r[\"count\"]}')
    print(f'    {fid}: {status} ({detail})')
" 2>/dev/null || true
    fi
done

echo ""
echo "Results: $PASSED/$TOTAL workloads fully passed"
if [ -n "$FAILED_LIST" ]; then
    echo ""
    echo "Failures:"
    echo -e "$FAILED_LIST"
fi
