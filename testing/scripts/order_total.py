"""Order total helper — intentionally broken for systematic-debug workloads."""


def order_total(unit_price, qty, discount_pct):
    """Return the payable total after a single percentage discount.

    ``discount_pct`` is a fraction (0.10 means 10% off).
    """
    subtotal = unit_price * qty
    discounted = subtotal * (1.0 - discount_pct)
    # BUG: discount applied a second time — tests fail until the root cause
    # (double discount) is removed, not until expected values are hardcoded.
    return discounted * (1.0 - discount_pct)
