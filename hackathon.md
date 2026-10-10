# neatHack build log: Upshift

## Project
An agent that fixes the code a dependency, Python or third-party API version change breaks.
Three triggers (vulnerability scan, developer-requested bump, API drift seen in traces), one engine:
research → cited playbook → bump → fix loop → verify → report.

## Team
Levelup

## Stack
- Agent: custom Python loop; bring-your-own-key model layer (OpenAI-compatible, Anthropic and Bedrock)
- neatlogs: tracing every job, phase, tool and model call; later also detection of API drift
- Entire: checkpoints for this repo; entire-graph as the agent's search/impact/explain/verify tools
- cfo.ai: business plan (pricing, costs, revenue, runway), not started

## Build log

### 2026-10-10 (Day 1)
Done:
- Installed Entire 0.11.4 and enabled it for Claude Code, plus the entire-graph plugin (v0.4.0).
  Python is a semantic language in entire-graph.
- Built the package-bump pipeline: setup, baseline, research, bump, fix loop, verify, report, with
  state saved between phases and `upshift resume`.
- Research: PyPI links, doc pages, GitHub release notes in range, and an API diff made by installing
  both versions in throwaway venvs and comparing public symbols and signatures.
- Fix loop: one root cause per round. Progress is committed, regressions are reverted, tool errors go
  back to the model.
- Demo target `demo/shop_app` (Pydantic v1, 16 tests).
- Model layer made provider-agnostic: openai (any OpenAI-compatible base URL) and
  bedrock, chosen with UPSHIFT_PROVIDER. Keys come only from the environment or a git-ignored .env.
- Pre-commit hook (`scripts/check_secrets.py`) blocks staged .env files, the values of secrets in .env,
  and common key formats. Tested by staging a fake key: the commit was blocked.

Verified:
- `upshift bump --repo demo/shop_app --package pydantic --to 2 --dry-run`: baseline 16 passing; target
  resolved to 2.14.0; after the bump the suite fails at import (`constr(regex=...)`).
- `uv run pytest`: 20 passed, including the fix loop and cleanup rounds driven by a scripted model,
  fetch dedupe, and the OpenAI message translation.
- First real run (v0), gpt-5.4, run `20261010-144415-pydantic`: pydantic 1.10.13 → 2.14.0 on
  demo/shop_app. 1 fix round, 0 → 16 passing, 0 new failures vs baseline; only `shop/models.py` changed,
  no test files touched. `entire graph verify`: tests behave exactly as before the upgrade.
  Cost: 187,897 input / 5,134 output tokens. Playbook: 35 cited rules from the official migration guide.
- v0 weaknesses seen in that run (targets for v1, to be measured in neatlogs):
  - It stops as soon as tests pass, leaving 6 deprecated v1 calls in `shop/orders.py`
    (`parse_obj`, `parse_raw`, `.json()`, `.dict()`, `.copy()`, `from_orm`).
  - ~188K input tokens for a single fix round: each step resends the whole conversation.

Problems found and fixed:
- `entire graph verify` only recognises pytest output with `-rA` or `-v`; with `-q` it fell back to
  exit-code-only. The verify command now uses `-rA`.
- The API diff first reported 1,631 removed Pydantic symbols because v2 exports names lazily. The probe
  now reads `__all__`, and the diff lists public names before internals.
- The GitHub repo was taken from PyPI's Funding link (`github.com/sponsors/...`). Source/repository links
  are now preferred.
- Same-size edits within one second were masked by stale `.pyc` files. Tests now run with `python -B`.
- The report showed 0 tokens (counts were saved after the report was written). Now synced after every phase.
- The model wrote some playbook detect regexes that flag valid code (e.g. `==\s*\{`). A rule's regex is now
  kept only if it matches its own old example and not its new one; test files are skipped in the
  leftover scan.

### v0 → v1, driven by the neatlogs trace
Read the v0 trace (`96ce4f0c44ab5957bf92f47efeeb50ed`) through the neatlogs MCP server
(`search_traces`, `get_trace_context`). What it showed:
- Playbook research made 2 model calls, but the first one fetched the same migration page 11 times
  with different `#anchor`s, each returning the same ~30 KB. That duplicate text went into the second call.
- The fix loop's 10 model calls each resent the whole conversation, including pytest's warnings section.
- neatlogs raised a "Slow LLM Response" detection on the 38.5 s playbook call.

Changes in v1:
- `Fetcher`: one fetch per page per conversation; `#anchor` variants are refused with a short note.
- Fix loop: tool results older than the last two steps are shortened, and pytest's warnings summary is
  stripped from round prompts and `run_tests` output.
- Cleanup round after tests pass: migrate deprecated calls the playbook still detects; commit only if
  tests still pass and the count went down, otherwise revert.
- Per-round input tokens recorded on each `fix.N` / `cleanup.N` span. `--fresh-research` so a comparison
  run doesn't reuse the cached playbook.

