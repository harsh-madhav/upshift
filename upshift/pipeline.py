"""One job end to end: setup → baseline → bump → research → fix → verify → report.

Each phase is a neatlogs span under the job's WORKFLOW trace, and state is saved between phases.
"""

import re
import shutil
from pathlib import Path

import neatlogs
from packaging.utils import canonicalize_name
from packaging.version import Version

from . import graph, playbook, pyversion, research, tracing, vuln
from .agent import FixLoop
from .llm import LLMError, make_llm
from .state import PHASES as PHASE_ORDER, RunState
from .workspace import Workspace

REPORT = "UPSHIFT_REPORT.md"


class Stop(Exception):
    """Ends the job early with a message (already at target, dry run, ...)."""


def run(state: RunState, dry_run: bool = False) -> RunState:
    ws = Workspace(state.dir, python=state.python)
    llm = None if dry_run else make_llm()
    with neatlogs.identify(session_id=state.run_id):
        with neatlogs.trace(f"upshift.{state.kind}", kind="WORKFLOW") as root:
            root.set_attribute("upshift.package", state.package)
            root.set_attribute("upshift.repo", state.source)
            root.set_attribute("upshift.run_id", state.run_id)
            if state.advisories:
                root.set_attribute("upshift.advisories", ", ".join(state.advisories))
            try:
                for phase in PHASE_ORDER[:-1]:
                    if state.reached(phase):
                        continue
                    with neatlogs.trace(phase, kind="AGENT"):
                        PHASES[phase](state, ws, llm, dry_run)
                    if llm:
                        state.input_tokens, state.output_tokens = llm.input_tokens, llm.output_tokens
                    state.advance(next_phase(phase))
                    print(f"[upshift] {phase} done")
            except Stop as stop:
                state.notes.append(str(stop))
                print(f"[upshift] stopped: {stop}")
            except LLMError as exc:
                # the phase didn't finish, so `resume` redoes it once the provider is fixed or back
                tracing.annotate(failure_class=f"llm:{exc.code}", recovery="stop_resumable")
                state.notes.append(f"{state.phase}: model call failed ({exc.code})")
                print(f"[upshift] {state.phase}: model call failed: {exc}\n"
                      f"[upshift] fix the provider settings, then: upshift resume {state.run_id}")
            finally:
                root.set_attribute("upshift.phase_reached", state.phase)
                state.save()
    return state


def next_phase(phase: str) -> str:
    return PHASE_ORDER[PHASE_ORDER.index(phase) + 1]


def setup(state: RunState, ws: Workspace, llm, dry_run: bool) -> None:
    if state.kind == "python":
        return setup_python(state, ws)
    info = research.pypi_info(state.package)
    state.new = research.resolve_target(info, state.wanted)
    if not ws.repo.exists():
        ws.clone(state.source, f"upshift/{state.kind}-{state.package}-{state.new}")
        state.base_commit = ws.git("rev-parse", "HEAD").strip()
    ws.install()
    state.old = ws.installed_version(state.package) or ""
    tracing.annotate(old=state.old, new=state.new)
    if not state.old:
        raise Stop(f"{state.package} is not installed by this project")
    if Version(state.old) >= Version(state.new):
        raise Stop(f"{state.package} is already at {state.old} (target {state.new})")


def setup_python(state: RunState, ws: Workspace) -> None:
    if not ws.repo.exists():
        ws.clone(state.source, f"upshift/python-{state.wanted}")
        state.base_commit = ws.git("rev-parse", "HEAD").strip()
    if not state.python:
        state.python = ws.python = pyversion.detect(ws.repo) or ""
        if not state.python:
            raise Stop("can't tell which Python the repo uses (no .python-version); pass --from")
    ws.install()
    state.old = ws.python_version()
    state.new = state.wanted
    tracing.annotate(old=state.old, new=state.new)
    if Version(state.old) >= Version(state.new):
        raise Stop(f"the project already runs on Python {state.old} (target {state.new})")


def baseline(state: RunState, ws: Workspace, llm, dry_run: bool) -> None:
    (state.dir / "env-before.txt").write_text(ws.freeze())  # the versions the old code ran with
    run = ws.run_tests()
    state.baseline_failing = sorted(run.failing)
    state.baseline_passed = run.passed
    graph.verify(str(ws.repo), ws.test_cmd, record_baseline=str(state.dir / "verify-baseline.json"))
    tracing.annotate(baseline_passed=run.passed, baseline_failing=len(run.failing))


