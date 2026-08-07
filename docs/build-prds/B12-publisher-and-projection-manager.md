# B12 — Publisher and projection manager

Normative requirements: [L10 fenced publication](../component-prds/10-fenced-publication-and-projections.md)
and [L15 resilience](../component-prds/15-nfr-and-resiliency-spec.md).

## Ownership

Track D owns approved-package manifests, publication operation state, namespace staging, verification,
monotonic fences, atomic pointer activation, outbox invalidation, rollback and rebuild.

## Boundary

Given an exact approved proposal/package and expected prior, build a disposable projection namespace,
verify its checksum, then atomically activate one pointer. It cannot infer or repair approval.

## Contracts

Manifest binds operation/proposal/approval/package digests, expected prior, namespace, edge count and
checksum. Every conditional pointer write carries a monotonic token and returns a durable result.

## State and failure model

Publication redrives manifest, reservation, namespace, edge batches, verify, activate, outbox and
complete stages. A stale fence cannot activate. Verify mismatch retains/discards staging by policy.

## Data ownership

B12 owns publication operations, manifests, graph namespaces, pointers, activation/rollback audit and
cache/query invalidation outbox. Evidence/proposals/packages remain immutable upstream truth.

## Infrastructure bill of materials

Publication Lambda, DynamoDB operation/fence/pointer tables, S3 manifests, Neptune staged graph,
activation/cache outbox, queues/DLQ, KMS, VPC endpoints, aliases/canary, alarms and rebuild jobs.

## Local adapter

SQLite transactions, file manifests and versioned graph tables prove every boundary with named crash
injection and the same expected-prior/fence/checksum invariants.

## Security and privacy

Only the publication role may reserve/activate; approval references are verified; Neptune/S3/Dynamo
actions are resource-scoped; manifest/checksum/audit data is immutable and correlated.

## SLOs

Exactly one active verified version and zero stale activation are mandatory. Visibility/rebuild targets
remain environment-specific evidence, not local claims.

## Observability

Expose operation stage/age/redrive, fence conflict, batch/checksum verify, pointer swap, approval link,
outbox delivery, pointer/package mismatch, projection watermark and rebuild divergence.

## Acceptance criteria

| Acceptance ID | Requirement | Scenario and evidence | Gate |
|---|---|---|---|
| B12-AC-001 | L10/L15 fenced resumable publication | Crash after every durable boundary redrives to the same result with one active namespace, one approval link and zero checksum divergence; stale publisher loses | Every PR fault/property gate |

## Deployment and rollback

Canary handler aliases without changing in-flight operation version. Rollback is a new fenced/audited
pointer event to a verified retained namespace; rebuild validates checksum before activation.

## Dependencies

B01, B03, B05, B11 approval and B16 Neptune/control resources. Retention/rebuild/RPO policy are CTX
seams.

## Definition of Ready

Manifest, operation stages, fence/pointer transaction, staging failure/retention, batch limit,
verification oracle and rollback target policy are reviewed.

## Definition of Done

Every crash/fence/corruption test passes; approval is never lost; resources/IAM/alarms/rebuild and
`B12-AC-001` evidence exist.
