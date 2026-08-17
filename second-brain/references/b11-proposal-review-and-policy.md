---
type: Reference
title: B11 Proposal, Review and Policy Service
description: Delivers the server-authoritative proposal lifecycle that turns consolidated edge diffs into immutable versioned proposals with audited human/auto decisions consumable by the publisher.
resource: /Users/sowmiyamohankumar/Documents/lineage-collector-v1/docs/build-prds/B11-proposal-review-and-policy.md
tags: [lineage, build-prd, review, proposals, policy]
timestamp: 2026-08-14T11:30:00Z
---

# B11 Proposal, Review and Policy Service

Track D's trust-tier gatekeeper (normative source: L09). It converts consolidated edge diffs from
B10 into versioned proposals, enforces server-authoritative review decisions, and emits approval
references that B12 consumes. It deliberately **does not change graph pointers** — publication is
B12's job.

## What it delivers

Immutable proposal versions, the review lifecycle, correction flow, approval/rejection evidence,
optimistic concurrency, and the auto-publish/narrowing policy. Domain owners supply reviewer
mappings; the service handles routing and policy evaluation.

## Lifecycle and invariants

- State machine: `DRAFT -> IN_REVIEW -> APPROVED|REJECTED|SUPERSEDED`; approved proposals become
  finalized only after publication.
- **Exactly one decision per proposal version** and **no unapproved publication** are hard gates.
- Conditional locking yields one winner under concurrent decisions; correction never edits in
  place — it creates an immutable successor (B11-AC-001, per-PR lifecycle gate).
- Every terminal decision binds proposal/version/lock, actor (workload or human identity),
  rationale, and an immutable approval/rejection reference; policy results must be reproducible.
- A policy rollback cannot retroactively approve work; proposals/decisions are never deleted or
  rewritten.

## Data ownership and contracts

B11 owns proposals, decisions, the review queue, policy evaluations, ownership routing, and the
audit sample. It *references* evidence, edges, and packages without copying them. Proposal inputs
bind system, active base, edge versions, coverage, and determinants.

## Infrastructure and constraints

Proposal/policy Lambda, DynamoDB proposal/decision tables with PITR/streams, S3 decision evidence,
review notification lane, identity integration, audit export. Local adapter: FastAPI + SQLite +
deterministic policy/clock reproducing the same lifecycle and lock behavior. Review-age, sampling,
and narrowing SLO targets require enterprise ownership/volume approval (CTX seams alongside
identity/ownership). Security emphasizes domain/action authorization, existence-leak prevention,
mandatory rationale, and tamper-evident audit evidence. New policy versions canary with shadow
decisions before cutover.

## Related

* [Proposal Review and Autopublish](/references/proposal-review-and-autopublish.md) - the normative L09 component PRD this build unit implements.
* [B10 Consolidation and Confidence](/references/b10-consolidation-and-confidence.md) - upstream producer of the consolidated edge diffs B11 turns into proposals.
* [B12 Publisher and Projection Manager](/references/b12-publisher-and-projection-manager.md) - downstream consumer of B11's matching approval references; only it moves graph pointers.
* [B14 Review and Operations UI](/references/b14-review-and-operations-ui.md) - renders B11's server-authoritative proposal and decision state for reviewers.
* [B03 Evidence and Control Stores](/references/b03-evidence-and-control-stores.md) - upstream store dependency for the evidence and control records proposals reference.

## Citations

1. [B11-proposal-review-and-policy.md](/Users/sowmiyamohankumar/Documents/lineage-collector-v1/docs/build-prds/B11-proposal-review-and-policy.md)
