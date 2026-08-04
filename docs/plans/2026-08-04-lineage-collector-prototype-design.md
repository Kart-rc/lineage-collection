# Lineage Collector Local Prototype Design

**Date:** 2026-08-04
**Status:** Approved
**Selected approach:** PRD L16 M1 walking skeleton

## 1. Objective

Build a locally runnable prototype that demonstrates one lineage assertion moving through the platform's complete trust boundary:

`push intake → classification → static analysis → URN resolution → immutable evidence → confidence merge → human review → fenced publication → lineage and impact queries`

The prototype must preserve the behavioral invariants that make the architecture meaningful while replacing AWS infrastructure with local equivalents. It is a product and contract prototype, not an AWS deployment prototype.

## 2. Approaches Considered

### A. End-to-end walking skeleton — selected

A real local backend, durable local state, deterministic analysis, and a review/query SPA. This provides the strongest evidence that the component contracts and lifecycle fit together. It follows the M1 milestone defined in L16.

### B. UI-first simulation

A polished SPA backed by static fixtures. It would be faster to demonstrate but would not prove intake deduplication, immutability, confidence merging, proposal transitions, fencing, or impact semantics.

### C. AWS service emulation

LocalStack or similar infrastructure mirroring EventBridge, SQS, Step Functions, S3, DynamoDB, and API Gateway. This would improve infrastructure similarity but add substantial setup weight while weakening application breadth. L14 already specifies the production AWS mapping, so emulation adds little value to the prototype.

## 3. Prototype Boundary

### Implemented as working behavior

- L01: URN grammar, deterministic normalization, catalog match, and explicit quarantine.
- L02: signed demo-event intake, immutable event identity, deduplication, normalization, lane assignment, and quarantine.
- L03/L13: repository classification, a visible Incremental workflow, stage history, no-impact handling, and redrivable failure state.
- L04: deterministic Python and embedded-SQL analysis for a seeded repository, including exact evidence and residue.
- L07: idempotent assertion merge, ordinal confidence bands, corroboration as a separate field, conflicts, and provenance retention.
- L08: checksummed, write-once artifact storage with stable evidence references.
- L09: proposal lifecycle, manual approval/rejection/correction, immutable versions, and audit records.
- L10: manifest creation, monotonic fencing token, inactive namespace staging, verification, atomic active-pointer swap, and version history.
- L11: operations overview, review workflow, run timeline, lineage traversal, edge detail, and impact analysis.
- L12: correlation fields, append-only audit events, typed operational failures, and visible health/gate metrics.

### Represented through contract-faithful adapters

- L05 LLM evidence: deterministic validated fixtures exercise LLM-only and SCA+LLM confidence behavior without an enterprise model endpoint.
- L06 runtime evidence: validated metadata-only fixtures exercise complete/incomplete sessions and corroboration without requiring instrumented CI.
- AWS services: local storage and in-process orchestration model their semantics; L14 remains the production deployment specification.

### Deferred from the prototype

- Real GitHub/Jenkins, enterprise catalog, TAS, OIDC, Bedrock, OpenTelemetry, Spark, Dask, Neptune, and OpenSearch integrations.
- Baseline, PRGate, deployment promotion, and Nightly workflows beyond fixtures/read models needed to demonstrate their contracts.
- Production-scale load, DR, IAM, KMS, and multi-region deployment. Their seams and stated targets remain documented.

## 4. Architecture

The repository follows the intent of L14's monorepo while remaining small enough to run on a laptop.

```text
apps/
  api/                 FastAPI application and modular domain services
  web/                 React + TypeScript SPA
packages/
  contracts/           JSON Schemas shared by backend tests and generated TS types
fixtures/
  catalog/             versioned mock enterprise catalog snapshot
  repositories/        seeded Python/SQL repository and expected lineage
data/                   generated local state; gitignored
docs/
  plans/               design and implementation records
  prd-ambiguities.md   final ambiguity/readiness register
tests/                 cross-component and end-to-end tests
```

### Local production-equivalent mapping

