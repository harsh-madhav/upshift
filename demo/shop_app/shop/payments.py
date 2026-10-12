import json
import os
import time
from contextlib import nullcontext
from decimal import Decimal

import requests

try:
    import neatlogs  # optional: production traces each acme call
except ImportError:
    neatlogs = None


class PaymentError(Exception):
    pass


def base_url() -> str:
    return os.environ.get("ACME_BASE_URL", "https://api.acme-payments.example")


def charge(order_id: str, amount: Decimal) -> dict:
    """Charge an order through acme. Returns {"id", "status", "amount"}; status is "succeeded" or "failed"."""
    with _traced("acme.create_charge") as span:
        response = requests.post(f"{base_url()}/v1/charges", timeout=10,
                                 json={"amount": str(amount), "currency": "usd", "order_id": order_id})
        _record(span, response)
    if response.status_code != 201:
        raise PaymentError(f"acme returned {response.status_code}: {response.text[:200]}")
    data = response.json()
    return {"id": data["id"], "status": data["status"], "amount": Decimal(data["amount"])}


def get_charge(charge_id: str) -> dict:
    with _traced("acme.get_charge") as span:
        response = requests.get(f"{base_url()}/v1/charges/{charge_id}", timeout=10)
        _record(span, response)
    if response.status_code != 200:
        raise PaymentError(f"acme returned {response.status_code}: {response.text[:200]}")
    data = response.json()
    return {"id": data["id"], "status": data["status"], "amount": Decimal(data["amount"])}


def _traced(name: str):
    return neatlogs.trace(name, kind="TOOL") if neatlogs else nullcontext()


def _record(span, response) -> None:
    """Log every acme response, with the headers acme uses to announce API changes."""
    entry = {"ts": time.strftime("%Y-%m-%dT%H:%M:%S"), "api": "acme", "method": response.request.method,
             "url": response.url, "status": response.status_code,
             "deprecation": response.headers.get("Deprecation"), "sunset": response.headers.get("Sunset"),
             "link": response.headers.get("Link")}
    log_path = os.environ.get("SHOP_LOG")
    if log_path:
        os.makedirs(os.path.dirname(log_path) or ".", exist_ok=True)
        with open(log_path, "a") as log:
            log.write(json.dumps(entry) + "\n")
    if span is not None:  # the span's input and output are what the tracing backend shows and searches
        span.set_attribute("input.value", f"{entry['method']} {entry['url']}")
        span.set_attribute("output.value", json.dumps({k: v for k, v in entry.items() if v is not None}))
