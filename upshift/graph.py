"""Thin wrappers over the `entire graph` CLI: the agent's code-intelligence layer."""

import os
import shutil
import subprocess

import neatlogs

TIMEOUT = 600


def available() -> bool:
    return shutil.which("entire") is not None


def _run(args: list[str], repo: str, stdin: str | None = None, timeout: int = TIMEOUT,
         env: dict | None = None) -> str:
    if not available():
        return "entire CLI not installed; graph tools unavailable."
    proc = subprocess.run(
        ["entire", "graph", *args, "--repo", repo],
        input=stdin, capture_output=True, text=True, timeout=timeout, env={**os.environ, **(env or {})},
    )
    out = proc.stdout.strip()
    if proc.returncode != 0:
        out = f"{out}\n[exit {proc.returncode}] {proc.stderr.strip()[-2000:]}".strip()
    return out


@neatlogs.span(kind="TOOL", tool_name="graph_search")
def search(repo: str, query: str, top_k: int = 8) -> str:
    return _run(["search", "--query", query, "--top-k", str(top_k), "--format", "agent",
                 "--profile", "full", "--max-context-bytes", "16000"], repo)


@neatlogs.span(kind="TOOL", tool_name="graph_impact")
def impact(repo: str, symbol: str) -> str:
    return _run(["impact", "--symbol", symbol], repo)


@neatlogs.span(kind="TOOL", tool_name="graph_explain")
def explain(repo: str, failure_text: str) -> str:
    return _run(["explain"], repo, stdin=failure_text)


@neatlogs.span(kind="GUARDRAIL", tool_name="graph_verify")
def verify(repo: str, test_cmd: str, record_baseline: str | None = None,
           pre_edit_baseline: str | None = None, env: dict | None = None) -> str:
    args = ["verify", "--test", test_cmd, "--max-bytes", "4000"]
    if record_baseline:
        args += ["--record-baseline", record_baseline]
    if pre_edit_baseline:
        args += ["--pre-edit-baseline", pre_edit_baseline]
    return _run(args, repo, timeout=1800, env=env)
