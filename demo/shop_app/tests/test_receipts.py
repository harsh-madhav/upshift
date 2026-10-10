import copy

from shop.orders import parse_order
from shop.receipts import footer, render_receipt

PAYLOAD = {
    "orderId": "ord_42",
    "customer": {"id": 7, "name": "Grace", "email": "grace@example.com"},
    "items": [
        {"sku": "ABC-0001", "quantity": 2, "unit_price": "9.99"},
        {"sku": "XYZ-1234", "quantity": 1, "unit_price": "5.00"},
    ],
}


def test_receipt_lists_items_and_total():
    html = render_receipt(parse_order(PAYLOAD))
    assert "<li>2 x ABC-0001: $19.98</li>" in html
    assert "Total: $24.98" in html


def test_currency_comes_from_the_environment():
    assert "Total: €24.98" in render_receipt(parse_order(PAYLOAD), currency="€")


def test_customer_label_uses_shop_name_from_context():
    html = render_receipt(parse_order(PAYLOAD), shop_name="Corner Shop")
    assert "Grace &lt;grace@example.com&gt;, Corner Shop customer" in html


def test_customer_name_is_escaped():
    payload = copy.deepcopy(PAYLOAD)
    payload["customer"]["name"] = "<script>alert(1)</script>"
    html = render_receipt(parse_order(payload))
    assert "<script>" not in html and "&lt;script&gt;" in html


def test_footer_is_markup_and_escapes_the_shop_name():
    assert str(footer("Tom & Jerry")) == "<small>Thank you for shopping at Tom &amp; Jerry</small>"
    assert "<small>Thank you for shopping at Upshift Shop</small>" in render_receipt(parse_order(PAYLOAD))
