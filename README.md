# Upshift

Dependabot opens the PR. Upshift makes it pass.

Upshift is an agent that fixes the code a version change breaks. Point it at a Python repo and tell it what
to upgrade, or let it find the vulnerable packages itself. It reads the upgrade (changelog, release notes,
migration guide, and an API diff made by installing both versions), writes a playbook where every rule cites
its source, bumps the dependency, fixes the code one root cause at a time, and proves nothing new broke
against the test results it recorded before touching anything.

Built for #neatHack with neatlogs, Entire and cfo.ai. Bring your own model key.

**What you can ask it to do**

| Command | What it does | Example result on the demo app |
|---|---|---|
| `upshift bump` | Upgrade one package and fix the code it breaks | pydantic 1 → 2: 16/16 tests passing |
| `upshift scan` | Find packages with known vulnerabilities and fix each one | jinja2: 4 CVEs fixed, 21 tests passing |
| `upshift python` | Move the project to a newer Python and fix what breaks | Python 3.10 → 3.12: 26 tests passing |
| `upshift watch` | Find third-party API calls that are being retired and migrate them | acme v1 → v2: 30/30 tests passing after the sunset |

Your repo is never edited in place: every run works on its own copy under `.upshift/runs/`.

## Quick start

About five minutes on the bundled demo app (`demo/shop_app`, a small shop deliberately pinned to old versions).

### 1. Install the tools

You need:

