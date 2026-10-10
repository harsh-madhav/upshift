"""The fix loop: one root cause per round, tests after every round, commit progress, revert regressions."""

import re

import neatlogs

from . import graph, tracing
from .llm import LLMError, tool_result, tool_spec, tool_uses
from .playbook import render as render_playbook
from .research import Fetcher
from .state import RunState
from .workspace import TestRun, Workspace, tail

MAX_STEPS_PER_ROUND = 30
MAX_STALLS = 2
MAX_CLEANUP_ROUNDS = 2
KEEP_RECENT_RESULTS = 2   # tool-result messages kept whole; older ones are shortened
ELIDE_OVER = 1500         # chars

CLEANUP_PROMPT = """Cleanup round {n}. Tests pass again ({passed} passing), but this code still uses APIs that
{package} {new} deprecated or renamed. They work today and break in a later release:
{leftovers}

Migrate each one to its replacement from the playbook. Keep behaviour identical, including arguments
such as by_alias, include, exclude and update. Don't touch tests. Run the tests, then call finish_round."""

SYSTEM = """You are upgrading {subject} from {old} to {new} in the repository at the
working directory. The new version is already installed and pinned. Your job is to change the code so the
test suite passes again. Tests that were already failing before the upgrade are not your concern.

Migration playbook (researched from the official docs, release notes and an API diff):
{playbook}

How to work in each round:
1. Read the failing output. Pick ONE root cause: the one behind the most failures.
2. Find every place in the codebase it occurs (search_code, graph_search, graph_impact), not just the
   line in the traceback.
3. Fix them with minimal edits that keep behaviour the same. Follow the playbook; if something isn't
   covered, use fetch_docs on the official docs.
4. Run the most relevant tests with run_tests, then call finish_round with a one-line summary.

Rules: don't edit dependency pins or lockfiles. Don't weaken, skip or delete tests. Don't silence
deprecation warnings instead of migrating. Paths are relative to the repo root."""

TOOLS = [
    tool_spec("read_file", "Read a file with line numbers. Optional 1-based start/end lines.",
              {"path": {"type": "string"}, "start": {"type": "integer"}, "end": {"type": "integer"}}, ["path"]),
    tool_spec("edit_file", "Replace one exact, unique occurrence of `old` with `new` in a file.",
              {"path": {"type": "string"}, "old": {"type": "string"}, "new": {"type": "string"}},
              ["path", "old", "new"]),
    tool_spec("search_code", "Regex search across the repo's source files. Returns path:line: text.",
              {"pattern": {"type": "string"}, "glob": {"type": "string", "description": "default *.py"}},
              ["pattern"]),
    tool_spec("run_tests", "Run pytest. `selector` is passed through, e.g. 'tests/test_models.py -k parse'.",
              {"selector": {"type": "string"}}, []),
    tool_spec("graph_search", "entire-graph: find code for a plain-language description, ranked with context.",
              {"query": {"type": "string"}}, ["query"]),
    tool_spec("graph_impact", "entire-graph: callers, callees and related files for a symbol.",
              {"symbol": {"type": "string"}}, ["symbol"]),
    tool_spec("graph_explain", "entire-graph: resolve the symbols named in a failure (paste the error).",
              {"failure": {"type": "string"}}, ["failure"]),
    tool_spec("lookup_playbook", "Find playbook rules mentioning any of these words.",
              {"query": {"type": "string"}}, ["query"]),
    tool_spec("fetch_docs", "Fetch a documentation page as text.", {"url": {"type": "string"}}, ["url"]),
    tool_spec("finish_round", "End this round once the root cause is fixed everywhere.",
              {"summary": {"type": "string"}}, ["summary"]),
]


