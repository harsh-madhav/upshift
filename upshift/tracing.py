"""neatlogs setup. Every job is one trace; phases, tools and LLM calls are spans under it."""

import os

import neatlogs
from opentelemetry import trace as otel

AGENT_VERSION = os.environ.get("UPSHIFT_AGENT_VERSION", "v0")


def init(tags: list[str]) -> None:
    api_key = os.environ.get("NEATLOGS_API_KEY")
    neatlogs.init(
        api_key=api_key,
        workflow_name="upshift",
        tags=[AGENT_VERSION, *tags],
        disable_export=not api_key,
    )


def annotate(**attrs) -> None:
    """Attach upshift.* attributes to the active span (state changes, failure class, retries)."""
    span = otel.get_current_span()
    for key, value in attrs.items():
        if value is not None:
            span.set_attribute(f"upshift.{key}", value if isinstance(value, (str, int, float, bool)) else str(value))


def shutdown() -> None:
    neatlogs.flush()
    neatlogs.shutdown()