- **Python 3.11 or newer**: check with `python3 --version`.
- **git**: check with `git --version`.
- **uv**, the Python package manager Upshift uses: install it with `curl -LsSf https://astral.sh/uv/install.sh | sh`
  (or `brew install uv`), then check with `uv --version`. See the [uv docs](https://docs.astral.sh/uv/) for Windows.
- *Optional:* the [Entire CLI](https://docs.entire.io/overview) with its graph plugin (`entire plugin install graph`).
  It gives the agent code-graph tools. Without it Upshift still runs and simply reports those tools as unavailable.

### 2. Get the code

```bash
git clone https://github.com/harsh-madhav/upshift.git
cd upshift
uv sync
```

`uv sync` installs Upshift and its dependencies into a local `.venv`. You don't need to activate it: prefix
commands with `uv run`.

### 3. Try it without an API key

```bash
uv run upshift bump --repo demo/shop_app --package pydantic --to 2 --dry-run
```

`--dry-run` makes no model calls, so no key is needed. Upshift copies the demo app, runs its tests to record a
baseline, gathers the upgrade research, bumps pydantic and shows which tests now fail. That is the problem the
full agent fixes.

### 4. Add your model key

```bash
cp .env.example .env
```

Open `.env` and fill in two things: which provider to use and its key. For example, with Anthropic:

```bash
UPSHIFT_PROVIDER=anthropic
ANTHROPIC_API_KEY=sk-ant-...
```

`.env` is git-ignored and read automatically. Other providers are listed under [Models](#models-bring-your-own-key).

### 5. Run the full agent

```bash
uv run upshift bump --repo demo/shop_app --package pydantic --to 2
```

It prints one line per phase and finishes with something like:

```
[upshift] OK: 16 passing, 0 new failures. Report: .../UPSHIFT_REPORT.md
```

Open that `UPSHIFT_REPORT.md` to see what changed, how it was verified and which sources each fix came from.
The fixed code is in `.upshift/runs/<run-id>/repo/`, with one git commit per step.

Then try the other commands on the demo:

```bash
uv run upshift scan --repo demo/shop_app                # find known vulnerabilities and fix each one
uv run upshift python --repo demo/shop_app --to 3.12    # move to Python 3.12 and fix what breaks
```

The API drift demo for `watch` needs a small fake API server running; see [The API drift demo](#the-api-drift-demo).

## Use it on your own project

`--repo` takes a local path or a git URL. Your project needs a test suite that runs with pytest, because the
tests are how Upshift knows the upgrade worked.

```bash
uv run upshift bump   --repo ~/code/my-app --package fastapi           # latest stable fastapi
uv run upshift bump   --repo https://github.com/you/my-app.git --package sqlalchemy --to 2
uv run upshift scan   --repo ~/code/my-app
uv run upshift python --repo ~/code/my-app --to 3.12
```

Start with `--dry-run` to see what breaks before spending any tokens.

### Command reference

| Command | Required | Useful options |
|---|---|---|
| `bump` | `--repo`, `--package` | `--to VERSION` (default: latest stable), `--python`, `--fresh-research`, `--dry-run` |
| `scan` | `--repo` | `--python`, `--dry-run` |
| `python` | `--repo`, `--to` | `--from` (default: the repo's `.python-version`), `--fresh-research`, `--dry-run` |
| `watch` | `--repo` | `--neatlogs`, `--logs FILE`, `--since MINUTES`, `--after-env KEY=VALUE`, `--fresh-research`, `--dry-run` |
| `resume` | a run id | `--dry-run` |

- `--python` picks the Python for the project's venv. The default is the repo's `.python-version`, else 3.12.
- `--fresh-research` ignores a cached playbook and researches the upgrade again.
- Run `uv run upshift <command> --help` for the full list.

## Models: bring your own key

Set `UPSHIFT_PROVIDER`, the provider's key and optionally `UPSHIFT_MODEL` in `.env` or in your shell.

| `UPSHIFT_PROVIDER` | Needs | Notes |
|---|---|---|
| `anthropic` | `ANTHROPIC_API_KEY` | `UPSHIFT_MODEL` defaults to `claude-opus-5-5` |
| `openai` | `OPENAI_API_KEY` | Set `OPENAI_BASE_URL` for any OpenAI-compatible service (OpenRouter, Groq, Together, a local server) |
| `azure-openai` | `AZURE_OPENAI_API_KEY`, `AZURE_OPENAI_ENDPOINT` | `UPSHIFT_MODEL` is the deployment name |
| `litellm` | `LITELLM_API_KEY` | A LiteLLM proxy. Defaults to the [neatHack model proxy](https://neatlogs.com/hackathon/models) (Claude and Amazon Nova, e.g. `UPSHIFT_MODEL=claude-sonnet-4.6`); set `LITELLM_BASE_URL` for your own |
| `bedrock` | `UPSHIFT_AWS_PROFILE` | An explicit profile is required, so the default AWS account is never billed by accident |

Optional:

- `NEATLOGS_API_KEY` exports traces to neatlogs. Without it, spans are still created but not sent.
- `UPSHIFT_AGENT_VERSION` tags runs so their traces can be compared.

`.env.example` lists every variable with a comment.

## Where things go

- **Runs** live in `.upshift/runs/<run-id>/`. Each one holds the working copy (`repo/`) and its venv,
  `state.json`, the research sources and `UPSHIFT_REPORT.md`.
- **Playbooks** are cached in `playbooks/` and reused by the next repo doing the same upgrade.

## Troubleshooting

- **`Set UPSHIFT_PROVIDER to one of: ...`**: you ran without `--dry-run` and no provider is configured. Fill in
  `.env` (step 4 above).
- **A run crashed or you stopped it**: continue from the last finished phase with
  `uv run upshift resume <run-id>`. The run id is printed at the start, and is the folder name under
  `.upshift/runs/`.
- **`entire CLI not installed; graph tools unavailable.`**: the run still works. Install the Entire CLI for
  better code navigation (see step 1).
- **`<package> is not installed by this project`**: `bump` only upgrades packages the project already depends
  on. Check the spelling against your requirements or pyproject.toml.
- **`upshift python` stops and names a package**: that dependency has no release for the new Python yet.
  Upgrade it first with `upshift bump`, then run `upshift python` again.

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

### `scan`
Installs the repo once and audits the exact installed versions with
[pip-audit](https://github.com/pypa/pip-audit) (OSV advisories, transitive dependencies included). It then starts
one job per vulnerable package, like one PR each. The target is the smallest release that fixes every advisory,
preferring the current major line. A fix that breaks nothing is just a pin change with no model calls. One that
breaks the code goes through research and the fix loop like any other upgrade.

### `python`
Moves the version pins: `.python-version`, the Dockerfile base image, GitHub Actions, tox, and the
`requires-python` / ruff / mypy targets in pyproject.toml. It then rebuilds the venv on the new Python and fixes
what breaks. Its research is the Removed, Porting and Deprecated sections of each What's New page in range, plus a
diff of the standard library under both interpreters.

### `watch`
Looks for third-party API calls the provider is retiring: responses with a `Deprecation` or `Sunset` header, or
410 Gone. It searches the app's neatlogs traces over the neatlogs MCP server and/or the app's own call log, groups
the calls by API, and follows the `Link: rel="successor-version"` header.

Research is the provider's migration guide plus the old and new OpenAPI specs side by side. Tests then run with
`--after-env`, an environment that makes the provider's sandbox behave as it will after the shutdown, so the fix
loop has real failures to fix.

### The API drift demo
`shop_app` charges orders through acme's v1 API. acme's sandbox (`demo/shop_app/tests/acme_sandbox.py`) says v1
shuts down on 31 Oct 2026 and v2 replaces it.

```bash
python3 demo/shop_app/tests/acme_sandbox.py --port 8765 --mode deprecating &   # "production" acme
ACME_BASE_URL=http://127.0.0.1:8765 python demo/shop_app/scripts/traffic.py   # needs neatlogs + requests
uv run upshift watch --repo demo/shop_app --neatlogs --logs logs/app.log --after-env ACME_SANDBOX_MODE=sunset
```

## Contributing

```bash
uv sync
python3 scripts/check_secrets.py --install   # pre-commit hook that blocks committed keys
uv run pytest
```

The fix-loop tests drive the real loop (repo, venv, pytest, git) with a scripted model in place of a real one.

The agent uses one message format internally and each provider translates to it, so adding a provider means one
adapter in `upshift/llm.py`. Every client is wrapped with `neatlogs.wrap`, so model calls are traced with tokens
and latency whichever provider you pick. With Anthropic, Claude's thinking blocks are replayed unchanged and old
tool results are trimmed by the API's context editing, so the conversation stays append-only.
