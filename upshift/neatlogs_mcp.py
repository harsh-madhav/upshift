"""A minimal client for the neatlogs MCP server (streamable HTTP, JSON-RPC)."""

import json
import os

import httpx

URL = "https://ingest.neatlogs.com/mcp"


class Client:
    def __init__(self, api_key: str | None = None):
        key = api_key or os.environ.get("NEATLOGS_API_KEY")
        if not key:
            raise RuntimeError("NEATLOGS_API_KEY is not set")
        self.http = httpx.Client(timeout=120)
        self.headers = {"Authorization": f"Bearer {key}", "Content-Type": "application/json",
                        "Accept": "application/json, text/event-stream"}
        response = self._post({"jsonrpc": "2.0", "id": 1, "method": "initialize", "params": {
            "protocolVersion": "2025-06-18", "capabilities": {}, "clientInfo": {"name": "upshift", "version": "0.1"}}})
        if session := response.headers.get("mcp-session-id"):
            self.headers["mcp-session-id"] = session
        self._post({"jsonrpc": "2.0", "method": "notifications/initialized"})
        self.ids = 2

    def _post(self, payload: dict) -> httpx.Response:
        response = self.http.post(URL, headers=self.headers, json=payload)
        response.raise_for_status()
        return response

    def call(self, tool: str, arguments: dict) -> dict:
        self.ids += 1
        response = self._post({"jsonrpc": "2.0", "id": self.ids, "method": "tools/call",
                               "params": {"name": tool, "arguments": arguments}})
        message = response.json() if "json" in response.headers.get("content-type", "") else next(
            json.loads(line[5:]) for line in response.text.splitlines() if line.startswith("data:"))
        result = message.get("result", {})
        text = "".join(part.get("text", "") for part in result.get("content", []))
        if result.get("isError"):
            raise RuntimeError(f"neatlogs {tool} failed: {text[:300]}")
        return json.loads(text) if text else {}
