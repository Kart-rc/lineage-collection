---
type: Reference
title: Build-Ready Lineage Platform PRDs
description: Overview of the B01-B16 build-ready PRDs that turn the normative L01-L16 domain requirements into owned, deployable build units with a strict six-stage build order.
resource: /Users/sowmiyamohankumar/Documents/lineage-collector-v1/docs/build-prds/README.md
tags: [lineage, build-prd, delivery, architecture, planning]
timestamp: 2026-08-14T11:30:00Z
---

# Build-Ready Lineage Platform PRDs

The build-prds set (B01-B16) converts the normative L01-L16 component requirements into owned,
deployable artifacts. Deliberately, there is not one service per PRD: several build units share the
same versioned Python package and are separated instead by ports, handlers, IAM boundaries, scaling
and release configuration.

## Build order

Six stages, each depending on frozen releases of the previous:

1. **Foundations** — B01 contracts/correlation, B02 catalog/resolver, B03 evidence/control stores.
2. **Durable flow** — B04 event intake and lanes, B05 classifier and orchestrator.
3. **Evidence engines** — B06 SCA worker, B07 LLM gateway, B08 runtime session/ingestion, B09 runtime emitters.
4. **Trust** — B10 consolidation/confidence, B11 proposal review/policy, B12 publisher/projections.
5. **Product and operations** — B13 query/impact/PR gate, B14 review/ops UI, B15 telemetry/recovery.
6. **Delivery** — B16 platform IaC and delivery.

## Governing rules

- The executable acceptance policy (lineage-platform-acceptance) is **normative** for all units.
- Unknown enterprise `CTX-*` values are Definition-of-Ready seams — they must be named and owned,
  and are never allowed to become fixture defaults in production.
- No downstream build unit is Ready until its upstream contracts, fixtures, test oracle and
  rollback boundary are versioned.
- `AWS_REQUIRED` and `NOT_CONFIGURED` are visible, honest outcomes — never treated as passes.

## Related

* [Lineage Platform Acceptance](/references/lineage-platform-acceptance.md) - the README declares this executable acceptance policy normative for every build unit
* [B01 Contracts, Schemas and Correlation](/references/b01-contracts-and-correlation.md) - first foundation unit; every other build unit depends on a frozen B01 release
* [B02 Catalog Snapshot and Resolver Packages](/references/b02-catalog-and-resolver.md) - foundation unit providing pinned identity resolution
* [B03 Evidence and Control-Store Adapters](/references/b03-evidence-and-control-stores.md) - foundation unit providing immutable evidence and conditional control storage
* [Lineage Collector](/projects/lineage-collector.md) - the project these build PRDs implement

## Citations

1. [README.md](/Users/sowmiyamohankumar/Documents/lineage-collector-v1/docs/build-prds/README.md)
