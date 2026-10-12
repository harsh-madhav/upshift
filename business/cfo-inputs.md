# Upshift: inputs for the cfo.ai business plan

Everything under "Measured" comes from neatlogs traces of real Upshift runs. Everything under "Assumptions" is a
starting point to change in cfo.ai. Lines marked **(you decide)** need the founders' own numbers.

## The product
Upshift is an agent that fixes the code a version change breaks. Three triggers, one engine:
- **Security fix:** finds known vulnerabilities (pip-audit / OSV), bumps to the smallest fixed release, and if
  that breaks the code, migrates it until the tests pass.
- **Upgrade on request:** a package (e.g. Pydantic 1 → 2) or Python itself (3.10 → 3.12).
- **API drift:** sees a third-party API's deprecation signals in production traces and migrates to the new
  version before the sunset date (working: demoed on acme v1 → v2, 30/30 passing).

Every change is proven against the repo's own tests, recorded before anything was touched.

**Why customers pay:** Dependabot and Renovate open the version-bump PR for free, but when the bump breaks the
build an engineer spends hours to days migrating. Snyk and GitHub's autofix cover security only. Upshift does the
migration itself. Buyers: engineering teams with Python services and a backlog of stuck upgrades or open
security alerts.

## Measured cost per job (model spend, from neatlogs traces)

| Job (demo app, 16–26 tests) | Model | Tokens | Model cost |
|---|---|---|---|
| Security fix that breaks nothing (requests 2.31 → 2.33) | none needed | 0 | $0.00 |
| Security fix that breaks code (Jinja2 2.11 → 3.1, research cached) | Claude Sonnet 4.6 | 21,680 | ≈ $0.07 |
| Security fix that breaks code (Jinja2 2.11 → 3.1, with research) | GPT-5.4 | 44,084 | $0.11 |
| Package migration (Pydantic 1 → 2), before optimisation (v0) | GPT-5.4 | 193,031 | $0.40 |
| Package migration (Pydantic 1 → 2), after optimisation (v1) | GPT-5.4 | 142,395 | $0.33 |
| Python 3.10 → 3.12 | Claude Sonnet 4.6 | 137,904 | ≈ $0.51 |
| Python 3.10 → 3.12 | Claude Opus 4.6 | 148,284 | ≈ $0.88 |

"≈" = tokens × list price (neatlogs shows no price for the hackathon proxy's model names); the rest are neatlogs'
own cost figures. The v0 → v1 change (found from the v0 trace) cut cost 17% for the same result.

## Assumptions to start from

**Cost per job on real repositories** (the demo app is small, so scale up):
- Security fix that breaks nothing: $0 model, ≈ $0.02 compute (a few CI minutes for install + tests).
- Breaking migration: 5× the demo → ≈ $2.00 model + $0.10 compute. Researched playbooks are cached and reused by
  every customer on the same upgrade, so this falls as usage grows.

**Usage per repository per month:** 4 security fixes (mostly pin-only), 1 breaking migration
→ ≈ $2.20 cost per repo per month.

**Fixed costs per month:** hosting and job runners $150, observability (neatlogs) $50, other tools $100.

**Pricing (proposal):**
- Free: open-source repos, and security fixes that need no code change.
- Team: $49 per repo per month, including 5 breaking migrations; extra migrations $15 each.
- Business: $399 per month for up to 15 repos, plus API-drift monitoring.

At $49 and ≈ $2.20 cost, gross margin per Team repo is ≈ 95%.

**Customers (repos on paid plans)**, two scenarios over 24 months:
- Base: 10 paid repos by month 3, +15% per month after.
- Upside: 25 paid repos by month 3, +25% per month after.
- Monthly churn: 3%.

**Team and runway**:
- Founders and monthly salaries.
- No hires in the 24 months.
- Starting cash: $0 (bootstrapped). Show the cumulative cash low point, i.e. how much the founder must fund
  before break-even.

## What to ask cfo.ai for
1. A 24-month model with base and upside scenarios: customers, revenue, inference + compute costs, gross margin,
   operating costs, burn and runway.
2. The break-even month in each scenario.
3. Sensitivity: what happens to margin if a breaking migration costs $5 instead of $2, or if price is $29.
4. A share link and an export, for the hackathon submission.
