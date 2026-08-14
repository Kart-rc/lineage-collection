---
type: Reference
title: L11 Query APIs, Review UI, and PR-Gate Experience PRD
description: "The experience layer of the lineage platform: query and impact APIs, the review SPA, run timeline, and PR-gate verdicts — always rendered with confidence band and provenance, never implying more certainty than the band supports."
resource: /Users/sowmiyamohankumar/Documents/lineage-collector-v1/docs/component-prds/11-apis-review-ui-and-pr-gate.md
tags: [lineage, component-prd, pr-gate, impact-analysis, review-ui]
timestamp: 2026-08-14T11:30:00Z
---

# L11 Query APIs, Review UI, and PR-Gate Experience PRD

## Purpose

L11 is "where trust is experienced": it exposes approved lineage, the review workflow, quarantine triage, the run timeline, and PR-gate verdicts. Its core contract is honesty about certainty — every rendered edge shows band + mechanisms + evidence links (no bare edges), projection staleness is bannered rather than hidden, and the SPA holds no authoritative state (UI states mirror server states 1:1).

## Key requirements

- **Query API:** `GET /lineage/{urn}` up/down, depth ≤ 5, env-scoped, active namespace via the L10 pointer, with version pinning for history; edge detail exposes full provenance (mechanisms, citations, sessions, rejects) and confidence axes. One-hop and proposal views ≤ 2 s p95.
- **Review SPA:** queues (proposals, CONFLICTING, corrections, quarantine, audit samples) with ownership routing; proposal diff with before/after; correction and approval flows per the L09 contract; quarantine triage hands alias decisions to L01. A reviewer completes a decision without leaving the SPA.
- **PR gate (P1):** `GET /impact` used read-only; verdict classes pass / warn-with-consumers / block-on-high-confidence-break, environment baseline named, superseded runs collapsed. On upstream timeout the gate falls to WARN — never a silent PASS.
- **Dashboards:** join rate, cache hit, audit disagreement, queue depth, corroboration coverage (the launch-gate views).
- **Security:** Cognito OIDC SSO; domain-scoped RBAC on review actions; evidence reads ABAC-audited; 403s leak no existence of unauthorized systems; PR-gate tokens scoped to check-write only.

## Impact analysis specification (§15, closes G-L11-1)

Six change types: COLUMN_DROP, COLUMN_TYPE_CHANGE, COLUMN_RENAME, DATASET_REMOVAL, TRANSFORM_CHANGE, FINGERPRINT_DRIFT. Two traversal modes: bounded synchronous downstream (depth ≤ 5) for interactive/PR use, plus an async full-closure job (`POST /impact-jobs`) with webhook/UI notification. Severity is band-gated: drops/type-changes/removals BLOCK only at HIGH/HIGHEST band, WARN at MEDIUM, INFO below; renames/transform changes cap at WARN; FINGERPRINT_DRIFT adds a review item. BLOCK derives only from the band — LLM-only (LOWEST) edges can never block. PR verdict = max severity over the affected set, and the response schema includes namespace version, depth, truncation flag, per-consumer band/corroboration/path, and a block/warn/info summary.

## Constraints

Forbidden: serving stale pointers silently, rendering edges without band + provenance, client-side-only state transitions, unbounded traversals (`DEPTH_EXCEEDED` is a 400, never partial-silent). No payload values exist anywhere to leak — metadata-only platform contract.

## Related

* [Fenced Publication and Projections](/references/fenced-publication-and-projections.md) - all reads go through L10's active pointer with projection-lag watermarks surfaced
* [Proposal Review and Autopublish](/references/proposal-review-and-autopublish.md) - the review SPA drives the L09 lifecycle and its states map 1:1 to L09 server states
* [Consolidation and Confidence](/references/consolidation-and-confidence.md) - bands and corroboration from L07 gate what may BLOCK in impact verdicts
* [B13 Query, Impact, and PR Gate](/references/b13-query-impact-and-pr-gate.md) - build PRD for the query/impact APIs and PR-check rendering
* [B14 Review and Operations UI](/references/b14-review-and-operations-ui.md) - build PRD for the review SPA and dashboards this component specifies

## Citations

1. [11-apis-review-ui-and-pr-gate.md](/Users/sowmiyamohankumar/Documents/lineage-collector-v1/docs/component-prds/11-apis-review-ui-and-pr-gate.md)