def bump(state: RunState, ws: Workspace, llm, dry_run: bool) -> None:
    if state.kind == "python":
        move_python(state, ws)
    else:
        ws.set_version(state.package, state.new)
        if not ws.pin_files(state.package):
            state.notes.append(f"{state.package} is not declared in a requirements file or pyproject.toml; "
                               "it was upgraded in the environment only.")
    ws.git("add", "-A")
    ws.git("commit", "-q", "--allow-empty", "-m", f"upshift: bump {state.package} {state.old} -> {state.new}")
    run = ws.run_tests()
    state.after_bump_failing = sorted(run.failing - set(state.baseline_failing))
    (state.dir / "after-bump.txt").write_text(run.output)
    tracing.annotate(passed_after_bump=run.passed, new_failures_after_bump=len(state.after_bump_failing))


def move_python(state: RunState, ws: Workspace) -> None:
    """Point the repo's version pins at the new Python and rebuild the venv on it."""
    changed = pyversion.retarget(ws.repo, state.old, state.new)
    tracing.annotate(config_files_changed=", ".join(changed) or "none")
    if not changed:
        state.notes.append("No Python version pins found (.python-version, Dockerfile, CI, tox, pyproject).")
    state.python = ws.python = state.new
    shutil.rmtree(ws.venv, ignore_errors=True)
    try:
        ws.install()
    except RuntimeError as exc:
        # a pinned dependency with no release for the new Python: upgrading it is its own job
        blocked = sorted(set(re.findall(r"`([A-Za-z0-9][A-Za-z0-9._-]*==[^`\s]+)`", str(exc))))
        tracing.annotate(failure_class="install_failed_on_new_python", blocked=", ".join(blocked))
        raise Stop(f"the dependencies don't install on Python {state.new}"
                   + (f" ({', '.join(blocked)})" if blocked else "")
                   + ". Upgrade those first with `upshift bump --package NAME`, then run this again.") from exc


def do_research(state: RunState, ws: Workspace, llm, dry_run: bool) -> None:
    if state.kind == "vuln" and not state.after_bump_failing:
        # a security fix should be the smallest safe change: nothing broke, so no migration and no model calls
        tracing.annotate(playbook="not_needed")
        state.notes.append("The fixed version broke no tests, so no migration research or model calls were needed.")
        return
    cached = None if state.fresh_research else playbook.load_cached(state.package, state.old, state.new)
    if cached:
        state.playbook_path = str(playbook.cache_path(state.package, state.old, state.new))
        tracing.annotate(playbook_cache="hit")
    else:
        tracing.annotate(playbook_cache="miss")
        env_before = state.dir / "env-before.txt"
        if state.kind == "python":
            sources = research.gather_python(state.old, state.new, state.dir)
        else:
            sources = research.gather(state.package, state.old, state.new, state.python, state.dir,
                                      env_before if env_before.exists() else None)
        (state.dir / "sources.md").write_text(playbook.render_sources(sources))
        if not dry_run:
            state.playbook_path = str(playbook.save(playbook.build(llm, sources)))
    if dry_run:
        raise Stop(f"dry run: {len(state.after_bump_failing)} new failures after the bump "
                   f"(pytest output in {state.dir / 'after-bump.txt'}, research in {state.dir})")


def load_playbook(state: RunState) -> dict:
    return playbook.load(state.playbook_path) if state.playbook_path else {"summary": "", "rules": []}


def fix(state: RunState, ws: Workspace, llm, dry_run: bool) -> None:
    if not state.playbook_path:  # research was skipped because nothing broke
        return
    pb = playbook.load(state.playbook_path)
    loop = FixLoop(ws, llm, state, pb)
    final = loop.run(state.save)
    if not loop.new_failures(final):
        loop.cleanup(lambda: leftover_usages(ws, pb), state.save)


