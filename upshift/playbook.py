"""Turn research into a cited migration playbook. Cached so the next repo skips research."""

import json
import re
from pathlib import Path

import neatlogs

from . import tracing
from .llm import text_of, tool_result, tool_spec, tool_uses
from .research import Fetcher

CACHE = Path(__file__).resolve().parent.parent / "playbooks"
MAX_TURNS = 10

SYSTEM = """You research Python dependency upgrades and write migration playbooks.

You get the package, the old and new versions, the project's doc and changelog pages, GitHub release
notes between the versions, and an API diff computed by installing both versions and comparing their
public symbols. The API diff is ground truth for what was removed or changed. For an upgrade of Python
itself the package is "python": the docs are the What's New sections and the API diff compares the standard
library under both interpreters. For a third-party HTTP API the package is the API's name: the docs are the
provider's migration guide and the API diff lists the old and new OpenAPI operations; write rules for the
client code (endpoints, request fields, headers, response shape), with detect regexes that match old calls.

Read what you were given. If you need the official migration guide or a specific doc page, use
fetch_url (follow links you saw, don't guess random URLs). Each page is returned whole, so fetch a
page once; #section anchors return the same page. Then call submit_playbook once.

Each rule must describe one concrete breaking change a codebase could hit, as:
- old: the old usage, written as code
- new: what to write instead, as code
- detect: a Python regex that matches the old usage and never matches correct new-version code, or ""
  for behavioural changes that can't be found textually
- why: one sentence
- source: the URL you read it in, or "api-diff"
Only include rules supported by a source. Prefer 10-40 high-value rules over exhaustive lists."""

TOOLS = [
    tool_spec("fetch_url", "Fetch a web page as text (docs, migration guide, changelog).",
              {"url": {"type": "string"}}, ["url"]),
    tool_spec("submit_playbook", "Submit the finished playbook.", {
        "summary": {"type": "string", "description": "2-4 sentences on the shape of this upgrade."},
        "rules": {"type": "array", "items": {"type": "object", "properties": {
            "old": {"type": "string"}, "new": {"type": "string"}, "detect": {"type": "string"},
            "why": {"type": "string"}, "source": {"type": "string"}},
            "required": ["old", "new", "detect", "why", "source"]}},
    }, ["summary", "rules"]),
]


def cache_path(package: str, old: str, new: str) -> Path:
    return CACHE / f"{package.lower()}-{old}-{new}.json"


def load_cached(package: str, old: str, new: str) -> dict | None:
    path = cache_path(package, old, new)
    return load(path) if path.exists() else None


def load(path: str | Path) -> dict:
    return sanitize(json.loads(Path(path).read_text()))


def sanitize(playbook: dict) -> dict:
    """Keep a rule's detect regex only if it matches the rule's old example and not its new one.

    Models sometimes write patterns like `==\\s*\\{` that flag valid code everywhere.
    """
    for rule in playbook["rules"]:
        pattern = rule.get("detect") or ""
        try:
            ok = bool(pattern) and re.search(pattern, rule["old"]) and not re.search(pattern, rule["new"])
        except re.error:
            ok = False
        rule["detect"] = pattern if ok else ""
    return playbook


def render_sources(sources: dict) -> str:
    docs = "\n\n".join(f"### {name}\n{text}" for name, text in sources["docs"].items())
    links = "\n".join(f"- {k}: {v}" for k, v in sources["links"].items())
    return (f"Package: {sources['package']}  {sources['old']} -> {sources['new']}\n\n"
            f"## Project links\n{links}\n\n## Doc and changelog pages\n{docs or '(none)'}\n\n"
            f"## GitHub release notes in range\n{sources['release_notes']}\n\n"
            f"## API diff (installed both versions)\n{sources['api_diff']}")


@neatlogs.span(kind="AGENT", name="playbook")
def build(llm, sources: dict) -> dict:
    messages = [{"role": "user", "content": [{"text": render_sources(sources)}]}]
    fetch = Fetcher(already_read=[sources["links"][name] for name in sources["docs"] if name in sources["links"]])
    for _ in range(MAX_TURNS):
        response = llm.converse(SYSTEM, messages, TOOLS, max_tokens=16000)
        message = response["output"]["message"]
        messages.append(message)
        uses = tool_uses(message)
        if not uses:
            messages.append({"role": "user", "content": [{"text": "Call submit_playbook with your rules."}]})
            continue
        results = []
        for use in uses:
            if "_invalid_json" in use["input"]:
                results.append(tool_result(use["toolUseId"], "Arguments were not valid JSON; call again.", error=True))
                continue
            if use["name"] == "submit_playbook":
                playbook = sanitize({"package": sources["package"], "old": sources["old"],
                                     "new": sources["new"], **use["input"]})
                tracing.annotate(playbook_rules=len(playbook["rules"]), urls_fetched=len(fetch.seen),
                                 duplicate_fetches_refused=fetch.duplicates)
                return playbook
            if use["name"] == "fetch_url":
                results.append(tool_result(use["toolUseId"], fetch(use["input"]["url"])))
            else:
                results.append(tool_result(use["toolUseId"], f"unknown tool {use['name']}", error=True))
        messages.append({"role": "user", "content": results})
    raise RuntimeError(f"no playbook after {MAX_TURNS} turns; last reply: {text_of(messages[-1])[:500]}")


def save(playbook: dict) -> Path:
    CACHE.mkdir(exist_ok=True)
    path = cache_path(playbook["package"], playbook["old"], playbook["new"])
    path.write_text(json.dumps(playbook, indent=2))
    return path


def render(playbook: dict) -> str:
    lines = [playbook.get("summary", "")]
    for i, rule in enumerate(playbook["rules"], 1):
        lines.append(f"R{i}. {rule['old']}  =>  {rule['new']}   ({rule['why']}; source: {rule['source']})")
    return "\n".join(lines)
