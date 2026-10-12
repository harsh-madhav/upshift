# Upshift business plan

Built in [cfo.ai](https://cfo.ai/s/vY7Ur0OCp02UyJmF) from the per-job costs measured in neatlogs traces
([inputs and assumptions](cfo-inputs.md)). Full 24-month model: [upshift-business-plan.xlsx](upshift-business-plan.xlsx).

## Pricing
- **Free:** open-source repos, and security fixes that need no code change.
- **Team:** $49 per repo per month, including 5 breaking migrations.
- **Business:** $399 per month for up to 15 repos (≈ $26.60 per repo), plus API-drift monitoring.

## Unit economics
- Measured model cost per job: $0 (pin-only security fix) to ≈ $0.88 (Python 3.10 → 3.12 on Opus). The plan assumes
  5× that for real repositories: ≈ $2.00 model + $0.10 compute per breaking migration.
- Cost per Team repo ≈ $2.20 a month → **gross margin 96%**. 7 Team repos cover the fixed costs.
- Fixed costs: $300 a month (hosting and job runners $150, observability $50, other tools $100).

## 24-month forecast

| | Base | Upside |
|---|---|---|
| Growth | 10 paid repos by month 3, +15%/month, 3% churn | 25 by month 3, +25%/month, 3% churn |
| Paid repos, month 24 | 108 | 1,627 |
| Monthly revenue, month 24 | $5,294 | $79,743 |
| ARR, month 24 | $63,527 | $956,916 |
| Operating profit, month 24 | $4,756 / month | $75,863 / month |
| Founder funding needed | $144 (month 1) | $0 |
| Break-even | Nov 2026 (month 2) | Oct 2026 (month 1) |

## Runway
Bootstrapped from $0 with one founder and no hires. The cumulative cash low point is **−$144** (the first month's
fixed costs not yet covered by revenue). Operating and cash break-even come in November 2026, and the founder's
money is recovered in December 2026.

## Sensitivity (base scenario)

| Stress | Break-even | Founder funding needed |
|---|---|---|
| Breaking migration costs $5 instead of $2 | Dec 2026 | $162 |
| Team price $29 instead of $49 | Jan 2027 | $364 |
| Both | Mar 2027 | $459 |

The business stays fundable by one person under every stress tested: model cost is a small share of the price,
and researched playbooks are reused across customers, so cost per migration falls as usage grows.
