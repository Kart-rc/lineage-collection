---
type: Reference
title: PRD Ambiguity Register
description: The register of enterprise context seams (CTX-01..19), specification gaps, prototype assumptions, and cross-document inconsistencies with each item's reversible local decision and production owner.
resource: /Users/sowmiyamohankumar/Documents/lineage-collector-v1/docs/prd-ambiguities.md
tags: [lineage, ambiguities, ctx-seams, governance]
timestamp: 2026-08-14T11:30:00Z
---

# PRD Ambiguity Register

Reviewed against component PRDs L01-L16 and the readiness review. Four classifications: enterprise
context seam (needs organization-specific input), specification gap (needs normative authoring),
cross-document inconsistency (needs one source of truth), prototype assumption (a scoped, reversible
local decision). No enterprise value was inferred; every local choice hides behind a fixture,
setting, port, or deployment context.

## The seams that matter most

- CTX-01..04 (catalog): import schema, vocabularies, temp-name patterns, and alias governance are unknown — the fixture vocabulary is only `staging`/`snowflake`/`payments` and unknowns are rejected or quarantined.
- CTX-05..06 (CI/GitHub): Jenkins payload contract and GitHub App scope are absent — only local signed fixtures are enabled.
- CTX-09 (Bedrock) and CTX-18 (budgets): no external model call is made and no LLM spend is enabled anywhere.
- CTX-10..12 (runtime): Spark/OpenLineage listener installation, CI session grants, and OTel collector topology all remain enterprise-owned.
- CTX-13..17, CTX-19: retention, reviewer-group mapping, PII flags, paging/severity, audit baselines, and availability/DR targets await their owners; related rows emit `AWS_REQUIRED`.

Prototype assumptions include PA-01 (DynamoDB→SQLite, S3 Object Lock→write-once local store) and
PA-04 (edges stamped auto-publish-eligible but held for manual review in M1). Open inconsistencies:
INC-01 (dedupe TTL 14 vs 7 days), INC-02 (auto-publish at MVP vs M2), INC-04 (impact API shape —
the prototype uses `POST /api/impact` bounded-sync and reserves `/impact-jobs`).

## Before production

Resolve INC-01/02/04 into versioned constants; source every CTX value from its named owning team
(fixtures must never become production defaults); author the remaining gap contracts (matcher
tables, prompts, OTel mappings, calibration corpus schema); specify async impact jobs; and approve
an AWS account for the guarded ephemeral proof rather than asserting past `AWS_REQUIRED`.

## Related

* [Lineage Platform Remaining Goals](/projects/remaining-goals.md) - carries these open decisions as the blockers on G6 and production readiness
* [Implementation and Evidence Coverage](/references/prototype-coverage.md) - shows each seam as `NOT_CONFIGURED` rows in the evidence matrix
* [Implementation Readiness Review](/references/implementation-readiness-review.md) - the readiness review most of these findings were sourced from
* [System Context](/references/system-context.md) - the L01-L16 domain framing the register audits against

## Citations

1. [prd-ambiguities.md](/Users/sowmiyamohankumar/Documents/lineage-collector-v1/docs/prd-ambiguities.md)
