---
type: Reference
title: "Throughline Prototype: Roadmap and Level of Effort"
description: "The prototype's 18-month delivery plan: 2 teams prove the platform in 6 months, then 3 teams take all 15 critical use cases live in parallel onboarding waves."
resource: /Users/sowmiyamohankumar/Documents/lineage-collector-v1/Data Lineage Impact Platform-10/Roadmap & LOE.dc.html
tags: [prototype, throughline, roadmap, staffing, effort]
timestamp: 2026-08-14T12:10:00Z
---

# Throughline Prototype: Roadmap and Level of Effort

The leadership-facing delivery plan: 15 critical use cases fully live in 18 months, each a complete
vertical slice with lineage capture, impact analysis, and change-safe PR gating. Note on packaging:
"Roadmap and LOE.html" in the same folder is a self-contained multi-page bundle of these decks —
packaging of the same content, not a distinct document.

Key claims and structure:

- Staffing is a deliberate two-step ramp: 2 teams for the first 6 months prove the platform, then
  3 teams for the next 12 months onboard use cases in parallel — investment grows only after the
  approach is proven. A team is 5 engineers; total capacity is computed as 2x5x6 + 3x5x12
  person-months.
- The plan reads in two chapters (prove, then scale), with a timeline showing one mission per team
  at a time, feature bars colored by owning team, and value checkpoints every two months so each
  phase ends on a concrete, fundable outcome.
- Use Case 01 is proven in Chapter 1; the remaining 14 onboard in waves whose velocity ramps — one
  lane (Team 3) while the platform is completed, two lanes once Team 2 joins at M13. One
  5-engineer lane has ~10 person-months per 2-month wave against a known per-slice cost grounded
  in the onboarding estimator, giving ~2 concurrent use cases per lane rising to ~3; one lane
  delivers 7 by M13, two lanes deliver the remaining 7 by M18.
- Sustainability is planned, not assumed: Team 1 runs a rolling capability-build lane from M11 so
  collector gaps found during onboarding do not stall the waves, and formally owns platform
  support and maintenance from M14 because "onboarded is not done."
- The level-of-effort table allocates the entire capacity — platform build plus 15 onboardings,
  hardening room, and buffer — and explicitly ties totals back to the staffing numbers. Critical
  path steps are separated from work that runs in parallel, and top risks carry named mitigations.

## Related

* [Throughline Prototype: Remediation Plan](/references/prototype-remediation-plan.md) - the PI-cadence governance and capacity reconciliation this roadmap is reviewed under
* [Throughline Prototype: Onboarding Estimator](/references/prototype-onboarding-estimator.md) - the source of the per-use-case person-month unit this plan multiplies out
* [Throughline Prototype: Executive Narrative](/references/prototype-executive-narrative.md) - the value story this staffing plan is meant to fund
* [Throughline Prototype: PRD 1 Lineage Collection](/references/prototype-prd-1-lineage-collection.md) - defines the vertical-slice-first rollout this plan schedules

## Citations

1. [Roadmap & LOE.dc.html](/Users/sowmiyamohankumar/Documents/lineage-collector-v1/Data Lineage Impact Platform-10/Roadmap & LOE.dc.html)
