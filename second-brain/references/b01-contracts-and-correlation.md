---
type: Reference
title: B01 Contracts, Schemas and Correlation
description: A versioned contracts library and schema bundle defining strict JSON Schemas and correlation conventions that every other build unit (B02-B16) consumes as a frozen release.
resource: /Users/sowmiyamohankumar/Documents/lineage-collector-v1/docs/build-prds/B01-contracts-and-correlation.md
tags: [lineage, build-prd, contracts, schemas, correlation]
timestamp: 2026-08-14T11:30:00Z
---

# B01 Contracts, Schemas and Correlation

B01 delivers a versioned library and schema bundle — explicitly **not** a network service. It
defines the envelopes and reference types shared by local modules, Lambda handlers, Step Functions
and external integration kits. Track A owns the package, compatibility policy, generators and
correlation conventions; producers own conformance, consumers own explicit supported-version ranges.

## Key contracts

- Strict JSON Schemas for events, commands, stage references, evidence, coverage, runtime sessions,
  proposals, approvals, packages, deployment outcomes and acceptance evidence.
- Correlation and causation identifiers required at every durable boundary.
- Compatibility is additive within a major version; unknown major versions fail with a typed error.
- Closed schemas reject undeclared fields where metadata-only guarantees apply.

## Failure and rollback model

Schemas are immutable release artifacts and generation is deterministic. Schema drift, an unknown
major, missing correlation or producer/consumer incompatibility is a merge-blocking build failure
(B01-AC-001, gated on every PR). Packages are published immutably with signature, checksum, SBOM
and provenance; rollback selects the prior signed package — schema records are never rewritten.

## Dependencies and seams

B01 has no build-time domain dependency; B02-B16 all depend on a frozen B01 release plus a
generated-model check. Production registry/account values are `CTX` seams needing named owners.
Local repository JSON files and Python/TypeScript model checks use the same fixtures and strictness
as published packages — local imports never bypass schema validation at ingress boundaries.

## Related

* [Build-Ready Lineage Platform PRDs](/references/build-prd-overview.md) - B01 is the first foundation unit in the build order
* [URN and Resolver Library](/references/urn-and-resolver-library.md) - normative L01 identity requirements that B01 implements as contracts
* [Evidence Store and Cache](/references/evidence-store-and-cache.md) - normative L08 evidence requirements covered by B01 schemas
* [Security, Observability and Operations](/references/security-observability-operations.md) - normative L12 operations requirements behind correlation and signing rules
* [B02 Catalog Snapshot and Resolver Packages](/references/b02-catalog-and-resolver.md) - first downstream consumer of frozen B01 schemas and correlation

## Citations

1. [B01-contracts-and-correlation.md](/Users/sowmiyamohankumar/Documents/lineage-collector-v1/docs/build-prds/B01-contracts-and-correlation.md)
