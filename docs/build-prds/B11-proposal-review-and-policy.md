# B11 — Proposal, review and policy service

Normative requirements: [L09 review](../component-prds/09-proposal-review-and-autopublish.md).

## Ownership

Track D owns immutable proposal versions, review lifecycle, correction, approval/rejection evidence,
optimistic concurrency and auto-publish/narrowing policy. Domain owners supply reviewer mappings.

## Boundary

Turn consolidated edge diffs into versioned proposals, enforce server-authoritative decisions and
emit approval references consumable by B12. The service does not change graph pointers.

## Contracts

Proposal inputs bind system, active base, edge versions, coverage and determinants. Decisions bind
proposal/version/lock, actor, rationale and immutable approval/rejection reference.

## State and failure model

`DRAFT -> IN_REVIEW -> APPROVED|REJECTED|SUPERSEDED`; approved becomes finalized after publication.
Correction creates an immutable successor. Conditional locking yields one concurrent winner.

## Data ownership

B11 owns proposals, decisions, review queue, policy evaluations, ownership routing and audit sample.
It references evidence/edges/packages without copying them.

## Infrastructure bill of materials

Proposal/policy Lambda, DynamoDB proposal/decision tables with PITR/streams, S3 decision evidence,
review notification lane, KMS, identity integration, alarms and audit export.

## Local adapter

FastAPI service, SQLite records, file evidence and deterministic policy/clock implement the same
lifecycle and lock behavior with seeded reviewer fixtures.

## Security and privacy

Authorize by domain/action, prevent existence leaks, record workload/human identity, require rationale,
protect correction/policy changes and retain tamper-evident audit evidence.

## SLOs

Exactly one decision per proposal version and no unapproved publication are hard gates. Review age,
sampling and narrowing targets require enterprise ownership/volume approval.

## Observability

Expose queue age/volume, decision conflict, approve/reject/correct, owner routing, auto/manual path,
audit sample, narrowing and proposal-to-publication lag.

## Acceptance criteria

| Acceptance ID | Requirement | Scenario and evidence | Gate |
|---|---|---|---|
| B11-AC-001 | L09 immutable concurrent review | Concurrent decisions have one winner; correction creates a successor; every terminal decision has actor/rationale/reference and reproducible policy result | Every PR lifecycle gate |

## Deployment and rollback

Canary policy versions with shadow decisions. Roll back handler/policy aliases; never delete or
rewrite proposals/decisions. A policy rollback cannot retroactively approve work.

## Dependencies

B01, B03, B05, B10 and identity/ownership CTX seams. B12 consumes matching approvals.

## Definition of Ready

Lifecycle, lock semantics, reviewer authorization/ownership, rationale/audit policy, auto-publish
threshold and correction behavior are approved.

## Definition of Done

Concurrency/correction/policy fixtures pass; every decision is immutable/audited; queue/alarms and
rollback exist; `B11-AC-001` evidence is retained.