| PRD service | Local implementation | Preserved semantic |
|---|---|---|
| EventBridge + SQS | in-process lane dispatcher persisted in SQLite | normalized envelope, lane, dedupe, no silent drop |
| Step Functions | explicit workflow service and stage ledger | visible stages, typed failure, idempotent redrive |
| Fargate SCA | Python analyzer service | digest-pinned deterministic evidence |
| S3 Object Lock | append-only files under a local object directory | checksum, schema version, no overwrite |
| DynamoDB | SQLite transactions and constraints | conditional writes, dedupe, versioned state, fencing |
| Neptune | versioned edge/node projection tables | active pointer, bounded traversal, disposable projection |
| CloudTrail | append-only audit-event table | actor, action, correlation, timestamp |

SQLite is used in WAL mode. Database writes are isolated behind repositories so an AWS-backed implementation can replace the adapters without changing domain behavior.

## 5. Domain Contracts

The first shared JSON contracts are:

- `EventEnvelope`
- `ClassificationDecision`
- `ResolvedName | QuarantinedName`
- `EvidenceRef`
- `ScaEvidenceFile`
- `MechanismAssertion`
- `ConsolidatedEdge`
- `Proposal` and `ApprovalRecord`
- `AcceptedLineageManifest`
- `ImpactResponse`

Every event, run, evidence artifact, proposal, audit event, and publication contains the applicable fields from the correlation contract. Unknown optional fields are omitted rather than emitted as empty strings.

The canonical edge key is a stable digest of sorted `from` URNs, `to` URN, and edge type. Provenance entries have stable IDs so replay is idempotent.

## 6. End-to-End Flow

1. A demo GitHub push is submitted with delivery ID, repository, digest, changed files, and signature.
2. Intake verifies the signature, conditionally records the delivery ID, creates a canonical envelope, and routes it to the events lane. A repeat delivery returns the original outcome and creates no duplicate run.
3. Classification evaluates fixture evidence in precedence order. The seeded repository resolves to `DATA_PIPELINE`; conflicting evidence becomes `UNKNOWN` and blocks analysis.
4. The Incremental workflow writes each stage transition to the run ledger.
5. Static analysis walks supported Python AST calls and embedded SQL in the changed scope. Exact findings include file, line, AST path, raw names, transform, and mechanism. Unsupported dynamic names become residue.
6. The resolver normalizes raw names against the run-pinned catalog snapshot. A single match emits a catalog-backed URN; zero or multiple matches emit a quarantine record and never enter the ledger.
7. The evidence store serializes the SCA artifact deterministically, computes SHA-256, and writes it once. A second write to the same key is accepted only when the checksum is identical.
8. Consolidation appends unseen provenance and derives the ordinal band from distinct mechanisms. Dataset corroboration is displayed separately and cannot change the band.
9. A proposal diff is created against the active graph version. For the walking skeleton, parser-exact edges remain in manual review so the human gate is demonstrable.
10. A reviewer inspects provenance and approves, rejects, or creates a corrected successor. Every transition is server-enforced and audited.
11. Approval creates a checksummed manifest. Publication reserves a monotonic fencing token, stages a new projection version, verifies it, and swaps the active pointer only if the expected prior version and token still match.
12. Lineage queries resolve through the active pointer. Impact analysis walks downstream at depth at most five and maps change type plus edge band to BLOCK, WARN, or INFO exactly as L11 section 15 specifies.

## 7. Confidence and Conflict Model

The prototype uses L07's pinned ordinal representation:

`LOWEST < SINGLE < MEDIUM < HIGH < HIGHEST`

- LLM alone: LOWEST
- one non-LLM mechanism: SINGLE
- SCA + LLM or runtime + LLM: MEDIUM
- SCA + runtime: HIGH
- SCA + LLM + runtime: HIGHEST

`corroboration` is independently `NONE`, `DATASET`, or `ELEMENT`. Dataset-level runtime evidence never raises a band.

Two exact/probable transforms conflict when their normalized forms differ. Conflicting assertions retain all provenance and enter review; they are never averaged.

## 8. API Surface