class FixLoop:
    def __init__(self, ws: Workspace, llm, state: RunState, playbook: dict, max_rounds: int = 12):
        self.ws, self.llm, self.state, self.playbook = ws, llm, state, playbook
        self.max_rounds = max_rounds
        self.fetch = Fetcher(cap=20000)
        self.baseline = set(state.baseline_failing)
        subject = "the project's Python version" if state.kind == "python" else f"the Python package {state.package}"
        self.system = SYSTEM.format(subject=subject, old=state.old, new=state.new, playbook=render_playbook(playbook))

    def new_failures(self, run: TestRun) -> set[str]:
        return run.failing - self.baseline

    def run(self, save) -> TestRun:
        current = self.ws.run_tests()
        stalls = 0
        while self.state.round < self.max_rounds:
            if not self.new_failures(current) and current.exit_code in (0, 1, 5):
                break
            self.state.round += 1
            current, outcome = self.one_round(self.state.round, current)
            save()
            stalls = stalls + 1 if outcome != "progress" else 0
            if stalls >= MAX_STALLS:
                self.state.notes.append(f"stopped after {stalls} rounds without progress")
                break
        return current

    def one_round(self, n: int, before: TestRun) -> tuple[TestRun, str]:
        with neatlogs.trace(f"fix.{n}", kind="AGENT") as span:
            tokens_before = self.llm.input_tokens
            failing = sorted(self.new_failures(before))
            prompt = (f"Round {n}. {len(failing)} tests fail that passed before the upgrade "
                      f"({before.passed} passing now).\nFailing: {', '.join(failing[:40])}\n\n"
                      f"Previous rounds:\n" + ("\n".join(self.state.round_log) or "(none)") +
                      f"\n\nPytest output:\n{failure_digest(before.output)}")
            try:
                summary = self.converse(prompt)
            except LLMError as exc:
                code = exc.code
                tracing.annotate(failure_class=f"llm:{code}", recovery="revert_and_retry_next_round")
                self.revert()
                self.state.round_log.append(f"round {n}: model call failed ({code}); changes reverted")
                return before, "error"

            after = self.ws.run_tests()
            new_before, new_after = self.new_failures(before), self.new_failures(after)
            if after.passed < before.passed and len(new_after) >= len(new_before):
                outcome = "regression"
                self.revert()
                tracing.annotate(failure_class="test_regression", recovery="revert_and_replan")
                self.state.round_log.append(
                    f"round {n}: REVERTED. '{summary}' made things worse "
                    f"({before.passed} -> {after.passed} passing). Try a different approach.")
                after = before
            elif self.ws.git("status", "--porcelain").strip() and (
                    after.passed > before.passed or len(new_after) < len(new_before)):
                outcome = "progress"
                self.ws.git("add", "-A")
                self.ws.git("commit", "-q", "-m", f"upshift: {summary}"[:200])
                self.state.round_log.append(
                    f"round {n}: {summary} ({before.passed} -> {after.passed} passing, "
                    f"{len(new_after)} new failures left)")
            else:
                outcome = "stall"
                self.state.round_log.append(f"round {n}: no measurable progress. '{summary}'")
            span.set_attribute("upshift.outcome", outcome)
            span.set_attribute("upshift.passed_before", before.passed)
            span.set_attribute("upshift.passed_after", after.passed)
            span.set_attribute("upshift.new_failures_left", len(self.new_failures(after)))
            span.set_attribute("upshift.input_tokens", self.llm.input_tokens - tokens_before)
            return after, outcome

    def revert(self) -> None:
        self.ws.git("checkout", "--", ".")
        self.ws.git("clean", "-fdq")

    def cleanup(self, find_leftovers, save) -> None:
        """Tests pass; now migrate deprecated calls that still work but will break later.

        v0 stopped as soon as tests passed and left six deprecated calls behind.
        """
        left = find_leftovers()
        for n in range(1, MAX_CLEANUP_ROUNDS + 1):
            if not left:
                return
            with neatlogs.trace(f"cleanup.{n}", kind="AGENT") as span:
                tokens_before = self.llm.input_tokens
                before = self.ws.run_tests()
                prompt = CLEANUP_PROMPT.format(n=n, passed=before.passed, package=self.state.package,
                                               new=self.state.new, leftovers="\n".join(f"- {l}" for l in left))
                try:
                    summary = self.converse(prompt)
                except LLMError as exc:
                    tracing.annotate(failure_class=f"llm:{exc.code}", recovery="revert_and_stop_cleanup")
                    self.revert()
                    return
                after, now_left = self.ws.run_tests(), find_leftovers()
                if self.new_failures(after):
                    outcome = "regression"
                    self.revert()
                    tracing.annotate(failure_class="test_regression", recovery="revert")
                    self.state.round_log.append(f"cleanup {n}: REVERTED. '{summary}' broke "
                                                f"{len(self.new_failures(after))} tests")
                    now_left = left
                elif len(now_left) < len(left) and self.ws.git("status", "--porcelain").strip():
                    outcome = "progress"
                    self.ws.git("add", "-A")
                    self.ws.git("commit", "-q", "-m", f"upshift: {summary}"[:200])
                    self.state.round_log.append(f"cleanup {n}: {summary} ({len(left)} -> {len(now_left)} "
                                                "deprecated usages, tests still passing)")
                else:
                    outcome = "stall"
                    self.revert()
                    self.state.round_log.append(f"cleanup {n}: no deprecated usages removed. '{summary}'")
                    now_left = left
                span.set_attribute("upshift.outcome", outcome)
                span.set_attribute("upshift.leftovers_before", len(left))
                span.set_attribute("upshift.leftovers_after", len(now_left))
                span.set_attribute("upshift.input_tokens", self.llm.input_tokens - tokens_before)
            left = now_left
            save()

    def converse(self, prompt: str) -> str:
        messages = [{"role": "user", "content": [{"text": prompt}]}]
        for _ in range(MAX_STEPS_PER_ROUND):
            if not getattr(self.llm, "trims_history_itself", False):
                compact(messages)
            response = self.llm.converse(self.system, messages, TOOLS)
            message = response["output"]["message"]
            messages.append(message)
            uses = tool_uses(message)
            if not uses:
                messages.append({"role": "user", "content": [{"text": "Continue with tools, or call finish_round."}]})
                continue
            results, finished = [], None
            for use in uses:
                if "_invalid_json" in use["input"]:
                    tracing.annotate(failure_class="tool_args_invalid_json", recovery="returned_to_model")
                    results.append(tool_result(use["toolUseId"], "Your tool arguments were not valid JSON. "
                                               "Call the tool again with valid JSON.", error=True))
                    continue
                if use["name"] == "finish_round":
                    finished = use["input"].get("summary", "").strip() or "round finished"
                    results.append(tool_result(use["toolUseId"], "ok"))
                    continue
                try:
                    results.append(tool_result(use["toolUseId"], self.call_tool(use["name"], use["input"])))
                except Exception as exc:  # tool errors go back to the model so it can recover
                    tracing.annotate(failure_class=f"tool:{use['name']}", recovery="returned_to_model")
                    results.append(tool_result(use["toolUseId"], f"{type(exc).__name__}: {exc}", error=True))
            messages.append({"role": "user", "content": results})
            if finished:
                return finished
        tracing.annotate(failure_class="step_budget", recovery="end_round")
        return "step budget for the round ran out"

    def call_tool(self, name: str, args: dict) -> str:
        ws = self.ws
        if name == "read_file":
            lines = ws.resolve(args["path"]).read_text().splitlines()
            start = max(args.get("start") or 1, 1)
            end = min(args.get("end") or start + 399, len(lines), start + 399)
            return "\n".join(f"{i:5}  {lines[i - 1]}" for i in range(start, end + 1)) or "(empty file)"
        if name == "edit_file":
            return edit_file(ws, args["path"], args["old"], args["new"])
        if name == "search_code":
            return search_code(ws, args["pattern"], args.get("glob") or "*.py")
        if name == "run_tests":
            run = ws.run_tests(args.get("selector", ""))
            return f"{run.summary}\n{failure_digest(run.output, 5000)}"
        if name == "graph_search":
            return tail(graph.search(str(ws.repo), args["query"]), 16000)
        if name == "graph_impact":
            return tail(graph.impact(str(ws.repo), args["symbol"]), 8000)
        if name == "graph_explain":
            return tail(graph.explain(str(ws.repo), args["failure"]), 8000)
        if name == "lookup_playbook":
            words = [w.lower() for w in re.findall(r"\w+", args["query"]) if len(w) > 2]
            hits = [r for r in self.playbook["rules"]
                    if any(w in (r["old"] + r["new"] + r["why"]).lower() for w in words)]
            return "\n".join(f"{r['old']} => {r['new']} ({r['why']}; {r['source']})" for r in hits[:20]) or "no matching rules"
        if name == "fetch_docs":
            return self.fetch(args["url"])
        raise ValueError(f"unknown tool {name}")


