import json
from decimal import Decimal

from .models import Customer, Order


def parse_order(payload: dict) -> Order:
    return Order.parse_obj(payload)


def parse_order_json(raw: str) -> Order:
    return Order.parse_raw(raw)


def order_to_json(order: Order) -> str:
    return order.json(by_alias=True, exclude={"created_at"})


def order_total(order: Order) -> Decimal:
    return sum((item.total for item in order.items), Decimal("0"))


def order_summary(order: Order) -> dict:
    data = order.dict(include={"id", "coupon"})
    data["total"] = str(order_total(order))
    data["item_count"] = len(order.items)
    return data


def apply_coupon(order: Order, code: str) -> Order:
    return order.copy(update={"coupon": code.upper()})


def order_fields() -> list:
    return list(Order.__fields__.keys())


def order_schema() -> dict:
    return Order.schema()


def customer_from_row(row) -> Customer:
    return Customer.from_orm(row)


def export_orders(orders) -> str:
    return json.dumps([json.loads(order_to_json(order)) for order in orders])
