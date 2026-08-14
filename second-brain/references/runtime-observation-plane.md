---
type: Reference
title: Runtime Observation Plane
description: L06 watches instrumented integration-test runs and records which datasets and elements were actually read and written — metadata only, production hard-denied.
resource: /Users/sowmiyamohankumar/Documents/lineage-collector-v1/docs/component-prds/06-runtime-observation-plane.md
tags: [lineage, component-prd, runtime, observation, corroboration]
timestamp: 2026-08-14T11:30:00Z
---

# Runtime Observation Plane

## Purpose

L06 proves execution, not intent: during instrumented integration-test runs it records names, operations, counts, and schema fingerprints — never payload values. Its observations corroborate SCA and LLM assertions in L07, measurably lifting confidence bands (e.g. moving LLM-only edges to MEDIUM). It is also a hard security boundary: production observation is denied twice, at IAM and again in the validator, verified by a weekly live probe that must fail to inject.

## Key requirements and invariants

- No session, no ingestion: grants are signed JWTs scoped (repo, env) with TTL = test budget, audited, revocable; expired/revoked/unsigned sessions are rejected with security telemetry and never retried. Backfilling after session close is forbidden.
- Four emitters: OTel auto-instrumentation + attribute extractor (Phase 1, pulled forward for early corroboration); custom SDK `lineage.emit(op, rawName, elements[])`; Spark OpenLineage driver listener (element-level when the columnLineage facet is present); Dask scheduler plugin (dataset-level only). Only SDK and Spark may assert element lists.
- Validator enforces in order: signature/TTL, env ≠ prod, resolver pass (run's L01 pin; unresolved observations quarantine), and a closed metadata schema — any extra field rejects. Unmappable OTel spans drop with a metric, never a guess.
- Transport: Kinesis partitioned by dataset URN (ordered per dataset, no silent sampling) → Firehose → S3 `evidence/runtime/{session}/{datasetUrn}/{seq}.json`.
- Schema fingerprints are deterministic hashes of (field names, types, order) and never include values.
- Session lifecycle `GRANT → READY → OBSERVING → DRAIN → CLOSED(COMPLETE | INCOMPLETE | EXPIRED | REVOKED)`; only COMPLETE sessions corroborate at full weight, and INCOMPLETE never promotes confidence. Absence of runtime evidence never demotes an edge.

## Constraints and failure semantics

The validator sustains peak test bursts with backpressure into Kinesis and zero accepted-event loss; emitter overhead ≤ 5% of test wall time. A production-targeted observation triggers a sev-2 alert. Dataset-level observations carry half-step containment semantics pinned in the observation contract with L07.

## Related

* [Consolidation, URN Merge, and Confidence](/references/consolidation-and-confidence.md) - L07 consumes observations for corroboration under the pinned containment semantics
* [URN Grammar and Resolver Library](/references/urn-and-resolver-library.md) - the validator resolves every observed name through L01 and quarantines misses
* [Runtime Session and Ingestion (b08)](/references/b08-runtime-session-and-ingestion.md) - the build unit for the session API, validator, and stream topology
* [Runtime Emitters and Integrations (b09)](/references/b09-runtime-emitters-and-integrations.md) - the build unit shipping the OTel, SDK, Spark, and Dask emitters with conformance kits
* [Lineage Platform Acceptance](/references/lineage-platform-acceptance.md) - the weekly hard-deny probe and seeded corroboration scenario are standing acceptance gates

## Citations

1. [06-runtime-observation-plane.md](/Users/sowmiyamohankumar/Documents/lineage-collector-v1/docs/component-prds/06-runtime-observation-plane.md)
