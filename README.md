# upshift

Dependabot opens the PR. Upshift makes it pass.

Upshift is an agent that fixes the code a version change breaks. Give it a Python repo and a package to
upgrade, or let it find the vulnerable ones. It researches the upgrade itself (changelog, release notes, migration guide, and an API diff
made by installing both versions), writes a playbook where every rule cites its source, bumps the
dependency, fixes the code one root cause at a time, and proves nothing new broke against the test
baseline it recorded before touching anything.

Built for #neatHack with neatlogs, Entire and cfo.ai. Bring your own model key.

## Status

Package bumps and vulnerability scans work end to end. Python version bumps and third-party API drift
come next.

## Run it

Needs Python 3.11+, [uv](https://docs.astral.sh/uv/) and the [Entire CLI](https://docs.entire.io/overview) with
its graph plugin (`entire plugin install graph`). Without Entire the agent still runs, but its graph tools and the
`entire graph verify` check report that they are unavailable.

```bash
uv sync
python3 scripts/check_secrets.py --install   # pre-commit hook that blocks committed keys
cp .env.example .env                         # fill in your provider and key (git-ignored)

# no model calls: clone, baseline, research, bump, and show what breaks
uv run upshift bump --repo demo/shop_app --package pydantic --to 2 --dry-run

# the full agent
uv run upshift bump --repo demo/shop_app --package pydantic --to 2
uv run upshift scan --repo demo/shop_app                     # find known vulnerabilities and fix each one
uv run upshift resume <run-id>     # continue a crashed or killed run
```

Runs live in `.upshift/runs/<run-id>/`: the working copy, its venv, `state.json`, the research sources and
the report. Playbooks are cached in `playbooks/` and reused by the next repo on the same upgrade.

## Models: bring your own key

Set `UPSHIFT_PROVIDER` and `UPSHIFT_MODEL` plus that provider's key in `.env` (read automatically,
never committed) or in your shell.

| `UPSHIFT_PROVIDER` | Needs | Notes |
|---|---|---|
| `anthropic` | `ANTHROPIC_API_KEY` | `UPSHIFT_MODEL` defaults to `claude-opus-5-5`. Claude's thinking blocks are replayed unchanged and old tool results are trimmed by the API's context editing, so the conversation stays append-only |
| `azure-openai` | `AZURE_OPENAI_API_KEY`, `AZURE_OPENAI_ENDPOINT` | `UPSHIFT_MODEL` is the deployment name |
| `openai` | `OPENAI_API_KEY` | Set `OPENAI_BASE_URL` for any OpenAI-compatible service (OpenRouter, Groq, Together, a local server) |
| `bedrock` | `UPSHIFT_AWS_PROFILE` | An explicit profile is required, so the default AWS account is never billed by accident |

The agent uses one message format internally and each provider translates to it, so adding a
provider means one adapter in `upshift/llm.py`. Every client is wrapped with `neatlogs.wrap`, so
model calls are traced with tokens and latency whichever provider you pick.

## How a job runs

| Phase | What happens | Where the tools come in |
|---|---|---|
| setup | Clone, create a venv, install, resolve the target version from PyPI | |
| baseline | Run the tests before changing anything | `entire graph verify --record-baseline` |
| bump | Rewrite the pins, install the new version, see what breaks | |
| research | Docs, changelog, GitHub release notes, API diff of both installed versions → cited playbook. Skipped for a security fix that breaks nothing | neatlogs `RETRIEVER`/`AGENT` spans |
| fix | One root cause per round. Progress is committed, regressions are reverted | `entire graph search / impact / explain` tools, one neatlogs span per round |
| verify | No new failures against the baseline, no old APIs left from the playbook; for security fixes, pip-audit again | `entire graph verify --pre-edit-baseline` |
| report | `UPSHIFT_REPORT.md` with changes, verification and sources | |

Every job is one neatlogs trace.

`upshift scan` installs the repo once, audits the exact installed versions with
[pip-audit](https://github.com/pypa/pip-audit) (OSV advisories, transitive dependencies included) and starts
one job per vulnerable package, like one PR each. The target is the smallest release that fixes every advisory,
preferring the current major line. A fix that breaks nothing is just a pin change with no model calls; one that
breaks the code goes through research and the fix loop like any other upgrade.

## Tests

```bash
uv run pytest
```

The fix-loop tests drive the real loop (repo, venv, pytest, git) with a scripted model in place of a real one.
