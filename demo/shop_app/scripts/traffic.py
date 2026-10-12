"""Run some checkouts the way production does: acme calls traced in neatlogs and logged to logs/app.log.

Starts its own acme sandbox (in today's "deprecating" mode) unless ACME_BASE_URL points at one.
    python scripts/traffic.py --orders 5
"""

import argparse
import os
import sys
from decimal import Decimal
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

import neatlogs  # noqa: E402

from shop import payments  # noqa: E402
from tests.acme_sandbox import Sandbox  # noqa: E402


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--orders", type=int, default=5)
    args = parser.parse_args()
    if "ACME_BASE_URL" not in os.environ:
        os.environ["ACME_BASE_URL"] = Sandbox().start().url
    os.environ.setdefault("SHOP_LOG", str(ROOT / "logs" / "app.log"))
    neatlogs.init(api_key=os.environ.get("NEATLOGS_API_KEY"), workflow_name="shop_app", tags=["production"])
    for n in range(1, args.orders + 1):
        with neatlogs.trace("shop.checkout", kind="WORKFLOW"):
            try:
                charged = payments.charge(f"ord_{n}", Decimal("19.99") + n)
                payments.get_charge(charged["id"])
                print(f"ord_{n}: {charged['status']}")
            except payments.PaymentError as exc:
                print(f"ord_{n}: {exc}")
    neatlogs.flush()
    neatlogs.shutdown()


if __name__ == "__main__":
    main()