def verify(state: RunState, ws: Workspace, llm, dry_run: bool) -> None:
    run = ws.run_tests()
    new_failures = sorted(run.failing - set(state.baseline_failing))
    verdict = graph.verify(str(ws.repo), ws.test_cmd, pre_edit_baseline=str(state.dir / "verify-baseline.json"))
    leftovers = leftover_usages(ws, load_playbook(state))
    remaining = []
    if state.kind == "vuln":
        still = vuln.audit(ws).get(canonicalize_name(state.package), {}).get("advisories", {})
        remaining = [a for a in state.advisories if a in still]
    state.result = {
        "passed": run.passed,
        "baseline_passed": state.baseline_passed,
        "new_failures": new_failures,
        "leftover_old_apis": leftovers,
        "advisories_remaining": remaining,
        "entire_verdict": verdict,
        "ok": not new_failures and not remaining,
    }
    tracing.annotate(ok=state.result["ok"], new_failures=len(new_failures), leftovers=len(leftovers),
                     advisories_remaining=len(remaining))


def is_test_file(rel: Path) -> bool:
    return "tests" in rel.parts or "test" in rel.parts or rel.name.startswith("test_") or rel.name == "conftest.py"


def leftover_usages(ws: Workspace, pb: dict, limit: int = 50) -> list[str]:
    """Non-test source lines that still use an old API from the playbook."""
    hits = []
    patterns = []
    for rule in pb["rules"]:
        if not rule.get("detect"):
            continue
        try:
            patterns.append((re.compile(rule["detect"]), rule["why"]))
        except re.error:
            continue
    for path in sorted(ws.source_files()):
        rel = path.relative_to(ws.repo)
        if path.suffix != ".py" or is_test_file(rel):
            continue
        for i, line in enumerate(path.read_text(errors="ignore").splitlines(), 1):
            for regex, why in patterns:
                if regex.search(line):
                    hits.append(f"{rel}:{i} `{line.strip()}`: {why}")
                    break
                    if len(hits) >= limit:
                        return hits
    return hits


def report(state: RunState, ws: Workspace, llm, dry_run: bool) -> None:
    r = state.result
    pb = load_playbook(state)
    diffstat = ws.git("diff", "--stat", state.base_commit, "HEAD").strip().splitlines()
    tests = "no new test failures" if not r["new_failures"] else f"{len(r['new_failures'])} new test failures remain"
    lines = [
        f"# Upshift: {state.package} {state.old} → {state.new}"
        + (" (security fix)" if state.kind == "vuln" else ""),
        "",
        f"**Result:** {tests} · {r['passed']} passing (baseline {r['baseline_passed']})"
        + (f" · {len(state.advisories) - len(r['advisories_remaining'])}/{len(state.advisories)} advisories fixed"
           if state.advisories else ""),
        f"**Run:** `{state.run_id}` · {state.round} fix rounds · "
        f"{state.input_tokens:,} input / {state.output_tokens:,} output tokens",
        "",
        "## What changed",
        f"- {state.package} {state.old} → {state.new}",
        *[f"- {entry}" for entry in state.round_log],
        "",
        f"`{diffstat[-1].strip() if diffstat else 'no diff'}`",
        "",
        "## Verification (entire graph verify against the pre-upgrade baseline)",
        "```", r["entire_verdict"].strip(), "```",
    ]
    if state.advisories:
        lines += ["", "## Security advisories (pip-audit, OSV)",
                  *[f"- {vuln.label(a, info['aliases'])}: "
                    f"{'still present' if a in r['advisories_remaining'] else 'fixed'} "
                    f"(fixed in {', '.join(info['fix_versions']) or 'no release'})"
                    for a, info in state.advisories.items()]]
    if r["new_failures"]:
        lines += ["", "## Still failing", *[f"- `{t}`" for t in r["new_failures"]]]
    if r["leftover_old_apis"]:
        lines += ["", "## Old APIs still in use", *[f"- {h}" for h in r["leftover_old_apis"]]]
    if pb["rules"]:
        lines += ["", "## Playbook", pb.get("summary", ""), "",
                  *[f"- `{x['old']}` → `{x['new']}` — {x['why']} ([source]({x['source']}))"
                    if x["source"].startswith("http") else f"- `{x['old']}` → `{x['new']}` — {x['why']} (API diff)"
                    for x in pb["rules"]]]
    if state.notes:
        lines += ["", "## Notes", *[f"- {n}" for n in state.notes]]
    text = "\n".join(lines) + "\n"
    (ws.repo / REPORT).write_text(text)
    (state.dir / REPORT).write_text(text)
    ws.git("add", REPORT)
    ws.git("commit", "-q", "-m", f"upshift: report for {state.package} {state.new}")


PHASES = {"setup": setup, "baseline": baseline, "research": do_research, "bump": bump,
          "fix": fix, "verify": verify, "report": report}
