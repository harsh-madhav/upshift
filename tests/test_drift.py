from upshift.drift import findings, links, template
from upshift.research import describe_openapi

LINK = '<http://acme.test/docs/migration>; rel="deprecation", <http://acme.test/v2/openapi.json>; rel="successor-version"'


def signal(**kw):
    return {"api": "acme", "method": "POST", "url": "http://acme.test/v1/charges", "status": 201,
            "source": "log app.log", **kw}


def test_only_drifting_calls_count_and_group_by_api():
    apis = findings([
        signal(deprecation="@1788220800", sunset="Sat, 31 Oct 2026 23:59:59 GMT", link=LINK),
        signal(method="GET", url="http://acme.test/v1/charges/ch_42", deprecation="@1788220800", link=LINK,
               source="neatlogs trace abc", trace_id="abc"),
        signal(status=410, url="http://acme.test/v1/charges"),
        {"api": "other", "method": "GET", "url": "http://other.test/v3/items", "status": 200, "source": "log app.log"},
    ])
    assert list(apis) == ["acme"]
    acme = apis["acme"]
    assert (acme["calls"], acme["gone"], acme["old"], acme["new"]) == (3, 1, "v1", "v2")
    assert acme["endpoints"] == ["GET /v1/charges/{id}", "POST /v1/charges"]
    assert acme["successor"] == "http://acme.test/v2/openapi.json"
    assert acme["deprecation_doc"] == "http://acme.test/docs/migration"
    assert acme["sunset"] == "Sat, 31 Oct 2026 23:59:59 GMT"
    assert acme["sources"] == ["log", "neatlogs"] and acme["traces"] == ["abc"]


def test_link_header_and_path_templates():
    assert links(LINK)["successor-version"] == "http://acme.test/v2/openapi.json"
    assert template("/v1/charges/ch_42") == "/v1/charges/{id}"
    assert template("/v2/payments") == "/v2/payments"


def test_openapi_operations_are_described_with_required_fields():
    spec = {"paths": {"/v2/payments": {"post": {
        "parameters": [{"name": "Idempotency-Key", "in": "header", "required": True}],
        "requestBody": {"content": {"application/json": {"schema": {
            "type": "object", "required": ["amount_cents"], "additionalProperties": False,
            "properties": {"amount_cents": {"type": "integer"}, "reference": {"type": "string"}}}}}},
        "responses": {"201": {}}}}}}
    assert describe_openapi(spec) == [
        "POST /v2/payments headers ['Idempotency-Key*'] body {amount_cents*:integer, reference:string} "
        "(no other fields) -> 201"]
