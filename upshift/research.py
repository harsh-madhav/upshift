"""Research a package upgrade: versions from PyPI, docs and release notes, and a real API diff."""

import html
import json
import re
from pathlib import Path

import httpx
import neatlogs
from packaging.version import InvalidVersion, Version

from . import tracing
from .workspace import sh, tail

PYPI = "https://pypi.org/pypi/{}/json"
PROBE = Path(__file__).with_name("apidiff_probe.py")
SOURCE_CAP = 30000
USEFUL_LINK = re.compile(r"change|release|history|news|migrat|upgrad|doc", re.I)
WHATSNEW = "https://docs.python.org/3/whatsnew/{}.html"


def http_get(url: str, timeout: float = 20) -> httpx.Response:
    return httpx.get(url, timeout=timeout, follow_redirects=True,
                     headers={"User-Agent": "upshift/0.1 (dependency upgrade agent)"})


def pypi_info(package: str) -> dict:
    response = http_get(PYPI.format(package))
    response.raise_for_status()
    return response.json()


def resolve_target(info: dict, wanted: str | None) -> str:
    """`None` → latest stable; `2` or `2.7` → newest stable release with that prefix; else exact."""
    releases = []
    for raw, files in info["releases"].items():
        try:
            version = Version(raw)
        except InvalidVersion:
            continue
        if not version.is_prerelease and files and not all(f.get("yanked") for f in files):
            releases.append(version)
    releases.sort()
    if not wanted:
        return str(releases[-1])
    prefix = [int(p) for p in wanted.split(".")]
    if len(prefix) >= 3 and Version(wanted) in releases:
        return wanted
    matches = [v for v in releases if list(v.release[: len(prefix)]) == prefix]
    if not matches:
        raise ValueError(f"no stable release matches {wanted!r}")
    return str(matches[-1])


def html_to_text(raw: str) -> str:
    raw = re.sub(r"(?is)<(script|style|nav|header|footer)[^>]*>.*?</\1>", " ", raw)
    raw = re.sub(r"(?i)<br\s*/?>|</(p|div|li|h\d|tr|pre)>", "\n", raw)
    raw = re.sub(r"<[^>]+>", " ", raw)
    text = html.unescape(raw)
    text = re.sub(r"[ \t]+", " ", text)
    return re.sub(r"\n\s*\n+", "\n\n", text).strip()


@neatlogs.span(kind="TOOL", tool_name="fetch_url")
def fetch_text(url: str, cap: int = SOURCE_CAP) -> str:
    try:
        response = http_get(url)
    except httpx.HTTPError as exc:
        return f"[fetch failed: {exc}]"
    if response.status_code >= 400:
        return f"[HTTP {response.status_code} for {url}]"
    body = response.text
    if "html" in response.headers.get("content-type", ""):
        body = html_to_text(body)
    return body[:cap] + ("\n...[truncated]" if len(body) > cap else "")


class Fetcher:
    """Fetch each page at most once per conversation.

    URLs that differ only by #anchor are the same page: in the v0 trace the playbook writer fetched the
    Pydantic migration guide 11 times this way and paid for ~330 KB of duplicate text.
    """

    def __init__(self, cap: int = SOURCE_CAP, already_read: list[str] = ()):
        self.cap = cap
        self.seen = {self.page(u) for u in already_read}
        self.duplicates = 0

    @staticmethod
    def page(url: str) -> str:
        return url.split("#", 1)[0].rstrip("/")

    def __call__(self, url: str) -> str:
        page = self.page(url)
        if page in self.seen:
            self.duplicates += 1
            tracing.annotate(duplicate_fetch=page)
            return (f"[Already fetched {page} earlier in this conversation. A #section anchor does not change "
                    "the page, so use the text you already have.]")
        self.seen.add(page)
        return fetch_text(page, cap=self.cap)


def github_repo(urls: dict) -> str | None:
    preferred = sorted(urls.items(), key=lambda kv: not re.search(r"source|repo|code|home", kv[0], re.I))
    for _, url in preferred:
        m = re.match(r"https?://github\.com/([^/]+/[^/#?]+)", url or "")
        if m and m.group(1).split("/")[0] != "sponsors":
            return m.group(1).removesuffix(".git")
    return None


