"""Vulnerability trigger: audit what the project installs with pip-audit (OSV) and pick the smallest fix."""

import json
from pathlib import Path

import neatlogs
from packaging.utils import canonicalize_name
from packaging.version import InvalidVersion, Version

from . import tracing
from .state import RUNS, RunState
from .workspace import SKIP_DIRS, Workspace, lock_pins, sh, tail

def scan(source: str, python: str, stamp: str, only: str | None = None) -> list[RunState]:
    """Install the repo once, audit it, and return one vuln job per package that has a fixed release."""
    scan_id = f"{stamp}-scan"
    ws = Workspace(RUNS / scan_id, python=python)
    with neatlogs.identify(session_id=scan_id):
        with neatlogs.trace("upshift.scan", kind="WORKFLOW") as root:
            root.set_attribute("upshift.repo", source)
            ws.clone(source, "upshift/scan")
            if not declares_dependencies(ws.repo):
                # a monorepo root installs nothing but pytest, and "nothing to fix" would be a false all-clear
                found = projects_below(ws.repo)
                raise SystemExit("[upshift] no Python dependencies are declared at the repo root, so there is "
                                 "nothing to audit. " + (f"Python projects found in: {', '.join(found)}. Upshift "
                                 "audits one project at a time; monorepo subfolders aren't supported yet."
                                 if found else "No Python project was found."))
            ws.install()
            findings = audit(ws)
            jobs = []
            lockfile, _ = lock_pins(ws.repo)
            for package, found in sorted(findings.items(), key=lambda kv: (not kv[1]["installed"], kv[0])):
                found["target"] = fix_version(found["version"], found["advisories"])
                ids = ", ".join(label(a, info["aliases"]) for a, info in found["advisories"].items())
                if not found["target"]:
                    print(f"[upshift] {package} {found['version']}: no fixed release yet for {ids}")
                    continue
                if not found["installed"]:
                    print(f"[upshift] {package} {found['version']} -> {found['target']}: {ids} "
                          f"(pinned in {lockfile} for optional extras the tests don't install; update the lock)")
                    continue
                print(f"[upshift] {package} {found['version']} -> {found['target']}: {ids}")
                if only and canonicalize_name(only) != package:
                    continue
                jobs.append(RunState(run_id=f"{stamp}-vuln-{package}", kind="vuln", source=source,
                                     package=package, wanted=found["target"], python=python,
                                     advisories=found["advisories"]))
            (ws.root / "findings.json").write_text(json.dumps(findings, indent=2))
            root.set_attribute("upshift.jobs", len(jobs))
    for job in jobs:
        job.save()
    return jobs


def declares_dependencies(repo: Path) -> bool:
    return any((repo / name).is_file() for name in ("pyproject.toml", "setup.py", "setup.cfg")) or any(
        repo.glob("requirements*.txt"))


def projects_below(repo: Path, depth: int = 3) -> list[str]:
    found = set()
    for path in repo.rglob("*"):
        rel = path.relative_to(repo)
        if len(rel.parts) > depth or any(part in SKIP_DIRS or part.startswith(".") for part in rel.parts):
            continue
        if path.is_file() and (path.name in ("pyproject.toml", "setup.py") or path.match("requirements*.txt")):
            found.add(str(rel.parent))
    return sorted(found)


@neatlogs.span(kind="TOOL", tool_name="pip_audit")
def audit(ws: Workspace) -> dict[str, dict]:
    """Vulnerable packages: everything installed in the workspace venv, plus everything the lockfile pins.

    The installed versions (`uv pip freeze`) are what the tests run against. The lockfile adds what the default
    install leaves out (optional extras): on a real project, auditing only the venv missed 36 vulnerable locked packages.
    Each finding says whether it is installed; only installed ones can be fixed and verified by the tests.
    """
    installed = {canonicalize_name(line.split("==")[0]): line.split("==")[1]
                 for line in ws.freeze().splitlines() if "==" in line}
    lockfile, locked = lock_pins(ws.repo)
    versions = {name: [version] for name, version in installed.items()}
    for name, pinned in locked.items():
        versions.setdefault(name, []).extend(v for v in pinned if v not in versions.get(name, []))
    findings: dict[str, dict] = {}
    # pip-audit takes one version per name, so audit in layers: installed (or first locked) versions, then the rest
    for layer in range(max((len(v) for v in versions.values()), default=0)):
        pins = ws.root / f"audit-{layer}.txt"
        pins.write_text("".join(f"{n}=={v[layer]}\n" for n, v in sorted(versions.items()) if len(v) > layer))
        out = sh(["uvx", "pip-audit", "-r", str(pins), "--no-deps", "--disable-pip",
                  "--format", "json", "--progress-spinner", "off"])
        if out.returncode not in (0, 1):  # 1 means vulnerabilities were found
            raise RuntimeError(f"pip-audit failed:\n{tail(out.stderr, 3000)}")
        for name, found in parse_audit(json.loads(out.stdout)).items():
            found["installed"] = installed.get(name) == found["version"]
            found["source"] = "installed" if found["installed"] else lockfile
            if name not in findings or found["installed"]:
                findings[name] = found
    tracing.annotate(vulnerable_packages=len(findings), lockfile=lockfile or "none",
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
