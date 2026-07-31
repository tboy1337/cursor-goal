"""Tests for fibonacci implementation."""

from fibonacci import fibonacci


def test_fibonacci_zero():
    assert fibonacci(0) == 0


def test_fibonacci_one():
    assert fibonacci(1) == 1


def test_fibonacci_small():
    assert fibonacci(5) == 5
    assert fibonacci(6) == 8
    assert fibonacci(10) == 55


def test_fibonacci_negative():
    try:
        fibonacci(-1)
        assert False, "Should raise ValueError"
    except ValueError:
        pass


def test_fibonacci_sequence():
    expected = [0, 1, 1, 2, 3, 5, 8, 13, 21, 34]
    for i, val in enumerate(expected):
        assert fibonacci(i) == val, f"fibonacci({i}) should be {val}, got {fibonacci(i)}"