@neatlogs.span(kind="RETRIEVER", tool_name="release_notes")
def github_release_notes(repo: str, old: str, new: str, cap: int = SOURCE_CAP) -> str:
    """Release notes for versions in (old, new], newest first."""
    try:
        response = http_get(f"https://api.github.com/repos/{repo}/releases?per_page=100")
        response.raise_for_status()
    except httpx.HTTPError as exc:
        return f"[release notes unavailable: {exc}]"
    lo, hi = Version(old), Version(new)
    parts = []
    for release in response.json():
        tag = re.sub(r"^[^\d]*", "", release.get("tag_name", ""))
        try:
            version = Version(tag)
        except InvalidVersion:
            continue
        if lo < version <= hi and not version.is_prerelease:
            parts.append(f"## {release['tag_name']}\n{release.get('body') or ''}")
    text = "\n\n".join(parts)
    return text[:cap] + ("\n...[truncated]" if len(text) > cap else "") if text else "[no GitHub releases in range]"


@neatlogs.span(kind="TOOL", tool_name="api_diff")
def api_diff(package: str, old: str, new: str, python: str, scratch: Path,
             old_env: Path | None = None) -> dict:
    """Install both versions side by side and diff their public symbols and signatures.

    `old_env` pins the old side to the project's pre-upgrade versions: an old release installed next to
    today's dependencies may not even import (jinja2 2.11 with markupsafe 3).
    """
    surfaces = {}
    for label, version in (("old", old), ("new", new)):
        venv = scratch / f"apidiff-{label}"
        if not venv.exists():
            sh(["uv", "venv", "-q", "--python", python, str(venv)])
        py = str(venv / "bin" / "python")
        install = ["uv", "pip", "install", "-q", "--python", py, f"{package}=={version}"]
        out = sh([*install, "-c", str(old_env)]) if label == "old" and old_env else None
        if out is None or out.returncode != 0:
            out = sh(install)
        if out.returncode != 0:
            return {"error": f"could not install {package}=={version}: {tail(out.stderr, 1500)}"}
        out = sh([py, "-I", str(PROBE), package], timeout=300)
        if out.returncode != 0:
            return {"error": f"probe failed for {version}: {tail(out.stderr, 1500)}"}
        surfaces[label] = json.loads(out.stdout or "{}")
        if not surfaces[label]:
            return {"error": f"{package}=={version} installed but exposed no symbols (it probably failed to import)"}
    return compare(surfaces["old"], surfaces["new"])


@neatlogs.span(kind="TOOL", tool_name="stdlib_diff")
def stdlib_diff(old: str, new: str, scratch: Path) -> dict:
    """The standard library's public API under both interpreters. A module removed whole is one entry."""
    surfaces = {}
    for label, version in (("old", old), ("new", new)):
        venv = scratch / f"stdlib-{label}"
        if not venv.exists():
            out = sh(["uv", "venv", "-q", "--python", version, str(venv)])
            if out.returncode != 0:
                return {"error": f"could not create a Python {version} venv: {tail(out.stderr, 1500)}"}
        out = sh([str(venv / "bin" / "python"), "-I", str(PROBE), "--stdlib"], timeout=600)
        if out.returncode != 0:
            return {"error": f"stdlib probe failed on Python {version}: {tail(out.stderr, 1500)}"}
        surfaces[label] = json.loads(out.stdout or "{}")
    diff = compare(surfaces["old"], surfaces["new"])
    after_tops = {name.split(".")[0] for name in surfaces["new"]}
    gone = {name.split(".")[0] for name in diff["removed"]} - after_tops
    diff["removed"] = sorted(f"{top} (whole module removed)" for top in gone) + [
        name for name in diff["removed"] if name.split(".")[0] not in gone]
    return diff


def compare(before: dict, after: dict) -> dict:
    return {
        "removed": sorted(set(before) - set(after)),
        "added": sorted(set(after) - set(before)),
        "changed": {k: [before[k], after[k]] for k in sorted(set(before) & set(after)) if before[k] != after[k]},
        "old_count": len(before),
        "new_count": len(after),
    }


