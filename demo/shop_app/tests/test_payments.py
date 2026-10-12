from decimal import Decimal

import pytest

from shop.orders import checkout, parse_order
from shop.payments import PaymentError, charge, get_charge

ORDER = {
    "orderId": "ord_7",
    "customer": {"id": 1, "name": "Ada", "email": "ada@example.com"},
    "items": [{"sku": "ABC-0001", "quantity": 2, "unit_price": "9.99"}, {"sku": "XYZ-1234", "quantity": 1, "unit_price": "5.00"}],
}


def test_checkout_charges_the_order_total():
    payment = checkout(parse_order(ORDER))
    assert payment["status"] == "succeeded"
    assert payment["amount"] == Decimal("24.98")


def test_charge_can_be_read_back():
    created = charge("ord_8", Decimal("10.50"))
    fetched = get_charge(created["id"])
    assert fetched == created


def test_amounts_keep_their_cents():
    assert charge("ord_9", Decimal("0.07"))["amount"] == Decimal("0.07")


def test_unknown_charge_raises():
    with pytest.raises(PaymentError):
        get_charge("does_not_exist")
