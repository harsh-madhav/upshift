"""acme payments sandbox, vendored from acme's test kit. Tests run the shop against it instead of the live API.

ACME_SANDBOX_MODE picks how v1 behaves, following acme's announced timeline:
  ok           v1 as it always was
  deprecating  v1 still works, and every response says it is going away (today, in production)
  sunset       v1 answers 410 Gone (after 31 Oct 2026)
v2 works in every mode.

Run it standalone: python tests/acme_sandbox.py --port 8765 --mode deprecating
"""

import argparse
import itertools
import json
import os
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

SUNSET = "Sat, 31 Oct 2026 23:59:59 GMT"
DEPRECATED_SINCE = "@1788220800"  # 1 Sep 2026, RFC 9745 date format
CURRENCIES = {"USD", "EUR", "GBP", "INR"}

MIGRATION_GUIDE = """# Migrating from acme v1 charges to v2 payments

acme API v1 is deprecated and will be shut down on 31 Oct 2026. After that date every v1 request returns
410 Gone. Move to v2 before then.

## Create a payment
v1: `POST /v1/charges` with JSON `{"amount": "24.98", "currency": "usd", "order_id": "ord_1"}`
v2: `POST /v2/payments` with JSON `{"amount_cents": 2498, "currency": "USD", "reference": "ord_1"}`

- `amount` (decimal string, major units) is replaced by `amount_cents` (integer, minor units).
- `currency` is now upper case (ISO 4217).
- `order_id` is renamed `reference`.
- An `Idempotency-Key` header is required. Send a value that is unique per payment attempt and reuse it when
  retrying the same attempt, so a retry never charges twice. Requests without it get 400.
- Unknown fields are rejected with 422.

## Responses
v2 wraps the object: `{"payment": {"id": "pay_...", "state": "captured", "amount_cents": 2498, ...}}`.
v1 `status: "succeeded"` corresponds to v2 `state: "captured"`; v1 `status: "failed"` to `state: "declined"`.

## Read a payment
v1: `GET /v1/charges/{id}` returns the charge.
v2: `GET /v2/payments/{id}` returns `{"payment": {...}}`.
"""


def openapi(version: str) -> dict:
    if version == "v1":
        charge = {"type": "object", "properties": {"id": {"type": "string"}, "status": {"type": "string"},
                                                   "amount": {"type": "string"}, "currency": {"type": "string"},
                                                   "order_id": {"type": "string"}}}
        return {"openapi": "3.1.0", "info": {"title": "acme payments", "version": "v1", "deprecated": True},
                "paths": {
                    "/v1/charges": {"post": {"deprecated": True, "requestBody": {"content": {"application/json": {
                        "schema": {"type": "object", "required": ["amount", "currency", "order_id"], "properties": {
                            "amount": {"type": "string"}, "currency": {"type": "string"},
                            "order_id": {"type": "string"}}}}}},
                        "responses": {"201": {"content": {"application/json": {"schema": charge}}}}}},
                    "/v1/charges/{id}": {"get": {"deprecated": True,
                        "responses": {"200": {"content": {"application/json": {"schema": charge}}}}}}}}
    payment = {"type": "object", "properties": {"payment": {"type": "object", "properties": {
        "id": {"type": "string"}, "state": {"type": "string", "enum": ["captured", "declined"]},
        "amount_cents": {"type": "integer"}, "currency": {"type": "string"}, "reference": {"type": "string"}}}}}
    return {"openapi": "3.1.0", "info": {"title": "acme payments", "version": "v2"},
            "paths": {
                "/v2/payments": {"post": {
                    "parameters": [{"name": "Idempotency-Key", "in": "header", "required": True,
                                    "schema": {"type": "string"}}],
                    "requestBody": {"content": {"application/json": {"schema": {
                        "type": "object", "required": ["amount_cents", "currency", "reference"],
                        "additionalProperties": False, "properties": {
                            "amount_cents": {"type": "integer", "minimum": 1},
                            "currency": {"type": "string", "pattern": "^[A-Z]{3}$"},
                            "reference": {"type": "string"}}}}}},
                    "responses": {"201": {"content": {"application/json": {"schema": payment}}}}}},
                "/v2/payments/{id}": {"get": {
                    "responses": {"200": {"content": {"application/json": {"schema": payment}}}}}}}}


class Sandbox(ThreadingHTTPServer):
    def __init__(self, port: int = 0, mode: str | None = None):
        super().__init__(("127.0.0.1", port), Handler)
        self.mode = mode or os.environ.get("ACME_SANDBOX_MODE", "deprecating")
        self.records: dict[str, dict] = {}
        self.ids = itertools.count(1)

    @property
    def url(self) -> str:
        return f"http://127.0.0.1:{self.server_port}"

    def start(self) -> "Sandbox":
        threading.Thread(target=self.serve_forever, daemon=True).start()
        return self


