---
type: Reference
title: Immutable Evidence Store and Cache
description: L08 owns the immutable, encrypted S3 truth layer for all lineage artifacts plus the content-addressed cache index that makes unchanged work free.
resource: /Users/sowmiyamohankumar/Documents/lineage-collector-v1/docs/component-prds/08-evidence-store-and-cache.md
tags: [lineage, component-prd, evidence, storage, cache]
timestamp: 2026-08-14T11:30:00Z
---

# Immutable Evidence Store and Cache

## Purpose

L08 is the truth layer: immutable, KMS-encrypted, content-organized S3 storage for evidence, residue, LLM artifacts, observations, proposals, approvals, manifests, and snapshots — plus DynamoDB content-addressed cache indexes (SCA per digest, LLM per cacheKey). Everything else in the platform is rebuildable from here; projections are rebuilt, never restored from backup.

## Key requirements and invariants

- Zero in-place overwrites of truth classes (evidence, proposals, approvals, manifests): S3 Object Lock plus deny-delete SCPs plus write-path tests; an overwrite attempt is a producer defect, denied and alarmed. Quarantine and rejects instead carry lifecycle expiry.
- Any projection or ledger is rebuildable from S3 alone — a quarterly drill proves it; manifests must enumerate everything needed for reconstruction (drill co-owned with L10).
- Every write goes through the single sanctioned client library — no direct puts from engines — which validates the kind against the normative prefix layout, computes and stores checksum + schemaVersion, and does a conditional no-overwrite put. Unchecksummed or unversioned writes are forbidden.
- Cache reuse: unchanged (repo, digest, rulesetVersion) SCA analysis and unchanged LLM chunks are served from prior artifacts; the LLM index cooperates with L05's staged invalidation via a stale flag, never a delete. A cache hit whose underlying object fails checksum is never served.
- Workflows carry small, stable `EvidenceRef`s (bucket, key, checksum, kind, schemaVersion) — never large payloads.
- Integrity sweeps: sampled per class daily, full quarterly; checksum mismatch refuses the read, consults the replica, and raises an incident.

## Interfaces

Client library: `put(kind, key, body) → EvidenceRef`, `get(ref) → verified body`, `cacheLookup(kind, key)`. The EvidenceRef format is a platform-wide contract; the kinds registry is versioned.

## Constraints

Ref read ≤ 100 ms p95 for control-sized artifacts (large ones streamed); 15-minute RPO / four-hour RTO for truth classes with cross-Region replication per DR policy; TLS-only private access with audit logging on evidence reads.

## Related

* [System Context and Architecture](/references/system-context.md) - realizes the platform rule that S3 is immutable truth and projections are rebuildable
* [Consolidation, URN Merge, and Confidence](/references/consolidation-and-confidence.md) - every L07 provenance entry references immutable evidence stored here, and the ledger rebuilds by replaying it
* [LLM Inference Gateway](/references/llm-inference-gateway.md) - the LLM cache index and staged stale-flag invalidation are a joint contract with L05
* [Evidence and Control Stores (b03)](/references/b03-evidence-and-control-stores.md) - the build unit delivering the bucket layout, lock policies, and client library
* [Fenced Publication and Projections](/references/fenced-publication-and-projections.md) - L10 depends on manifests being sufficient for full projection rebuild

## Citations

1. [08-evidence-store-and-cache.md](/Users/sowmiyamohankumar/Documents/lineage-collector-v1/docs/component-prds/08-evidence-store-and-cache.md)
