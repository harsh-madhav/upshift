# Changelog

All notable changes to Upshift are listed here. The format follows
[Keep a Changelog](https://keepachangelog.com/en/1.1.0/), and the project uses
[Semantic Versioning](https://semver.org/).

## [Unreleased]

The first release will be 0.1.0.

### Added
- `upshift bump`: upgrade a package, research the change into a cited playbook, fix the code one root cause at a
  time, and verify against the pre-upgrade test baseline.
- `upshift scan`: audit dependencies with pip-audit (OSV) and fix each vulnerable package. `--package NAME` fixes
  one finding while still reporting all of them.
- `upshift python`: move a project to a newer Python and fix what breaks.
- `upshift watch`: find third-party API calls the provider is retiring (`Deprecation` / `Sunset` headers or
  410 Gone) in neatlogs traces or the app's call log, and migrate them with the fix loop.
- `upshift resume` continues a stopped run; `--dry-run` makes no model calls.
- Installs and audits follow `poetry.lock` and `uv.lock`, so tests run on the versions the project ships. A fix
  re-locks the package, and the report lists every locked version that moved.
- Providers: Anthropic, OpenAI, Azure OpenAI, LiteLLM (including the neatHack model proxy) and Bedrock, all
  traced with neatlogs.
- A landing page in `docs/`, plus the license, contributing guide, code of conduct and security policy.

### Changed
- The default Python for a project's venv is `.python-version`, else the lowest version `requires-python`
  allows, else 3.12.
- A folder inside a git repo is copied with only its git-tracked files, so local `.env` files stay behind.
- `scan` stops and lists the projects it finds when a repo's Python projects live in subfolders, instead of
  reporting a false all-clear.