Same repo, same model (gpt-5.4), both with fresh research:

| | v0 `96ce4f0c…` | v1 `d5b7c0ef…` |
|---|---|---|
| Cost | $0.396 | $0.327 (−17%) |
| Total tokens | 193,031 | 142,395 (−26%) |
| Model calls / tool calls | 12 / 32 | 8 / 23 |
| Duplicate page fetches | 11 | 0 |
| neatlogs detections | Slow LLM Response | none |
| Deprecated calls left | 6 | 0 |
| Tests | 16/16, 0 new failures | 16/16, 0 new failures |

Caveats: one run each, and model output varies between runs. In v1 the model migrated `shop/orders.py`
during the first fix round, so the cleanup round had nothing to do; the cleanup path is covered by
tests with a scripted model, not yet by a real run.

### Vulnerability trigger
`upshift scan --repo R`: install once, `uv pip freeze` → `pip-audit` (OSV, transitive deps included), then one job
per vulnerable package at the smallest release that fixes all its advisories. Phases now run bump before research,
so a security fix that breaks nothing skips research and the model entirely. Verify re-runs pip-audit and checks
the job's advisories are gone. A vulnerable transitive dependency gets a `pkg>=fixed` line in requirements.

Demo: `shop_app` now renders receipts with Jinja2 2.11.3 (`Markup`, `escape`, `contextfilter`,
`environmentfilter`, all removed in 3.1) and pins requests 2.31.0. 21 tests.

Real run (gpt-5.4), `upshift scan --repo demo/shop_app`, both jobs OK:

| Job | Trace | Result | Cost |
|---|---|---|---|
| jinja2 2.11.3 → 3.1.6, 4 CVEs | `269d09bce0c27e7d5ba14bb3a851f176` | 1 fix round, 0 → 21 passing, 4/4 advisories fixed, only `shop/receipts.py` changed | $0.112, 44,084 tokens, 33 s |
| requests 2.31.0 → 2.33.0, 3 CVEs | `f57c12d8965f3f5be257a52ad033db3b` | pin change only, 21/21, 3/3 advisories fixed | $0, no model calls, 2.4 s |

Problem found and fixed: the dry run showed the API diff for jinja2 as "0 public symbols before". The old version
was installed next to markupsafe 3, failed to import, and the probe reported an empty API, which would have told
the model nothing was removed. The old side is now installed with the project's own pre-upgrade versions
(`env-before.txt`, recorded at baseline), and an empty side is reported as "API diff unavailable". After the fix
the diff lists `jinja2.Markup`, `escape`, `contextfilter` and `environmentfilter` as removed.

### Anthropic provider
`UPSHIFT_PROVIDER=anthropic` calls Claude through the Anthropic SDK (default `claude-opus-5-5`, effort `high`),
streamed and traced by neatlogs. Claude Opus 5.5 always thinks, and Claude rejects a replayed thinking block once an
earlier turn was edited, so this provider keeps the history append-only: Claude's own blocks are sent back unchanged,
the fix loop's client-side trimming of old tool results is switched off, and the API's context editing
(`clear_tool_uses_20250919`) trims instead. Also on: prompt caching of the resent history, and server-side refusal
fallbacks. Checked against a local stub of the streaming Messages API (request shape, beta headers, a thinking block
replayed with its signature, token counts); not yet a real run, as there is no Anthropic key on this machine.

### neatHack model proxy
`UPSHIFT_PROVIDER=litellm` uses the [LiteLLM proxy](https://neatlogs.com/hackathon/models) the hackathon gives teams
(Claude and Amazon Nova models, OpenAI-compatible), with `LITELLM_API_KEY`. Same `upshift scan --repo demo/shop_app`
with `claude-sonnet-4.6`, both jobs OK:

| Job | Trace | Result | Tokens |
|---|---|---|---|
| jinja2 2.11.3 → 3.1.6, 4 CVEs | `b63ea8ca45b5a0ed830e1e04cdd9b5a8` | 1 fix round, 0 → 21 passing, 4/4 advisories fixed, only `shop/receipts.py` changed | 21,680 in 13 s (≈ $0.07 at Sonnet 4.6 list prices) |
| requests 2.31.0 → 2.33.0, 3 CVEs | | pin change only, 3/3 advisories fixed | 0, no model calls |

The playbook came from the cache (researched in the earlier run), so Claude only ran the fix loop here. neatlogs shows
the tokens but $0 cost for this trace: it has no price for the proxy's model name `claude-sonnet-4.6`.

Next:
- Repeat v0/v1 on more repos (real OSS projects) to separate the improvement from run-to-run variance.
- Python version bump, then API drift.

## Demo
Not recorded yet.

## Known limitations and next steps
- Python projects with pytest only.
- Vulnerability scan uses pip-audit (OSV). Dependabot alerts are not read yet.
- Requirement rewriting handles requirements*.txt and PEP 621 pyproject dependencies, not Poetry or lockfiles.

## Submission
Not submitted.
