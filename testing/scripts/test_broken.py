"""Broken test module — intentionally fails for CI-fix workload."""

from calculator import multiply


def test_multiply_broken():
    # Intentionally wrong expected value
    assert multiply(3, 4) == 11
