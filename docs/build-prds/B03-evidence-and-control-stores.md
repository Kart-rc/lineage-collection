# B03 — Evidence and control-store adapters

Normative requirements: [L08 evidence](../component-prds/08-evidence-store-and-cache.md),
[L10 publication](../component-prds/10-fenced-publication-and-projections.md), and
[L15 resilience](../component-prds/15-nfr-and-resiliency-spec.md).

## Ownership

Track A owns storage ports, migrations, retention classes, backup/restore and local/AWS adapter
contract suites. Domain producers own the completeness and classification of data they write.

## Boundary

Immutable large objects live behind an artifact port; receipts, commands, leases, stage results,
outbox records, manifests, fences and pointers live behind conditional control-store ports.

## Contracts

Evidence references include bucket/key or kind/key, immutable version, checksum and schema version.
Control mutations require idempotency identity and, where applicable, expected state/lease epoch/fence.

## State and failure model

Artifact overwrite and checksum mismatch fail closed. Control writes are conditional and retry-safe.
Throttling is typed/retryable; stale lease or fence is terminal for that writer. Projections rebuild
from truth rather than becoming hidden truth.

## Data ownership

B03 owns physical storage and lifecycle metadata. Evidence, decisions and manifests are truth;
queues/caches/projections are recoverable; retention and legal-hold classes remain configurable.

## Infrastructure bill of materials

KMS-encrypted S3 buckets with versioning, public-access block and Object Lock where required;
DynamoDB tables with PITR, deletion protection, streams, GSIs and backups; alarms, replication and
restore automation. Exact retention/RPO/RTO require approval.

## Local adapter

Files plus SQLite/WAL implement immutable writes, transactions, epochs and outbox semantics under
the same contract tests and deterministic fault points.

## Security and privacy

Per-kind IAM, tenant/environment conditions, encryption, TLS, audited reads and redacted logs.
Evidence access authorization must not leak the existence of unauthorized objects.

## SLOs

Zero accepted overwrite or stale conditional success. Proposed RPO/RTO remain unclaimed until AWS
backup/restore drills run in the approved environment.

## Observability

Expose conditional conflicts, throttling, object checksum failures, outbox age, table/bucket growth,
backup age, replication lag and restore drill evidence.

## Acceptance criteria

| Acceptance ID | Requirement | Scenario and evidence | Gate |
|---|---|---|---|
| B03-AC-001 | L08/L10 immutable truth and rebuild | Overwrite/corruption/stale-epoch writes fail; clean rebuild from manifests has zero checksum divergence; retain fault/rebuild report | Every PR locally; deploy/quarterly AWS gate |

## Deployment and rollback

Use additive migrations and backup-before-change. Roll application aliases back first; data rollback
uses versioned objects/point-in-time restore to a new resource, never destructive in-place rewind.

## Dependencies

B01 contracts. KMS, retention, residency, replication and backup approvals are CTX seams.

## Definition of Ready

Data classes, keys/indexes, access matrix, retention owner, migration, backup and recovery oracle are
reviewed.

## Definition of Done

Both adapters pass shared contracts; production resources satisfy assertions; restore/rebuild
evidence has the correct environment outcome; `B03-AC-001` is retained.
