# 06-runtime-observation-plane.md

# L06 Runtime Observation Plane PRD

## 1. Document Control

| Field | Value |
|---|---|
| Component | L06 |
| Status | Draft for implementation |
| Launch phase | Phase 1 (sessions + validator + OTel emitter — pulled forward for earlier corroboration) · Phase 2 (SDK, Spark, Dask emitters) |
| Criticality | P0 for corroboration; hard security boundary (prod deny) |
| Primary owner | Lineage platform team (runtime) |
| Required approvers | Architecture, security (mandatory), test platform owners |
| Upstream dependencies | AppConfig session API; integration-test CI; L01 resolver |
| Downstream dependencies | L08 evidence; L07 corroboration |
| Authoritative sources | Lineage HLD §6; Lineage LLD §§2.4, 4.3; canvas frame 2c; Risk R4 |

## 2. Purpose and Outcomes

L06 watches instrumented integration-test runs and records which datasets and
elements were actually read and written — names, operations, counts, and
schema fingerprints, never values. It proves execution, not intent.

Measurable outcomes:

- Zero observations accepted without a live signed session; zero from
  production (weekly hard-deny probe must fail to inject — Test Suite §6).
- 100% of accepted observations carry resolver-validated URNs and
  metadata-shaped payloads.
- Corroboration measurably raises bands: seeded runs move LLM-only edges to
  MEDIUM (Test Suite §4 runtime scenario).
- Per-repo corroboration coverage is reported; absence never demotes an edge.

## 3. Scope and Non-Goals

### In scope

- Session lifecycle: grant (signed JWT, scope repo+env, TTL = test budget),
  distribution, expiry, revocation.
- Phasing (decided): the OTel slice ships in Phase 1 so corroboration lifts
  confidence early; the remaining emitters follow in Phase 2.
- Four emitters: OTel auto-instrumentation + attribute extractor; custom SDK
  `lineage.emit(op, rawName, elements[])`; Spark OpenLineage driver listener;
  Dask scheduler plugin.
- Validator λ (signature, TTL, env ≠ prod, resolver pass, metadata-shape).
- Kinesis transport partitioned by dataset URN; Firehose → S3 evidence.
- Schema fingerprints: stable hash of (field names, types, order).

### Non-goals

- Production observation of any kind (hard-denied, twice).
- Claiming transforms or derivations (structural corroboration only).
- Test execution or scheduling (CI owns it).
- Analysis of any payload values (metadata-only is a contract).

## 4. Component Boundary

### Owned behavior

Session API + JWT claims contract; emitter SDKs/plugins + their conformance
kits; validator; stream topology; observation schema.

### Upstream inputs

Session requests from CI; spans/plans/task graphs from instrumented runs.

### Downstream outputs

`Observation` records (LLD §2.4) in S3, keyed (session, dataset URN);
coverage metrics per repo.

### Forbidden behavior

- Accepting an observation whose session is expired, revoked, or unsigned.
- Persisting any payload value, sample, or reversible encoding.
- Emitters claiming element lists they cannot see (only SDK and Spark may
  assert elements).
- Backfilling observations after session close.

## 5. Functional Requirements

| ID | Requirement | Priority |
|---|---|---|
| L06-FR-001 | Session grant: signed, time-boxed, scoped (repo, env); no session → no ingestion; grants audited. | P0 |
| L06-FR-002 | OTel path: attribute extractor maps span attrs (db.statement via bounded parse, messaging.destination, url.path) through L01; unmappable spans drop with a metric, never guess. | P0 |
| L06-FR-003 | Spark listener consumes the pinned OpenLineage spec; columnLineage facet when present → element-level; otherwise dataset-level. | P0 |
| L06-FR-004 | Dask plugin emits dataset-level only from task-graph IO nodes. | P0 |
| L06-FR-005 | Validator enforces, in order: signature/TTL, env ≠ prod, resolver pass, metadata shape (schema-closed: any extra field rejects). | P0 |
| L06-FR-006 | Kinesis partition key = dataset URN (ordered per dataset); no silent sampling. | P0 |
| L06-FR-007 | Fingerprints: deterministic across identical schemas; change on name/type/order change; never include values. | P0 |
| L06-FR-008 | Session close drains and writes a manifest (counts per emitter); incomplete drains marked, never silently complete. | P1 |

### Non-functional requirements

| ID | Requirement | Priority |
|---|---|---|
| L06-NFR-001 | Validator sustains peak integration-test bursts with backpressure into Kinesis, zero accepted-event loss. | P0 |
| L06-NFR-002 | Emitter overhead ≤ 5% test-run wall time at default settings. | P1 |

## 6. Data and Durable State

DynamoDB `sessions` (grant, TTL, publicKey, revoked). S3
`evidence/runtime/{session}/{datasetUrn}/{seq}.json` + session manifests.
Emitter versions are release artifacts with conformance kits.

## 7. Interfaces and Contracts

Session API (JWT claims pinned — Test Suite §5 “Runtime session”); observation
schema contract with L07; OpenLineage spec version pinned with forward-compat
fixtures (§5 “OpenLineage facets”).

## 8. Processing and State Model

`GRANT → READY(emitters attested) → OBSERVING → DRAIN → CLOSED(COMPLETE |
INCOMPLETE | EXPIRED | REVOKED)`. Only COMPLETE sessions corroborate at full
weight; INCOMPLETE never promotes confidence.

## 9. Failure Semantics

| Code | Class | Behavior |
|---|---|---|
| `NO_SESSION` / `EXPIRED` / `BAD_SIG` | DETERMINISTIC | Reject; security telemetry; never retried |
| `PROD_TARGET` | SECURITY | Reject + sev-2 alert (both IAM and validator layers) |
| `UNMAPPABLE_SPAN` | EXPECTED | Drop with metric; coverage report reflects it |
| `STREAM_THROTTLE` | TRANSIENT | Emitter buffer + retry; drain manifest reconciles counts |

## 10. Security and Privacy

Production deny implemented twice (IAM condition + validator); weekly live
probe asserts both (Test Suite §6); metadata-only enforced by closed schema;
sessions revocable centrally; all grants and rejections audited.

## 11. Observability

Observations by emitter/outcome; session outcomes; corroboration coverage per
repo; fingerprint-drift signals; prod-deny probe status (paging on success of
the attack).

## 12. Acceptance Criteria and Test Matrix

Normative suite: Test Suite §3.4 (validator, extractor, emitters,
fingerprint) + §4 runtime corroboration + §6 daily session loop and weekly
hard-deny probe. Acceptance: all four emitters pass conformance on seeded
runs; deny probe fails to inject from prod; coverage dashboard live.

## 13. Integration Obligations

- **L06↔L01:** validator uses the run's resolver pin; unresolved observations
  quarantine.
- **L06↔L07:** containment semantics (dataset-level → half-step) pinned in
  the observation contract.
- **L06↔CI:** session grant/close integrated into the integration-test
  harness with attestation.

## 14. Definition of Done

Session API + four emitters + validator deployed; conformance kits published;
seeded corroboration scenario green; hard-deny probe scheduled and paging;
coverage metrics visible per repo in L11.
