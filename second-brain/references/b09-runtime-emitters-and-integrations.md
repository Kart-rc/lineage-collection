---
type: Reference
title: B09 Runtime Emitters and Integration Kits
description: Delivers the client-side runtime observation kits — OpenLineage facets, OTel mappings, custom SDKs, and Spark/Dask integrations — that capture metadata-only lineage signals and submit bounded observations to the B08 session plane.
resource: /Users/sowmiyamohankumar/Documents/lineage-collector-v1/docs/build-prds/B09-runtime-emitters-and-integrations.md
tags: [lineage, build-prd, runtime, emitters, openlineage]
timestamp: 2026-08-14T11:30:00Z
---

# B09 Runtime Emitters and Integration Kits

Track C's client-side half of the runtime observation plane (normative source: L06). B09 ships
the emitter packages that run inside customer workloads; B08 owns the server side that accepts
what they send.

## What it delivers

Signed Python/JVM/Node packages, OpenLineage listener artifacts, OTel collector configuration,
compatibility matrices, and integration documentation. Kits implement a session lifecycle —
ready/observe/drain/close — with checksum support, binding every observation to the exact deployed
artifact/session.

## Key invariants

- **Emitters never assign confidence.** They capture metadata-only relationships at supported
  framework boundaries; trust decisions happen downstream (B10).
- Mechanism/granularity distinctions are preserved: OpenLineage column facets, SDK field mappings,
  and OTel semantic-convention mappings must retain distinct semantics through ingestion.
- Loss is honest: clients buffer within explicit limits, preserve per-dataset ordering, retry
  idempotently, and close sessions as *incomplete* on loss or throttle. Unsupported topology or
  generic connectivity is labeled, never upgraded to a stronger claim.
- Privacy is enforced client-side: SDK allowlists and prohibited-field checks run before transport;
  no SQL parameters, record values, or secrets are ever emitted.

## Data ownership and boundary

B09 owns emitter packages, mappings, integration manifests, and client-side counters. Accepted
observations and manifests belong to B08. Application data values are never owned or emitted.

## Constraints and seams

The proposed at-most-5% overhead SLO stays `AWS_REQUIRED`/integration-required until measured on
approved workloads. The local adapter (JSON fixtures plus fake transport/buffer/drain) proves
mapping and loss semantics but not platform installation or runtime overhead. Definition-of-Ready
seams: framework/version matrix, mapping authority, metadata allowlist, buffer policy, install
owner, overhead workload, rollback switch. Rollback disables instrumentation or restores the prior
kit while draining sessions honestly. Single acceptance gate: B09-AC-001 (mechanism fidelity,
honest incomplete close, environment-named overhead evidence).

## Related

* [B08 Runtime Session and Ingestion](/references/b08-runtime-session-and-ingestion.md) - B08 is the server-side counterpart that accepts the bounded observations B09 emitters submit and owns accepted observations/manifests.
* [B01 Contracts and Correlation](/references/b01-contracts-and-correlation.md) - B09 depends on B01 contracts for observation schemas and artifact/session binding.
* [B10 Consolidation and Confidence](/references/b10-consolidation-and-confidence.md) - runtime assertions from the emitter path feed B10 merging, which assigns the confidence emitters must never claim.
* [Runtime Observation Plane](/references/runtime-observation-plane.md) - the normative L06 component PRD from which this build unit's requirements derive.

## Citations

1. [B09-runtime-emitters-and-integrations.md](/Users/sowmiyamohankumar/Documents/lineage-collector-v1/docs/build-prds/B09-runtime-emitters-and-integrations.md)