WARNINGS_SECTION = re.compile(r"^=+ warnings summary =+$.*?(?=^=+ |^\d+ \w+|\Z)", re.S | re.M)


def failure_digest(output: str, limit: int = 6000) -> str:
    """Pytest output without the warnings summary, which in v0 repeated on every round."""
    return tail(WARNINGS_SECTION.sub("[warnings summary omitted]\n", output), limit)


def compact(messages: list[dict]) -> None:
    """Shorten tool results older than the last few steps; each model call resends the whole history."""
    result_turns = [m for m in messages if m["role"] == "user" and any("toolResult" in b for b in m["content"])]
    for message in result_turns[:-KEEP_RECENT_RESULTS]:
        for block in message["content"]:
            for part in block.get("toolResult", {}).get("content", []):
                text = part.get("text", "")
                if len(text) > ELIDE_OVER:
                    part["text"] = (text[:500] + f"\n...[{len(text) - 500} chars from an earlier step elided; "
                                    "call the tool again if you need them]")


@neatlogs.span(kind="TOOL", tool_name="edit_file")
def edit_file(ws: Workspace, path: str, old: str, new: str) -> str:
    target = ws.resolve(path)
    text = target.read_text()
    count = text.count(old)
    if count != 1:
        raise ValueError(f"`old` matched {count} times in {path}; it must match exactly once. "
                         "Re-read the file and include more surrounding lines.")
    target.write_text(text.replace(old, new, 1))
    return f"edited {path}"


@neatlogs.span(kind="TOOL", tool_name="search_code")
def search_code(ws: Workspace, pattern: str, glob: str = "*.py", limit: int = 80) -> str:
    regex = re.compile(pattern)
    hits = []
    for path in sorted(ws.source_files()):
        if not path.match(glob):
            continue
        try:
            lines = path.read_text().splitlines()
        except (UnicodeDecodeError, OSError):
            continue
        for i, line in enumerate(lines, 1):
            if regex.search(line):
                hits.append(f"{path.relative_to(ws.repo)}:{i}: {line.strip()[:200]}")
                if len(hits) >= limit:
                    return "\n".join(hits) + f"\n...[stopped at {limit} matches]"
    return "\n".join(hits) or "no matches"
