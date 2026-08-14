---
type: Reference
title: L10 Fenced Publication and Graph Projections PRD
description: "The single writer of the visible lineage graph: turns approved manifests into immutable versioned Neptune namespaces and atomically re-points the active version, fenced against split-brain and fully rebuildable."
resource: /Users/sowmiyamohankumar/Documents/lineage-collector-v1/docs/component-prds/10-fenced-publication-and-projections.md
tags: [lineage, component-prd, publication, fencing, projections]
timestamp: 2026-08-14T11:30:00Z
---

# L10 Fenced Publication and Graph Projections PRD

## Purpose

L10 is the only principal allowed to write graph projections. It converts an approved `AcceptedLineageManifest` into a new immutable graph namespace and advances the active pointer in one conditional transaction. Projections (Neptune at MVP, OpenSearch at P1) are disposable by design: anything visible can be rebuilt from manifests alone with zero divergence.

## Key requirements

- **Publication protocol:** write manifest → reserve + fence (conditional DynamoDB lock with monotonic token; an expired worker cannot win — property-tested) → stage into an *inactive* namespace → verify counts/checksums against the manifest → single DynamoDB transaction swaps the pointer iff reservation, token, and expected-prior-version all match; any failure discards staging.
- **Manifest contract:** checksummed; names proposal version, reviewer/policy, expected prior graph version, and full edge enumeration (or delta + base).
- **Rollback is a pointer event**, never a data rewrite — re-pointing to a prior namespace is a new audited event.
- **Rebuild tool** reconstructs any namespace from manifests alone; divergence is a defect (quarterly drill).
- **Pointer contract is shared:** the deploy-promotion lambda uses the same contract for env-truth re-points by artifact digest (merged ≠ running), and L11 reads only through the pointer.
- **Targets:** zero split-brain under contention (chaos-drilled); 95% of approved changes queryable within 60 s; median incremental publish ≤ 5 min; one-hop traversal ≤ 2 s p95 on the active namespace.

## State model and failure semantics

Publish: `MANIFEST_WRITTEN → RESERVED(fenced) → STAGED → VERIFIED → ACTIVE | DISCARDED`; namespaces: `STAGING → ACTIVE → PRIOR → EXPIRED`. `FENCE_LOST` aborts silently and redrive restages; `VERIFY_MISMATCH` never swaps (defect with diff attached); `POINTER_CONFLICT` forces a rebase via L09, never a force-write; `PROJECTION_LOSS` is recoverable by rebuild with a staleness watermark exposed meanwhile.

## Constraints

Never write into an active namespace; never advance the pointer without a valid token and expected prior; never delete namespace history inside retention; no writer other than L10 touches projections. Manifests are S3 Object-Locked; pointer-table writes restricted to publisher + promotion lambda; all swaps CloudTrail-audited.

## Related

* [Proposal Review and Autopublish](/references/proposal-review-and-autopublish.md) - manifests map 1:1 to L09 approvals; nothing unapproved may stage
* [APIs, Review UI, and PR Gate](/references/apis-review-ui-and-pr-gate.md) - L11 reads exclusively through the active pointer and honors version pinning for history
* [Evidence Store and Cache](/references/evidence-store-and-cache.md) - manifests and truth classes live in the Object-Locked store that makes projections disposable
* [B12 Publisher and Projection Manager](/references/b12-publisher-and-projection-manager.md) - the build PRD that implements the fencing protocol, namespaces, and rebuild tooling
* [Lineage Platform Acceptance](/references/lineage-platform-acceptance.md) - fencing property tests, weekly publish-lock chaos, and rebuild drills are normative acceptance for this component

## Citations

1. [10-fenced-publication-and-projections.md](/Users/sowmiyamohankumar/Documents/lineage-collector-v1/docs/component-prds/10-fenced-publication-and-projections.md)
