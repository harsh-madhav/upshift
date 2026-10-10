import json
from dataclasses import dataclass
from decimal import Decimal

import pytest
from pydantic import ValidationError

from shop.orders import (apply_coupon, customer_from_row, export_orders, order_fields, order_schema,
                         order_summary, order_to_json, order_total, parse_order, parse_order_json)

PAYLOAD = {
    "orderId": "ord_42",
    "customer": {"id": 7, "name": "  Grace  ", "email": "Grace@Example.com"},
    "items": [
        {"sku": "ABC-0001", "quantity": 2, "unit_price": "9.99"},
        {"sku": "XYZ-1234", "quantity": 1, "unit_price": "5.00"},
    ],
    "coupon": "spring",
}


def test_parse_by_alias():
    order = parse_order(PAYLOAD)
    assert order.id == "ord_42"
    assert len(order.items) == 2


def test_parse_by_field_name():
    order = parse_order({**{k: v for k, v in PAYLOAD.items() if k != "orderId"}, "id": "ord_43"})
    assert order.id == "ord_43"


def test_parse_json():
    order = parse_order_json(json.dumps(PAYLOAD))
    assert order.customer.email == "grace@example.com"


def test_customer_is_normalised():
    customer = parse_order(PAYLOAD).customer
    assert customer.name == "Grace"
    assert customer.email == "grace@example.com"


def test_coupon_is_uppercased():
    assert parse_order(PAYLOAD).coupon == "SPRING"


def test_bad_sku_rejected():
    bad = {**PAYLOAD, "items": [{"sku": "abc-1", "quantity": 1, "unit_price": "1"}]}
    with pytest.raises(ValidationError):
        parse_order(bad)


def test_quantity_must_be_positive():
    bad = {**PAYLOAD, "items": [{"sku": "ABC-0001", "quantity": 0, "unit_price": "1"}]}
    with pytest.raises(ValidationError):
        parse_order(bad)


def test_order_needs_items():
    with pytest.raises(ValidationError):
        parse_order({**PAYLOAD, "items": []})


def test_total():
    assert order_total(parse_order(PAYLOAD)) == Decimal("24.98")


def test_summary():
    assert order_summary(parse_order(PAYLOAD)) == {
        "id": "ord_42", "coupon": "SPRING", "total": "24.98", "item_count": 2}


def test_json_uses_aliases_and_drops_timestamp():
    data = json.loads(order_to_json(parse_order(PAYLOAD)))
    assert data["orderId"] == "ord_42"
    assert "created_at" not in data
    assert Decimal(str(data["items"][0]["unit_price"])) == Decimal("9.99")


def test_export_orders():
    exported = json.loads(export_orders([parse_order(PAYLOAD)]))
    assert exported[0]["customer"]["email"] == "grace@example.com"


def test_apply_coupon_returns_copy():
    order = parse_order(PAYLOAD)
    updated = apply_coupon(order, "summer")
    assert updated.coupon == "SUMMER"
    assert order.coupon == "SPRING"


def test_fields():
    assert order_fields() == ["id", "customer", "items", "coupon", "created_at"]


def test_schema_has_example():
    schema = order_schema()
    assert schema["example"]["orderId"] == "ord_1"
    assert "orderId" in schema["properties"]


@dataclass
class CustomerRow:
    id: int
    name: str
    email: str


def test_customer_from_row():
    customer = customer_from_row(CustomerRow(id=3, name="Lin", email="LIN@example.com"))
    assert customer.email == "lin@example.com"
