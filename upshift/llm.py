"""Model providers. Users bring their own key; every client is wrapped by neatlogs so calls are traced.

The agent speaks one message format (Bedrock Converse style: text / toolUse / toolResult blocks).
Each provider translates to and from it.

Configure with environment variables (or a git-ignored .env file):
  UPSHIFT_PROVIDER = anthropic | openai | azure-openai | litellm | bedrock
  UPSHIFT_MODEL    = model name (for Azure: the deployment name; Anthropic defaults to claude-opus-5-5)
  anthropic:    ANTHROPIC_API_KEY (or another credential the Anthropic SDK resolves)
  azure-openai: AZURE_OPENAI_API_KEY, AZURE_OPENAI_ENDPOINT, optional OPENAI_API_VERSION
  openai:       OPENAI_API_KEY, optional OPENAI_BASE_URL for any OpenAI-compatible service
  litellm:      LITELLM_API_KEY, optional LITELLM_BASE_URL (defaults to the neatHack model proxy)
  bedrock:      UPSHIFT_AWS_PROFILE, optional UPSHIFT_AWS_REGION
"""

import json
import os
import time
from types import SimpleNamespace

import neatlogs

from . import tracing

PROVIDERS = ("anthropic", "openai", "azure-openai", "litellm", "bedrock")
ANTHROPIC_DEFAULT_MODEL = "claude-opus-5-5"
# models that accept server-side refusal fallbacks (`fallbacks: "default"`)
ANTHROPIC_FALLBACK_MODELS = {"claude-fable-5-1", "claude-opus-5-5", "claude-opus-5", "claude-sonnet-5-5"}
BEDROCK_DEFAULT_MODEL = "us.anthropic.claude-opus-5"
AZURE_DEFAULT_API_VERSION = "2024-10-21"
STREAM_ATTEMPTS = 3  # a stream that drops midway is retried whole; the SDK only retries before it starts
# the LiteLLM proxy neatHack gives teams (Claude and Amazon Nova models): https://neatlogs.com/hackathon/models
LITELLM_DEFAULT_BASE_URL = "https://litellm.vision.aivar.app/v1"


class LLMConfigError(RuntimeError):
    """Missing or wrong provider settings. Raised before any work starts."""


class LLMError(RuntimeError):
    """A model call failed after the client's own retries."""

    def __init__(self, code: str, message: str = ""):
        super().__init__(f"{code}: {message}"[:500])
        self.code = code

    @property
    def fatal(self) -> bool:
        """Retrying can't help: bad key, unknown model, or the key's budget is spent."""
        return (self.code in ("AuthenticationError", "PermissionDeniedError", "NotFoundError")
                or "budget" in str(self).lower())


def _require(*names: str) -> None:
    missing = [n for n in names if not os.environ.get(n)]
    if missing:
        raise LLMConfigError(f"Missing environment variables: {', '.join(missing)}")


def make_llm():
    provider = os.environ.get("UPSHIFT_PROVIDER", "").strip().lower()
    if provider == "anthropic":
        return AnthropicLLM()
    if provider in ("openai", "azure-openai", "litellm"):
        return OpenAILLM(provider)
    if provider == "bedrock":
        return BedrockLLM()
    raise LLMConfigError(f"Set UPSHIFT_PROVIDER to one of: {', '.join(PROVIDERS)}")


class OpenAILLM:
    """OpenAI, Azure OpenAI, a LiteLLM proxy, or any OpenAI-compatible endpoint (OPENAI_BASE_URL)."""

    def __init__(self, provider: str):
        import openai

        _require("UPSHIFT_MODEL")
        if provider == "azure-openai":
            _require("AZURE_OPENAI_API_KEY", "AZURE_OPENAI_ENDPOINT")
            client = openai.AzureOpenAI(
                api_version=os.environ.get("OPENAI_API_VERSION", AZURE_DEFAULT_API_VERSION),
                max_retries=3, timeout=600)
        elif provider == "litellm":
            _require("LITELLM_API_KEY")
            client = openai.OpenAI(base_url=os.environ.get("LITELLM_BASE_URL", LITELLM_DEFAULT_BASE_URL),
                                   api_key=os.environ["LITELLM_API_KEY"], max_retries=3, timeout=600)
        else:
            _require("OPENAI_API_KEY")
            client = openai.OpenAI(max_retries=3, timeout=600)
        self._errors = openai.APIError
        self._dropped = (openai.APIConnectionError, openai.InternalServerError)
        self.client = neatlogs.wrap(client)
        self.model = os.environ["UPSHIFT_MODEL"]
        self.input_tokens = 0
        self.output_tokens = 0

    def converse(self, system: str, messages: list[dict], tools: list[dict] | None = None,
                 max_tokens: int = 8000) -> dict:
        # streamed: a proxy in front of the model (the neatHack LiteLLM one) cuts off replies that send nothing
        # for about a minute, which a long playbook easily does
        kwargs = {"model": self.model, "messages": to_openai(system, messages),
                  "max_completion_tokens": max_tokens, "stream": True, "stream_options": {"include_usage": True}}
        if tools:
            kwargs["tools"] = [openai_tool(t) for t in tools]
        for attempt in range(1, STREAM_ATTEMPTS + 1):
            try:
                message, usage = collect_stream(self.client.chat.completions.create(**kwargs))
                break
            except self._dropped as exc:
                if attempt == STREAM_ATTEMPTS:
                    raise LLMError(type(exc).__name__, str(exc)) from exc
                tracing.annotate(failure_class=f"llm:{type(exc).__name__}", recovery="retry_whole_call",
                                 retry_attempt=attempt)
                time.sleep(10 * attempt)
            except self._errors as exc:
                raise LLMError(type(exc).__name__, str(exc)) from exc
        if usage:
            self.input_tokens += usage.prompt_tokens
            self.output_tokens += usage.completion_tokens
        return {"output": {"message": from_openai(message)}}


