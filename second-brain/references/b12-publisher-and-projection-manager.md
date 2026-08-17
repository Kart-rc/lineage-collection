---
type: Reference
title: B12 Publisher and Projection Manager
description: Delivers fenced, resumable publication of approved lineage packages into disposable graph namespaces with checksum verification and atomic pointer activation.
resource: /Users/sowmiyamohankumar/Documents/lineage-collector-v1/docs/build-prds/B12-publisher-and-projection-manager.md
tags: [lineage, build-prd, publication, fencing, projections]
timestamp: 2026-08-14T11:30:00Z
---

# B12 Publisher and Projection Manager

Track D's publication engine (normative sources: L10 fenced publication, L15 resilience). Given an
exact approved proposal/package and an expected prior version, it builds a disposable projection
namespace, verifies its checksum, and atomically activates a single pointer. It **cannot infer or
repair approval** — approval references come only from B11 and are verified before any write.

## What it delivers

Approved-package manifests, publication operation state, namespace staging, verification, monotonic
fences, atomic pointer activation, an outbox for cache/query invalidation, rollback, and rebuild.

## Publication model and invariants

- A manifest binds operation/proposal/approval/package digests, expected prior, namespace, edge
  count, and checksum. Every conditional pointer write carries a monotonic fence token and returns
  a durable result.
- Publication is a redrivable staged pipeline: manifest, reservation, namespace, edge batches,
  verify, activate, outbox, complete. A crash after any durable boundary redrives to the same
  result (B12-AC-001, per-PR fault/property gate).
- **A stale fence cannot activate** — a stale publisher always loses; exactly one active verified
  version exists at any time, and zero stale activation is a mandatory SLO.
- Verify mismatch retains or discards staging by policy; rollback is not a delete but a new
  fenced/audited pointer event to a verified retained namespace; rebuild validates checksum before
  activation.

## Data ownership and boundary

B12 owns publication operations, manifests, graph namespaces, pointers, activation/rollback audit,
and the invalidation outbox. Evidence, proposals, and packages remain immutable upstream truth.

## Infrastructure and constraints

Publication Lambda, DynamoDB operation/fence/pointer tables, S3 manifests, Neptune staged graph,
queues/DLQ, VPC endpoints, canary aliases, rebuild jobs — Neptune/control resources come from B16.
The local adapter proves every boundary with SQLite transactions, file manifests, and named crash
injection under the same expected-prior/fence/checksum invariants. Visibility/rebuild timing
targets are environment-specific evidence, not local claims. Retention, rebuild, and RPO policy
are CTX seams. Only the publication role may reserve/activate.

## Related

* [Fenced Publication and Projections](/references/fenced-publication-and-projections.md) - the normative L10 component PRD this build unit implements.
* [B11 Proposal, Review and Policy](/references/b11-proposal-review-and-policy.md) - upstream source of the approval references B12 must verify and can never infer or repair.
* [B13 Query, Impact and PR Gate](/references/b13-query-impact-and-pr-gate.md) - downstream reader pinned to the active pointer/projection B12 activates.
* [B16 Platform IaC and Delivery](/references/b16-platform-iac-and-delivery.md) - provides the Neptune and control-plane resources B12 depends on.
* [NFR and Resiliency Spec](/references/nfr-and-resiliency-spec.md) - the L15 normative source for B12's crash-redrive and resilience requirements.

## Citations

1. [B12-publisher-and-projection-manager.md](/Users/sowmiyamohankumar/Documents/lineage-collector-v1/docs/build-prds/B12-publisher-and-projection-manager.md)
