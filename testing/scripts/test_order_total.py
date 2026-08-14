"""Tests for order_total — failing until the double-discount bug is fixed."""

from order_total import order_total


def test_no_discount():
    assert order_total(10.0, 2, 0.0) == 20.0


def test_ten_percent_off():
    assert order_total(10.0, 2, 0.10) == 18.0


def test_qty_one():
    assert order_total(50.0, 1, 0.20) == 40.0
