# 10-fenced-publication-and-projections.md

# L10 Fenced Publication and Graph Projections PRD

## 1. Document Control

| Field | Value |
|---|---|
| Component | L10 |
| Status | Draft for implementation |
| Launch phase | MVP (Neptune) · P1 (OpenSearch) |
| Criticality | P0 — the single writer; correctness of the visible graph |
| Primary owner | Lineage platform team (publication) |
| Required approvers | Architecture, operations |
| Upstream dependencies | L09 approvals; L08 manifests; L03 promotion λ (pointer contract) |
| Downstream dependencies | L11 reads; deploy promotion |
| Authoritative sources | Lineage HLD §§8–9; Lineage LLD §3.3; v3 deck slide 13; DE simplification P2/P7 |

## 2. Purpose and Outcomes

L10 turns an approved manifest into a new immutable graph namespace and
atomically re-points the active version — fenced, single-writer, rebuildable.

Measurable outcomes:

- Zero split-brain publishes under contention (fencing property test + chaos
  drill).
- 95% of approved changes queryable within 60 s of manifest acceptance.
- A deleted projection is fully rebuilt from manifests with zero divergence
  (quarterly drill).
- Rollback to a prior version is a pointer event, never a data rewrite.

## 3. Scope and Non-Goals

### In scope

- Publication protocol: write manifest → reserve + fence → stage inactive
  namespace → verify counts/checksums → conditional pointer swap.
- Versioned Neptune namespaces (nodes/edges per LLD §3.3); SAME_AS and
  TOMBSTONE link maintenance.
- Active-pointer contract (shared with the deploy-promotion λ).
- OpenSearch projection (P1): rebuild per version from the manifest,
  watermarked; deferred from MVP with zero rework (DE P7).

### Non-goals

- Deciding what publishes (L09).
- Serving queries (L11 reads through the pointer).
- Storing anything unrecoverable — projections are disposable by design.

## 4. Component Boundary

### Owned behavior

`AcceptedLineageManifest` format; publish lock + fencing tokens; namespace
lifecycle; pointer transaction; rebuild tooling.

### Upstream inputs

Approval records + edge sets from L09; prior active version.

### Downstream outputs

Active pointer per env; namespace versions; publish audit; rebuild reports.

### Forbidden behavior

- Writing into an active namespace (stage-then-swap only).
- Advancing the pointer without a valid token and expected prior version.
- Deleting namespace history inside the retention window.
- Any writer other than this component touching projections.

## 5. Functional Requirements

| ID | Requirement | Priority |
|---|---|---|
| L10-FR-001 | Manifest is checksummed and names proposal version, reviewer/policy, expected prior graph version, and full edge enumeration (or delta + base). | P0 |
| L10-FR-002 | Reserve-and-fence: conditional DynamoDB lock with monotonic token; an expired worker cannot win (property test). | P0 |
| L10-FR-003 | Stage into an inactive namespace; verify counts + checksums against the manifest before swap. | P0 |
| L10-FR-004 | One DynamoDB transaction advances the pointer iff (reservation, token, expected prior) all match; failure discards staging. | P0 |
| L10-FR-005 | Rollback re-points to a prior namespace as a new audited event. | P0 |
| L10-FR-006 | Deploy promotion consumes the same pointer contract: env-truth re-point by artifact digest (merged ≠ running). | P1 |
| L10-FR-007 | OpenSearch refreshes from the active version with a watermark; queries expose projection lag. | P1 |
| L10-FR-008 | Rebuild tool reconstructs any namespace from manifests alone; divergence is a defect. | P0 |

### Non-functional requirements

| ID | Requirement | Priority |
|---|---|---|
| L10-NFR-001 | Median incremental publish (stage→swap) ≤ 5 min. | P0 |
| L10-NFR-002 | Bounded one-hop traversal on the active namespace ≤ 2 s p95 (sizing input to L11). | P0 |

## 6. Data and Durable State

DynamoDB `publish_lock`, `pointers`. S3 `manifests/{env}/{version}.json`
(Object Lock). Neptune namespaces; OpenSearch indexes per version (P1).

## 7. Interfaces and Contracts

Publish invoked only as the workflow tail (L03) with an approval record
(L09 contract). Pointer read contract consumed by L11 and the promotion λ.

## 8. Processing and State Model

`MANIFEST_WRITTEN → RESERVED(fenced) → STAGED → VERIFIED → ACTIVE |
DISCARDED`. Namespace states: `STAGING → ACTIVE → PRIOR → EXPIRED(retention)`.

## 9. Failure Semantics

| Code | Class | Behavior |
|---|---|---|
| `FENCE_LOST` | EXPECTED | Worker aborts silently; staging discarded; redrive restages |
| `VERIFY_MISMATCH` | DETERMINISTIC | No swap; manifest vs staging diff attached; defect |
| `POINTER_CONFLICT` | EXPECTED | Expected-prior mismatch → rebase via L09; no force |
| `PROJECTION_LOSS` | RECOVERABLE | Rebuild from manifests; watermark exposes staleness meanwhile |

## 10. Security and Privacy

Publisher role is the only principal with projection write; pointer table
writes restricted to publisher + promotion λ (condition-scoped); manifests
Object-Locked; all swaps CloudTrail-audited.

## 11. Observability

Publish outcomes + durations; pointer age per env; projection lag watermark;
rebuild drill results; fence-contention counts.

## 12. Acceptance Criteria and Test Matrix

Normative suite: Test Suite §3.6 (publisher unit: fencing property,
stage-verify-flip) + §4 deploy promotion + replay/rebuild + §7 weekly chaos
(publish-lock contention). Acceptance: contention chaos green; rebuild
divergence = 0; rollback drill on staged env.

## 13. Integration Obligations

- **L10↔L09:** manifest ↔ approval 1:1; nothing unapproved stages.
- **L10↔L03:** promotion λ uses the pointer contract exclusively.
- **L10↔L11:** reads always through the pointer; version pinning honored for
  historical queries.

## 14. Definition of Done

Protocol implemented with property tests; namespaces + pointer live; rebuild
tool + drill evidence; OpenSearch P1 plan validated as zero-rework (schema
derived from manifest only).
