"""upshift bump --repo PATH_OR_URL --package NAME [--to VERSION]
upshift scan --repo PATH_OR_URL
upshift python --repo PATH_OR_URL --to 3.12
upshift resume RUN_ID"""

import argparse
import os
import sys
import time
from pathlib import Path

from . import pipeline, pyversion, tracing, vuln
from .llm import LLMConfigError
from .state import RunState


def load_env_file(path: Path = Path(".env")) -> None:
    """Read KEY=VALUE lines from a git-ignored .env. Variables already set in the shell win."""
    if not path.exists():
        return
    for line in path.read_text().splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, value = line.split("=", 1)
        value = value.strip().strip("\"'")
        if value:  # an empty OPENAI_BASE_URL= would otherwise override the SDK default
            os.environ.setdefault(key.removeprefix("export ").strip(), value)


def main(argv: list[str] | None = None) -> int:
    load_env_file()
    parser = argparse.ArgumentParser(prog="upshift", description=__doc__.split("\n")[0])
    sub = parser.add_subparsers(dest="command", required=True)

    bump = sub.add_parser("bump", help="upgrade a package and fix what breaks")
    bump.add_argument("--repo", required=True, help="local path or git URL")
    bump.add_argument("--package", required=True)
    bump.add_argument("--to", help="target version or prefix (default: latest stable)")
    bump.add_argument("--python", help="Python for the project's venv (default: its .python-version, else 3.12)")
    bump.add_argument("--fresh-research", action="store_true",
                      help="ignore a cached playbook and research the upgrade again")
    bump.add_argument("--dry-run", action="store_true",
                      help="no model calls: set up, baseline, gather research, bump, show what breaks")

    scan = sub.add_parser("scan", help="audit dependencies for known vulnerabilities and fix each one")
    scan.add_argument("--repo", required=True, help="local path or git URL")
    scan.add_argument("--python", help="Python for the project's venv (default: its .python-version, else 3.12)")
    scan.add_argument("--dry-run", action="store_true",
                      help="no model calls: fixes that break nothing still complete; others stop after research")

    py = sub.add_parser("python", help="move the project to a newer Python and fix what breaks")
    py.add_argument("--repo", required=True, help="local path or git URL")
    py.add_argument("--to", required=True, help="target Python, e.g. 3.12")
    py.add_argument("--from", dest="from_version", help="current Python (default: the repo's .python-version)")
    py.add_argument("--fresh-research", action="store_true",
                    help="ignore a cached playbook and research the upgrade again")
    py.add_argument("--dry-run", action="store_true",
                    help="no model calls: set up, baseline, move to the new Python, show what breaks")

    resume = sub.add_parser("resume", help="continue a run from its last completed phase")
    resume.add_argument("run_id")
    resume.add_argument("--dry-run", action="store_true")

    args = parser.parse_args(argv)
    stamp = time.strftime("%Y%m%d-%H%M%S")
    jobs = []
    if args.command in ("bump", "scan"):
        args.python = args.python or pyversion.detect(args.repo) or "3.12"
    if args.command == "python":
        state = RunState(run_id=f"{stamp}-python-{args.to}", kind="python", source=args.repo, package="python",
                         wanted=args.to, python=args.from_version or "", fresh_research=args.fresh_research)
        state.save()
        jobs, tags = [state], ["python", args.to]
    elif args.command == "bump":
        state = RunState(run_id=f"{stamp}-{args.package.lower()}", kind="bump", source=args.repo,
                         package=args.package, wanted=args.to, python=args.python,
                         fresh_research=args.fresh_research)
        state.save()
        jobs, tags = [state], [state.kind, state.package]
    elif args.command == "resume":
        state = RunState.load(args.run_id)
        jobs, tags = [state], [state.kind, state.package]
    else:
        tags = ["vuln"]

    tracing.init(tags)
    try:
        if args.command == "scan":
            jobs = vuln.scan(args.repo, args.python, stamp)
            if not jobs:
                print("[upshift] nothing to fix")
        results = [run_job(state, args.dry_run) for state in jobs]
    except LLMConfigError as exc:
        print(f"[upshift] {exc}", file=sys.stderr)
        return 2
    finally:
        tracing.shutdown()
    return 0 if all(results) else 1


def run_job(state: RunState, dry_run: bool) -> bool:
    print(f"[upshift] run {state.run_id} → {state.dir}")
    state = pipeline.run(state, dry_run=dry_run)
    if state.result:
        print(f"[upshift] {'OK' if state.result['ok'] else 'INCOMPLETE'}: "
              f"{state.result['passed']} passing, {len(state.result['new_failures'])} new failures. "
              f"Report: {state.dir / pipeline.REPORT}")
    return bool(state.result.get("ok")) or dry_run


if __name__ == "__main__":
    sys.exit(main())
