---
type: Reference
title: B03 Evidence and Control-Store Adapters
description: Storage ports and dual local/AWS adapters providing immutable checksummed evidence objects and conditional, fenced control-store writes that fail closed on overwrite or stale state.
resource: /Users/sowmiyamohankumar/Documents/lineage-collector-v1/docs/build-prds/B03-evidence-and-control-stores.md
tags: [lineage, build-prd, evidence, storage, resilience]
timestamp: 2026-08-14T11:30:00Z
---

# B03 Evidence and Control-Store Adapters

B03 delivers the platform's storage layer as ports with two adapters: immutable large objects live
behind an artifact port; receipts, commands, leases, stage results, outbox records, manifests,
fences and pointers live behind conditional control-store ports. Track A owns the ports, migrations,
retention classes, backup/restore and the shared local/AWS adapter contract suites; domain producers
own completeness and classification of what they write.

## Key contracts and invariants

- Evidence references include bucket/key or kind/key, immutable version, checksum and schema version.
- Control mutations require idempotency identity and, where applicable, expected state, lease epoch
  or fence; stale lease/fence is *terminal* for that writer.
- Artifact overwrite and checksum mismatch fail closed; throttling is typed and retryable.
- Evidence, decisions and manifests are truth; queues/caches/projections are recoverable and
  rebuild from truth rather than becoming hidden truth.
- B03-AC-001: overwrite/corruption/stale-epoch writes must fail, and a clean rebuild from manifests
  must show zero checksum divergence (every PR locally; deploy and quarterly AWS gate).

## Adapters and infrastructure

AWS: KMS-encrypted S3 with versioning and Object Lock where required; DynamoDB with PITR, deletion
protection, streams, GSIs and backups. Local: files plus SQLite/WAL implementing the same immutable
writes, transactions, epochs and outbox semantics under identical contract tests with deterministic
fault points. Evidence-access authorization must not leak the existence of unauthorized objects.

## Rollback and seams

Additive migrations with backup-before-change; roll application aliases back first, and data
rollback restores versioned objects/PITR *to a new resource* — never a destructive in-place rewind.
Depends only on B01 contracts. KMS, retention, residency, replication and backup approvals are CTX
seams; proposed RPO/RTO remain unclaimed until AWS restore drills run in the approved environment.

## Related

* [Evidence Store and Cache](/references/evidence-store-and-cache.md) - normative L08 evidence requirements B03 implements
* [Fenced Publication and Projections](/references/fenced-publication-and-projections.md) - normative L10 fencing/rebuild requirements behind the control-store ports
* [NFR and Resiliency Spec](/references/nfr-and-resiliency-spec.md) - normative L15 resilience requirements governing backup, restore and RPO/RTO claims
* [B01 Contracts, Schemas and Correlation](/references/b01-contracts-and-correlation.md) - sole upstream build dependency for schemas
* [B04 Event Intake and Lane Router](/references/b04-event-intake-and-lanes.md) - first downstream unit writing receipts, commands and outbox records through B03 ports

## Citations

1. [B03-evidence-and-control-stores.md](/Users/sowmiyamohankumar/Documents/lineage-collector-v1/docs/build-prds/B03-evidence-and-control-stores.md)
