---
type: Reference
title: L09 Proposal, Human Review, and Auto-Publish Policy PRD
description: "The human gate of the lineage platform: proposal lifecycle, immutable corrections, sampled-audit auto-publish for parser-exact edges, and the calibration corpus every decision feeds."
resource: /Users/sowmiyamohankumar/Documents/lineage-collector-v1/docs/component-prds/09-proposal-review-and-autopublish.md
tags: [lineage, component-prd, review, auto-publish, governance]
timestamp: 2026-08-14T11:30:00Z
---

# L09 Proposal, Human Review, and Auto-Publish Policy PRD

## Purpose

L09 owns the proposal lifecycle and its economics: which consolidated edges need a human decision, which parser-exact edges may bypass review under an audited policy, and how every decision becomes training signal (calibration corpus). It is P0 because baseline volume makes reviewing parser-exact edges impractical — auto-publish with a 5% weekly sampled audit is active from MVP (readiness decision Q8), with governance sign-off on the policy file as a launch gate.

## Key requirements

- **State machine, server-side:** DRAFT → IN_REVIEW → (APPROVED | REJECTED | SUPERSEDED) → FINALIZED; every transition audited with actor + correlation. Proposal creation is deterministic against an expected base graph version, with diffs (added/removed/band-changed) computed.
- **Corrections never mutate:** a correction produces edge v+1 and proposal v+1; originals retained, supersession linked bidirectionally; no lost history under concurrency (property-tested).
- **Auto-publish policy engine:** only parser-exact-stamped edges bypass review (flagged AUTO in the manifest); 5%/week seeded-random reproducible sample goes to an audit queue. Disagreement ≥ 2% (rolling 4 weeks) triggers *automatic* class narrowing (`ACTIVE → NARROWED → RESTORED`, audited) — policy, not manual intervention. Optional PII-dataset exclusion flag.
- **Review queue contains only** LLM-only, CONFLICTING, and corrections; depth must stay below reviewer capacity (launch gate R3).
- **Routing and provenance:** reviewers routed by system ownership (TAS join key), escalation for unowned datasets; proposals expose provenance verbatim — mechanisms, LLM citations, and rejects always visible.
- **Calibration corpus:** 100% of decisions (approve/reject/correct/audit verdict) write a labeled corpus record with evidence refs and band at decision time.

## Interfaces

Review API endpoints with Pact contracts to the L11 SPA; the AcceptedApproval record schema is the input contract to L10 manifests. State in S3 (`proposals/`, `approvals/`, `calibration/`) plus DynamoDB lifecycle/queue indexes.

## Failure semantics and constraints

`STALE_BASE_VERSION` rebases the proposal; `CONCURRENT_DECISION` resolves via conditional write (loser sees fresh state, never double-applies); `AUDIT_BREACH` fires automatic narrowing plus governance notification; `UNOWNED_SYSTEM` escalates as a catalog gap. Forbidden: in-place edits, auto-publishing without the parser-exact stamp or during narrowing, discarding history, or approving without recorded reviewer identity. Proposal detail assembly ≤ 2 s p95.

## Related

* [Consolidation and Confidence](/references/consolidation-and-confidence.md) - L07 supplies the consolidated edges (and bands) that L09 proposes, and correction round-trips produce edge v+1 re-merges
* [Fenced Publication and Projections](/references/fenced-publication-and-projections.md) - only APPROVED or in-policy AUTO edges reach an L10 manifest; approval records are its input contract
* [APIs, Review UI, and PR Gate](/references/apis-review-ui-and-pr-gate.md) - L11 renders the review queues and diff views, with UI states mapping 1:1 to L09 server states
* [B11 Proposal Review and Policy](/references/b11-proposal-review-and-policy.md) - the build PRD that implements this component
* [Lineage Platform Acceptance](/references/lineage-platform-acceptance.md) - the normative test suite for the conflict/correction scenario and review API contracts this PRD cites

## Citations

1. [09-proposal-review-and-autopublish.md](/Users/sowmiyamohankumar/Documents/lineage-collector-v1/docs/component-prds/09-proposal-review-and-autopublish.md)
