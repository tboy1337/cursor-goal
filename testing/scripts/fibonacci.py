"""Fibonacci implementation — intentionally broken for testing."""


def fibonacci(n):
    """Return the nth Fibonacci number (0-indexed)."""
    if n < 0:
        raise ValueError("n must be non-negative")
    if n == 0:
        return 0
    if n == 1:
        return 1
    # BUG: off-by-one error
    return fibonacci(n - 1) + fibonacci(n - 3)
