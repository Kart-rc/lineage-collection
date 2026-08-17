---
type: Reference
title: "Throughline Prototype: Remediation Plan"
description: The prototype's owned, dated responses to the red-team review — 6-week PI value gates, capacity reconciliation, an identity-resolution P0 spike, an adoption workstream, role-based entry, and leading indicators.
resource: /Users/sowmiyamohankumar/Documents/lineage-collector-v1/Data Lineage Impact Platform-10/Remediation Plan.dc.html
tags: [prototype, throughline, remediation, governance, risk]
timestamp: 2026-08-14T12:10:00Z
---

# Throughline Prototype: Remediation Plan

The action plan answering the Critical Review's accepted findings, built for leadership buy-in.
Disposition: 10 actions — 1 resolved, 6 planned in this document, 3 tracked elsewhere. Each action
is owned, scoped, and dated, and the through-line is delivering demonstrable business value every
6-week Program Increment rather than one 24-month bet.

The six planned actions:

- A3, value every PI: a business-relevant outcome at the end of every 6-week PI, with stage-gate
  funding — leadership commits one phase at a time, and a PI that misses its value gate triggers a
  replan before the next phase is funded, capping downside to ~6 weeks instead of 24 months.
- A2, capacity reconciliation: the review flagged that net build was sized at ~175 eng-weeks
  against far more capacity from two teams over 24 months; the plan answers that build is roughly
  a quarter of the work and accounts for the full net pool (~10 engineers x ~104 weeks, less ~25%
  for leave, on-call, ceremonies, and ramp) across categories.
- A5, P0 spike on canonical identity resolution: the make-or-break technical risk — can the same
  entity be matched across static, Spark, Dask, and OTel reliably? Answered in PI 1 as a timeboxed
  spike with a measured exit decision on slice ground truth, before committing the plan.
- A4, adoption and change management: value requires every app team to instrument collectors and
  accept a CI/CD gate, so this is org change — a funded workstream with a named exec sponsor, run
  on an observe -> warn -> block trust ramp.
- A8, role-based entry and search-first landing: the prototype opens on a browsable graph, which
  is overwhelming for non-engineers and impractical at 10k assets. Each role lands on the surface
  answering its question; global search becomes the default front door (type an asset, see its
  ego-network), and browsing is demoted to a secondary explore path.
- A10, leading indicators: directional adoption/value metrics reviewed each PI, with end-of-phase
  targets for P1/P2/P3, explicitly leading indicators rather than substitutes for the PRDs'
  qualitative success criteria.

## Related

* [Throughline Prototype: Critical Review](/references/prototype-critical-review.md) - the red-team findings this plan answers point by point
* [Throughline Prototype: Roadmap and Level of Effort](/references/prototype-roadmap-and-loe.md) - the delivery plan operating on the same staged-funding logic
* [Throughline Prototype: Executive Narrative](/references/prototype-executive-narrative.md) - the leadership pitch these remediations were built to protect
* [Throughline Prototype: Onboarding Estimator](/references/prototype-onboarding-estimator.md) - the per-team instrumentation cost model behind the adoption workstream

## Citations

1. [Remediation Plan.dc.html](/Users/sowmiyamohankumar/Documents/lineage-collector-v1/Data Lineage Impact Platform-10/Remediation Plan.dc.html)
