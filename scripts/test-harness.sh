#!/usr/bin/env bash
# Deprecated: bash harness unit tests were replaced by pytest.
# Run: pip install -e ".[dev]" && pytest tests -q
# Or:  py -3 scripts/verify.py
echo "test-harness.sh is deprecated. Use: pytest tests -q" >&2
echo "Or run the full pipeline: py -3 scripts/verify.py" >&2
exit 1
