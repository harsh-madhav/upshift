import inspect
import os
from configparser import SafeConfigParser
from distutils.util import strtobool

DEFAULTS = {"currency": "$", "shop_name": "Upshift Shop", "send_receipts": "yes", "tax_rate": "0.0"}

HOOKS = []


def load_settings(path=None, environ=None) -> dict:
    environ = os.environ if environ is None else environ
    parser = SafeConfigParser()
    parser.read_dict({"shop": DEFAULTS})
    if path:
        parser.read(path)
    shop = parser["shop"]
    return {
        "currency": environ.get("SHOP_CURRENCY", shop.get("currency")),
        "shop_name": shop.get("shop_name"),
        "send_receipts": bool(strtobool(environ.get("SHOP_SEND_RECEIPTS", shop.get("send_receipts")))),
        "tax_rate": float(shop.get("tax_rate")),
    }


def on_order(hook):
    """Register a hook to run after an order is placed. It takes (order) or (order, settings)."""
    HOOKS.append(hook)
    return hook


def run_hooks(order, settings) -> list:
    results = []
    for hook in HOOKS:
        if len(inspect.getargspec(hook).args) == 2:
            results.append(hook(order, settings))
        else:
            results.append(hook(order))
    return results