- `POST /api/demo/reset` — restore deterministic seed state.
- `POST /api/events/push` — process a signed push through the walking skeleton.
- `GET /api/overview` — metrics, gates, active version, queue/run summaries.
- `GET /api/runs` and `GET /api/runs/{runId}` — timeline and evidence links.
- `GET /api/proposals` and `GET /api/proposals/{proposalId}` — review queues and detail.
- `POST /api/proposals/{proposalId}/approve|reject|correct` — server-side lifecycle transitions.
- `GET /api/lineage/{urn}` — upstream/downstream traversal with depth and version pinning.
- `POST /api/impact` — bounded synchronous impact analysis.
- `GET /api/edges/{edgeKey}` — provenance and confidence detail.
- `GET /api/quarantine` — unresolved or ambiguous names.
- `GET /api/audit` — immutable action trail.

Errors use a consistent shape with `code`, `message`, `correlationId`, and optional safe details. User-visible degradation never becomes a silent pass.

## 9. User Experience

The visual direction follows the architecture deck: warm paper, near-black ink, cool teal for trusted/active state, rust for attention, and restrained graph-line motifs. The product should feel like an evidence control room rather than a generic dashboard.

Primary workspaces:

1. **Operations** — active graph version, launch-gate cards, recent runs, stage health, and the push-to-publish flow.
2. **Review Queue** — routed proposals with state, band, mechanism badges, diff counts, and age.
3. **Proposal Detail** — before/after edge diff, full provenance, citations, confidence explanation, and review actions.
4. **Lineage Explorer** — focused graph, upstream/downstream controls, version watermark, edge inspector, and change-impact simulation.
5. **Run Timeline** — stage-by-stage execution with correlation IDs, evidence refs, quarantine, and failure/redrive context.

The SPA is responsive and keyboard-accessible. Status is never encoded only by color. Reduced-motion preferences disable nonessential transitions.

## 10. Failure Semantics

- Invalid signatures, missing identities, unknown event types, unknown catalog names, and conflicting classification are deterministic quarantine outcomes.
- Replayed delivery IDs are expected duplicates, not errors.
- Unsupported code becomes explicit residue; it is not silently ignored.
- Stage failure records the failed stage and preserves completed work for redrive.
- Stale proposal bases require rebase; concurrent review decisions use conditional updates.
- Lost fencing tokens or expected-prior mismatches cannot advance the active pointer.
- Corrupt evidence fails checksum verification and is never merged or served.

## 11. Verification Strategy

Implementation follows red-green-refactor. Core domain tests are written before production behavior.

Unit and property-style coverage includes:

- URN round-trip, normalization order, quarantine, and no guessed identity.
- Signature verification, deduplication, envelope normalization, and lane policy.
- Classification precedence and conflict-to-UNKNOWN behavior.
- Deterministic SCA output and explicit residue.
- Write-once evidence and checksum verification.
- Merge idempotency, commutativity, band matrix, corroboration separation, and conflict retention.
- Proposal transition legality, immutable correction history, and concurrent decision handling.
- Monotonic fencing and stale-writer rejection.
- Impact severity matrix and traversal depth enforcement.

An API integration test drives the complete push-to-query path. A browser test drives demo reset, event collection, proposal review, publication, lineage exploration, and impact analysis. Build, lint, test, and local startup checks are required before completion.

## 12. Local Operation

The project exposes documented root commands for setup, seeded reset, development, tests, and production-like local serving. The preferred path is one command after dependency installation, with an optional Docker Compose wrapper only if it remains simpler than native execution.

Generated state lives under `data/` and is safe to recreate through the demo reset endpoint or command. Source fixtures and expected results remain versioned.

## 13. Ambiguity Handling

The final `docs/prd-ambiguities.md` will classify each finding as:

- **Enterprise context seam:** a real external value is intentionally unknown and must remain configurable.
- **Specification gap:** behavior is named but lacks an implementable table, schema, or algorithm.
- **Cross-document inconsistency:** two supplied sources prescribe different values or phases.
- **Prototype assumption:** a reversible local decision made to keep the walking skeleton runnable.

Each entry will cite the affected PRD section, explain implementation impact, record the prototype decision, and name the owner or decision needed for production.