class AnthropicLLM:
    """Claude through the Anthropic API.

    Thinking is on (Claude Opus 5.5 can't turn it off), and Claude rejects a replayed thinking block once an
    earlier turn has been edited. So the history stays append-only: Claude's own content blocks are sent back
    exactly as returned, and old tool results are trimmed by the API's context editing, not by the caller.
    """

    trims_history_itself = True  # FixLoop must not shorten old tool results client-side

    def __init__(self):
        import anthropic

        self._errors = anthropic.APIError
        self.client = neatlogs.wrap(anthropic.Anthropic(max_retries=6))
        self.model = os.environ.get("UPSHIFT_MODEL") or ANTHROPIC_DEFAULT_MODEL
        self.input_tokens = 0
        self.output_tokens = 0

    def converse(self, system: str, messages: list[dict], tools: list[dict] | None = None,
                 max_tokens: int = 8000) -> dict:
        betas = ["context-management-2025-06-27"]
        kwargs = {
            "model": self.model,
            # thinking shares max_tokens with the answer, so leave room; streaming avoids HTTP timeouts
            "max_tokens": max(max_tokens, 32000),
            "system": system,
            "messages": to_anthropic(messages),
            "output_config": {"effort": "high"},
            "cache_control": {"type": "ephemeral"},  # each step resends the whole history; cache the prefix
            "context_management": {"edits": [{
                "type": "clear_tool_uses_20250919",
                "trigger": {"type": "input_tokens", "value": 30000},
                "keep": {"type": "tool_uses", "value": 4},
                "clear_at_least": {"type": "input_tokens", "value": 5000},
            }]},
        }
        if tools:
            kwargs["tools"] = [anthropic_tool(t) for t in tools]
        if self.model in ANTHROPIC_FALLBACK_MODELS:
            # if a safety classifier declines (security fixes can look like exploit work), retry on a fallback model
            betas.append("server-side-fallback-2026-07-01")
            kwargs["fallbacks"] = "default"
        try:
            with self.client.beta.messages.stream(betas=betas, **kwargs) as stream:
                for _ in stream:  # neatlogs reads tokens and output from the streamed events
                    pass
                response = stream.get_final_message()
        except self._errors as exc:
            raise LLMError(type(exc).__name__, str(exc)) from exc
        usage = response.usage
        self.input_tokens += (usage.input_tokens + (usage.cache_creation_input_tokens or 0)
                              + (usage.cache_read_input_tokens or 0))
        self.output_tokens += usage.output_tokens
        if response.stop_reason == "refusal":
            category = response.stop_details.category if response.stop_details else None
            raise LLMError("refusal", f"declined ({category})")
        if response.stop_reason == "max_tokens":
            raise LLMError("max_tokens", "the reply was cut off; a tool call in it may be incomplete")
        return {"output": {"message": from_anthropic(response.content)}}


class BedrockLLM:
    def __init__(self):
        import boto3
        from botocore.config import Config

        # Explicit on purpose: never bill whatever account the default AWS profile points at.
        _require("UPSHIFT_AWS_PROFILE")
        session = boto3.Session(profile_name=os.environ["UPSHIFT_AWS_PROFILE"])
        client = session.client(
            "bedrock-runtime",
            region_name=os.environ.get("UPSHIFT_AWS_REGION", "us-west-2"),
            config=Config(retries={"max_attempts": 8, "mode": "adaptive"}, read_timeout=600),
        )
        self.client = neatlogs.wrap(client)
        self.model = os.environ.get("UPSHIFT_MODEL", BEDROCK_DEFAULT_MODEL)
        self.input_tokens = 0
        self.output_tokens = 0

    def converse(self, system: str, messages: list[dict], tools: list[dict] | None = None,
                 max_tokens: int = 8000) -> dict:
        from botocore.exceptions import ClientError

        kwargs = {
            "modelId": self.model,
            "system": [{"text": system}],
            "messages": messages,
            "inferenceConfig": {"maxTokens": max_tokens},
        }
        if tools:
            kwargs["toolConfig"] = {"tools": [{"toolSpec": t} for t in tools]}
        try:
            response = self.client.converse(**kwargs)
        except ClientError as exc:
            raise LLMError(exc.response.get("Error", {}).get("Code", "ClientError"), str(exc)) from exc
        usage = response.get("usage", {})
        self.input_tokens += usage.get("inputTokens", 0)
        self.output_tokens += usage.get("outputTokens", 0)
        return response