def _public_first(name: str) -> tuple:
    """Top-level names (what users import) before deep internals."""
    return (name.count("."), name)


def format_api_diff(diff: dict, per_section: int = 250) -> str:
    if "error" in diff:
        return f"[API diff unavailable: {diff['error']}]"
    removed = sorted(diff["removed"], key=_public_first)
    changed = sorted(diff["changed"].items(), key=lambda kv: _public_first(kv[0]))
    lines = [f"Public symbols: {diff['old_count']} before, {diff['new_count']} after. "
             "Most public (shallowest) names first.",
             f"REMOVED ({len(removed)}):"]
    lines += [f"- {name}" for name in removed[:per_section]]
    if len(removed) > per_section:
        lines.append(f"... and {len(removed) - per_section} more deep internals")
    lines.append(f"SIGNATURE CHANGED ({len(changed)}):")
    lines += [f"~ {name}: {a[:300]}  ->  {b[:300]}" for name, (a, b) in changed[:per_section]]
    if len(changed) > per_section:
        lines.append(f"... and {len(changed) - per_section} more")
    lines.append(f"ADDED: {len(diff['added'])} symbols (not listed)")
    return "\n".join(lines)


def html_section(html: str, section_id: str) -> str:
    """The HTML of one Sphinx <section id=...>, nested sections included."""
    start = html.find(f'<section id="{section_id}">')
    if start < 0:
        return ""
    depth = 0
    for tag in re.finditer(r"<section\b|</section>", html[start:]):
        depth += 1 if tag.group(0) == "<section" else -1
        if depth == 0:
            return html[start:start + tag.end()]
    return html[start:]


def python_versions(old: str, new: str) -> list[str]:
    """Minor versions whose What's New applies to an upgrade, e.g. 3.10 -> 3.12 gives 3.11 and 3.12."""
    low, high = Version(old), Version(new)
    return [f"{high.major}.{minor}" for minor in range(low.minor + 1, high.minor + 1)]


@neatlogs.span(kind="RETRIEVER", tool_name="gather_python_sources")
def gather_python(old: str, new: str, scratch: Path) -> dict:
    """Research for a Python version upgrade: the removal and porting sections of each What's New page, and a
    diff of the standard library under both interpreters."""
    links, docs = {}, {}
    for version in python_versions(old, new):
        url = WHATSNEW.format(version)
        links[f"What's New in Python {version}"] = url
        try:
            html = http_get(url).text
        except httpx.HTTPError as exc:
            docs[f"Python {version}"] = f"[fetch failed: {exc}]"
            continue
        for section, cap in (("removed", 15000), (f"porting-to-python-{version.replace('.', '-')}", 10000),
                             ("deprecated", 6000)):
            text = html_to_text(html_section(html, section))
            if text:
                docs[f"Python {version}: {section}"] = text[:cap] + ("\n...[truncated]" if len(text) > cap else "")
    return {
        "package": "python",
        "old": old,
        "new": new,
        "links": links,
        "docs": docs,
        "release_notes": "[not applicable: see the What's New sections above]",
        "api_diff": format_api_diff(stdlib_diff(old, new, scratch)),
    }


def candidate_links(info: dict) -> dict[str, str]:
    urls = info["info"].get("project_urls") or {}
    if info["info"].get("home_page"):
        urls.setdefault("Homepage", info["info"]["home_page"])
    return {k: v for k, v in urls.items() if v}


@neatlogs.span(kind="RETRIEVER", tool_name="gather_sources")
def gather(package: str, old: str, new: str, python: str, scratch: Path, old_env: Path | None = None) -> dict:
    """Everything the playbook writer starts from. It can fetch more links itself."""
    info = pypi_info(package)
    links = candidate_links(info)
    repo = github_repo(links)
    docs = {name: fetch_text(url, cap=12000) for name, url in links.items() if USEFUL_LINK.search(name + url)}
    diff = api_diff(package, old, new, python, scratch, old_env)
    return {
        "package": package,
        "old": old,
        "new": new,
        "links": links,
        "docs": docs,
        "release_notes": github_release_notes(repo, old, new) if repo else "[no GitHub repo linked on PyPI]",
        "api_diff": format_api_diff(diff),
    }