class Handler(BaseHTTPRequestHandler):
    server: Sandbox

    def log_message(self, *args):
        pass

    def reply(self, status: int, body, headers: dict | None = None, content_type: str = "application/json"):
        data = body.encode() if isinstance(body, str) else json.dumps(body).encode()
        self.send_response(status)
        self.send_header("Content-Type", content_type)
        self.send_header("Content-Length", str(len(data)))
        for key, value in (headers or {}).items():
            self.send_header(key, value)
        self.end_headers()
        self.wfile.write(data)

    def v1_headers(self) -> dict:
        if self.server.mode == "ok":
            return {}
        base = self.server.url
        return {"Deprecation": DEPRECATED_SINCE, "Sunset": SUNSET,
                "Link": f'<{base}/docs/migration>; rel="deprecation", <{base}/v2/openapi.json>; rel="successor-version"'}

    def body(self) -> dict | None:
        try:
            return json.loads(self.rfile.read(int(self.headers.get("Content-Length") or 0)) or b"{}")
        except json.JSONDecodeError:
            return None

    def do_GET(self):
        path = self.path.split("?")[0]
        if path in ("/v1/openapi.json", "/v2/openapi.json"):
            return self.reply(200, openapi(path.split("/")[1]))
        if path == "/docs/migration":
            return self.reply(200, MIGRATION_GUIDE, content_type="text/markdown")
        if path.startswith("/v1/charges/"):
            return self.v1(lambda: self.found(path, "ch_"))
        if path.startswith("/v2/payments/"):
            record = self.server.records.get(path.rsplit("/", 1)[1])
            if not record or not record["id"].startswith("pay_"):
                return self.reply(404, {"error": "no such payment"})
            return self.reply(200, {"payment": {k: v for k, v in record.items() if k != "_key"}})
        self.reply(404, {"error": "not found"})

    def found(self, path: str, prefix: str):
        record = self.server.records.get(path.rsplit("/", 1)[1])
        if not record or not record["id"].startswith(prefix):
            return self.reply(404, {"error": "no such charge"}, self.v1_headers())
        self.reply(200, record, self.v1_headers())

    def do_POST(self):
        path = self.path.split("?")[0]
        if path == "/v1/charges":
            return self.v1(self.create_charge)
        if path == "/v2/payments":
            return self.create_payment()
        self.reply(404, {"error": "not found"})

    def v1(self, handle):
        if self.server.mode == "sunset":
            return self.reply(410, {"error": f"acme v1 was shut down on {SUNSET}. Use /v2/payments."},
                              {"Sunset": SUNSET, "Link": f'<{self.server.url}/docs/migration>; rel="deprecation"'})
        handle()

    def create_charge(self):
        body = self.body() or {}
        if not {"amount", "currency", "order_id"} <= set(body):
            return self.reply(422, {"error": "amount, currency and order_id are required"}, self.v1_headers())
        record = {"id": f"ch_{next(self.server.ids)}", "status": "succeeded", "amount": str(body["amount"]),
                  "currency": body["currency"], "order_id": body["order_id"]}
        self.server.records[record["id"]] = record
        self.reply(201, record, self.v1_headers())

    def create_payment(self):
        if not self.headers.get("Idempotency-Key"):
            return self.reply(400, {"error": "Idempotency-Key header is required"})
        body = self.body()
        allowed = {"amount_cents", "currency", "reference"}
        if body is None or set(body) - allowed or not allowed <= set(body):
            return self.reply(422, {"error": f"body must have exactly {sorted(allowed)}"})
        if not isinstance(body["amount_cents"], int) or body["amount_cents"] < 1 or body["currency"] not in CURRENCIES:
            return self.reply(422, {"error": "amount_cents must be a positive integer and currency upper-case ISO 4217"})
        key = self.headers["Idempotency-Key"]
        existing = next((r for r in self.server.records.values() if r.get("_key") == key), None)
        if existing:
            return self.reply(201, {"payment": {k: v for k, v in existing.items() if k != "_key"}})
        record = {"id": f"pay_{next(self.server.ids)}", "state": "captured", **body, "_key": key}
        self.server.records[record["id"]] = record
        self.reply(201, {"payment": {k: v for k, v in record.items() if k != "_key"}})


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    parser.add_argument("--port", type=int, default=8765)
    parser.add_argument("--mode", choices=["ok", "deprecating", "sunset"])
    args = parser.parse_args()
    sandbox = Sandbox(args.port, args.mode)
    print(f"acme sandbox ({sandbox.mode}) on {sandbox.url}")
    sandbox.serve_forever()