# --- the agent's message format ----------------------------------------------

def tool_spec(name: str, description: str, properties: dict, required: list[str]) -> dict:
    return {
        "name": name,
        "description": description,
        "inputSchema": {"json": {"type": "object", "properties": properties, "required": required}},
    }


def text_of(message: dict) -> str:
    return "\n".join(block["text"] for block in message.get("content", []) if "text" in block)


def tool_uses(message: dict) -> list[dict]:
    return [block["toolUse"] for block in message.get("content", []) if "toolUse" in block]


def tool_result(tool_use_id: str, text: str, error: bool = False) -> dict:
    return {"toolResult": {"toolUseId": tool_use_id, "content": [{"text": text}],
                           "status": "error" if error else "success"}}


# --- Anthropic translation ---------------------------------------------------

def anthropic_tool(spec: dict) -> dict:
    return {"name": spec["name"], "description": spec["description"], "input_schema": spec["inputSchema"]["json"]}


def to_anthropic(messages: list[dict]) -> list[dict]:
    out = []
    for message in messages:
        if message["role"] == "assistant" and "anthropic_content" in message:
            # Claude's own blocks, thinking included, replayed unchanged
            out.append({"role": "assistant", "content": message["anthropic_content"]})
            continue
        content = []
        for block in message.get("content", []):
            if "text" in block:
                content.append({"type": "text", "text": block["text"]})
            elif "toolUse" in block:
                use = block["toolUse"]
                content.append({"type": "tool_use", "id": use["toolUseId"], "name": use["name"], "input": use["input"]})
            elif "toolResult" in block:
                result = block["toolResult"]
                content.append({"type": "tool_result", "tool_use_id": result["toolUseId"],
                                "content": "\n".join(c["text"] for c in result["content"] if "text" in c),
                                "is_error": result.get("status") == "error"})
        out.append({"role": message["role"], "content": content})
    return out


def from_anthropic(content: list) -> dict:
    blocks = []
    for block in content:
        if block.type == "text":
            blocks.append({"text": block.text})
        elif block.type == "tool_use":
            blocks.append({"toolUse": {"toolUseId": block.id, "name": block.name, "input": block.input}})
    return {"role": "assistant", "content": blocks, "anthropic_content": content}


# --- OpenAI translation ------------------------------------------------------

def openai_tool(spec: dict) -> dict:
    return {"type": "function", "function": {
        "name": spec["name"], "description": spec["description"], "parameters": spec["inputSchema"]["json"]}}


def to_openai(system: str, messages: list[dict]) -> list[dict]:
    out = [{"role": "system", "content": system}]
    for message in messages:
        blocks = message.get("content", [])
        text = "\n".join(b["text"] for b in blocks if "text" in b)
        if message["role"] == "assistant":
            calls = [{"id": u["toolUseId"], "type": "function",
                      "function": {"name": u["name"], "arguments": json.dumps(u["input"])}}
                     for u in (b["toolUse"] for b in blocks if "toolUse" in b)]
            entry = {"role": "assistant", "content": text or None}
            if calls:
                entry["tool_calls"] = calls
            out.append(entry)
            continue
        for block in blocks:
            if "toolResult" in block:
                result = block["toolResult"]
                body = "\n".join(c["text"] for c in result["content"] if "text" in c)
                if result.get("status") == "error":
                    body = f"ERROR: {body}"
                out.append({"role": "tool", "tool_call_id": result["toolUseId"], "content": body})
        if text:
            out.append({"role": "user", "content": text})
    return out


def collect_stream(chunks) -> tuple:
    """Rebuild one assistant message (text and tool calls) and the usage from streamed chat chunks."""
    text, calls, usage = [], {}, None
    for chunk in chunks:
        usage = getattr(chunk, "usage", None) or usage
        for choice in chunk.choices:
            delta = choice.delta
            if delta.content:
                text.append(delta.content)
            for call in delta.tool_calls or []:
                slot = calls.setdefault(call.index, {"id": None, "name": "", "arguments": ""})
                slot["id"] = call.id or slot["id"]
                if call.function:
                    slot["name"] = call.function.name or slot["name"]
                    slot["arguments"] += call.function.arguments or ""
    message = SimpleNamespace(content="".join(text) or None, tool_calls=[
        SimpleNamespace(id=c["id"], function=SimpleNamespace(name=c["name"], arguments=c["arguments"]))
        for _, c in sorted(calls.items())])
    return message, usage


def from_openai(message) -> dict:
    blocks = []
    if message.content:
        blocks.append({"text": message.content})
    for call in message.tool_calls or []:
        try:
            args = json.loads(call.function.arguments or "{}")
        except json.JSONDecodeError:
            # handed to the tool, which fails and tells the model its arguments were not valid JSON
            args = {"_invalid_json": call.function.arguments}
        blocks.append({"toolUse": {"toolUseId": call.id, "name": call.function.name, "input": args}})
    return {"role": "assistant", "content": blocks}
