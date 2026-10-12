"""API drift trigger: find third-party API calls that the provider says are going away, in neatlogs traces
and the app's own logs, and turn each API into a migration job.

A signal is one logged API response: {"api", "method", "url", "status", "deprecation", "sunset", "link"}, as the
app's HTTP client records it (the demo shop writes it to logs/app.log and to its neatlogs span output).
Drift is any response with a Deprecation or Sunset header, or 410 Gone.
"""

import json
import re
import time
from pathlib import Path
from urllib.parse import urlsplit

import neatlogs

from . import tracing
from .state import RUNS, RunState

LINK = re.compile(r'<([^>]+)>\s*;\s*rel="?([^";,]+)"?')
VERSION = re.compile(r"/(v\d+)(?:/|$)")


def signals_from_log(path: Path) -> list[dict]:
    signals = []
    for line in Path(path).read_text().splitlines():
        try:
            entry = json.loads(line)
        except json.JSONDecodeError:
            continue
        if isinstance(entry, dict) and entry.get("url"):
            signals.append({**entry, "source": f"log {Path(path).name}"})
    return signals


@neatlogs.span(kind="RETRIEVER", tool_name="neatlogs_drift_search")
def signals_from_neatlogs(since_minutes: int = 1440, max_traces: int = 20) -> list[dict]:
    """Search recent traces over the neatlogs MCP for API responses that carry deprecation signals."""
    from .neatlogs_mcp import Client

    client = Client()
    since = time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime(time.time() - since_minutes * 60))
    trace_ids = []
    # keyword search splits on hyphens ("successor-version" finds nothing) and doesn't apply created_after,
    # so search single words, filter by time here, and skip upshift's own traces (its prompts say "deprecation")
    for query in ("successor", "deprecation", "gone"):
        found = client.call("search_traces", {"query": query, "limit": 50, "created_after": since})
        trace_ids += [r["trace_id"] for r in found.get("results", [])
                      if r.get("created_at", "") >= since and not (r.get("workflow_name") or "").startswith("upshift")
                      and r["trace_id"] not in trace_ids]
    signals = []
    for trace_id in trace_ids[:max_traces]:
        context = client.call("get_trace_context", {"trace_id": trace_id, "include_evaluations": False})
        for span in context.get("spans", []):
            try:
                entry = json.loads(span.get("output_value") or "")
            except json.JSONDecodeError:
                continue
            if isinstance(entry, dict) and entry.get("url"):
                signals.append({**entry, "source": f"neatlogs trace {trace_id}", "trace_id": trace_id})
    tracing.annotate(traces_searched=len(trace_ids), signals=len(signals))
    return signals


def template(path: str) -> str:
    """/v1/charges/ch_42 -> /v1/charges/{id}: a segment with a digit is an id, unless it is the version."""
    return "/".join("{id}" if re.search(r"\d", seg) and not re.fullmatch(r"v\d+", seg) else seg
                    for seg in path.split("/"))


def links(header: str | None) -> dict[str, str]:
    return {rel.strip(): url for url, rel in LINK.findall(header or "")}


def findings(signals: list[dict]) -> dict[str, dict]:
    """Group drifting calls by API: which endpoints, since when, until when, and where the successor is."""
    apis: dict[str, dict] = {}
    for s in sorted(signals, key=lambda s: s.get("ts", "")):  # newest last, so its links win
        if not (s.get("deprecation") or s.get("sunset") or s.get("status") == 410):
            continue
        url = urlsplit(s["url"])
        name = s.get("api") or url.hostname
        found = apis.setdefault(name, {"api": name, "base_url": f"{url.scheme}://{url.netloc}", "calls": 0,
                                       "gone": 0, "endpoints": set(), "sources": set(), "traces": set(),
                                       "sunset": None, "successor": None, "deprecation_doc": None})
        found["calls"] += 1
        found["gone"] += s.get("status") == 410
        found["endpoints"].add(f"{s.get('method', 'GET')} {template(url.path)}")
        found["sources"].add(s["source"].split(" ")[0])
        if s.get("trace_id"):
            found["traces"].add(s["trace_id"])
        found["sunset"] = s.get("sunset") or found["sunset"]
        rels = links(s.get("link"))
        found["successor"] = rels.get("successor-version") or found["successor"]
        found["deprecation_doc"] = rels.get("deprecation") or found["deprecation_doc"]
    for found in apis.values():
        old = VERSION.search(next(iter(found["endpoints"])).split(" ", 1)[1])
        new = VERSION.search(urlsplit(found["successor"] or "").path)
        found["old"], found["new"] = (old.group(1) if old else "current"), (new.group(1) if new else "next")
        for key in ("endpoints", "sources", "traces"):
            found[key] = sorted(found[key])
    return apis


@neatlogs.span(kind="AGENT", name="drift_detection")
def watch(source: str, python: str, stamp: str, log: Path | None, use_neatlogs: bool,
          after_env: dict[str, str], since_minutes: int = 1440) -> list[RunState]:
    """Collect signals, report the drift, and return one migration job per API."""
    signals = []
    if use_neatlogs:
        signals += signals_from_neatlogs(since_minutes)
    if log and Path(log).exists():
        signals += signals_from_log(log)
    apis = findings(signals)
    tracing.annotate(signals=len(signals), drifting_apis=len(apis))
    jobs = []
    for name, found in sorted(apis.items()):
        print(f"[upshift] {name} {found['old']} is deprecated: {found['calls']} calls "
              f"({found['gone']} already 410), sunset {found['sunset'] or 'not announced'}, "
              f"successor {found['successor'] or 'not linked'}; seen in {', '.join(found['sources'])}")
        for endpoint in found["endpoints"]:
            print(f"[upshift]   {endpoint}")
        if not found["successor"]:
            print(f"[upshift]   no successor version linked; skipping {name}")
            continue
        jobs.append(RunState(run_id=f"{stamp}-api-{name}", kind="api", source=source, package=name,
                             wanted=found["new"], python=python, old=found["old"], new=found["new"],
                             evidence=found, after_env=after_env))
    if not signals:
        print("[upshift] no API calls found in the signals searched")
    for job in jobs:
        job.save()
    return jobs
