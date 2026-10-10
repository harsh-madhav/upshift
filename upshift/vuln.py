"""Vulnerability trigger: audit what the project installs with pip-audit (OSV) and pick the smallest fix."""

import json

import neatlogs
from packaging.utils import canonicalize_name
from packaging.version import InvalidVersion, Version

from . import tracing
from .state import RUNS, RunState
from .workspace import Workspace, sh, tail

def scan(source: str, python: str, stamp: str) -> list[RunState]:
    """Install the repo once, audit it, and return one vuln job per package that has a fixed release."""
    scan_id = f"{stamp}-scan"
    ws = Workspace(RUNS / scan_id, python=python)
    with neatlogs.identify(session_id=scan_id):
        with neatlogs.trace("upshift.scan", kind="WORKFLOW") as root:
            root.set_attribute("upshift.repo", source)
            ws.clone(source, "upshift/scan")
            ws.install()
            findings = audit(ws)
            jobs = []
            for package, found in sorted(findings.items()):
                found["target"] = fix_version(found["version"], found["advisories"])
                ids = ", ".join(label(a, info["aliases"]) for a, info in found["advisories"].items())
                if not found["target"]:
                    print(f"[upshift] {package} {found['version']}: no fixed release yet for {ids}")
                    continue
                print(f"[upshift] {package} {found['version']} -> {found['target']}: {ids}")
                jobs.append(RunState(run_id=f"{stamp}-vuln-{package}", kind="vuln", source=source,
                                     package=package, wanted=found["target"], python=python,
                                     advisories=found["advisories"]))
            (ws.root / "findings.json").write_text(json.dumps(findings, indent=2))
            root.set_attribute("upshift.jobs", len(jobs))
    for job in jobs:
        job.save()
    return jobs


@neatlogs.span(kind="TOOL", tool_name="pip_audit")
def audit(ws: Workspace) -> dict[str, dict]:
    """Vulnerable packages in the workspace venv, including transitive ones.

    Audits the exact installed versions (`uv pip freeze`), so no second dependency resolution can disagree
    with what the tests run against.
    """
    frozen = ws.root / "frozen.txt"
    frozen.write_text(ws.freeze())
    out = sh(["uvx", "pip-audit", "-r", str(frozen), "--no-deps", "--disable-pip",
              "--format", "json", "--progress-spinner", "off"])
    if out.returncode not in (0, 1):  # 1 means vulnerabilities were found
        raise RuntimeError(f"pip-audit failed:\n{tail(out.stderr, 3000)}")
    findings = parse_audit(json.loads(out.stdout))
    tracing.annotate(vulnerable_packages=len(findings),
                     advisories=sum(len(f["advisories"]) for f in findings.values()))
    return findings


def parse_audit(report: dict) -> dict[str, dict]:
    """{package: {"version", "advisories": {id: {"aliases", "fix_versions"}}}} for vulnerable packages only."""
    findings = {}
    for dep in report.get("dependencies", []):
        advisories = {v["id"]: {"aliases": v.get("aliases", []), "fix_versions": v.get("fix_versions", [])}
                      for v in dep.get("vulns", [])}
        if advisories:
            findings[canonicalize_name(dep["name"])] = {"version": dep["version"], "advisories": advisories}
    return findings


def fix_version(current: str, advisories: dict) -> str | None:
    """Smallest version that fixes every advisory, or None if one of them has no fix.

    For each advisory take its lowest fix above the current version (so a fix on the current major line
    wins over a newer major), then the highest of those.
    """
    now = Version(current)
    needed = []
    for advisory in advisories.values():
        fixes = []
        for raw in advisory["fix_versions"]:
            try:
                fixes.append(Version(raw))
            except InvalidVersion:
                continue
        fixes = [v for v in fixes if v > now]
        if not fixes:
            return None
        needed.append(min(fixes))
    return str(max(needed)) if needed else None


def label(advisory_id: str, aliases: list[str]) -> str:
    """Prefer the CVE id people search for."""
    cve = next((a for a in aliases if a.startswith("CVE-")), None)
    return f"{cve} ({advisory_id})" if cve else advisory_id
