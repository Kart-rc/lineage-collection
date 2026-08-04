# 07-consolidation-and-confidence.md

# L07 Consolidation, URN Merge, and Confidence PRD

## 1. Document Control

| Field | Value |
|---|---|
| Component | L07 |
| Status | Draft for implementation |
| Launch phase | MVP |
| Criticality | P0 — the architectural seam; everything downstream consumes its output |
| Primary owner | Lineage platform team (trust engine) |
| Required approvers | Architecture, data governance |
| Upstream dependencies | L04/L05/L06 evidence (via L08); L01 URNs; enterprise catalog schemas |
| Downstream dependencies | L09 proposals; L11 (edge detail reads) |
| Authoritative sources | Lineage HLD §7; Lineage LLD §5 (merge algorithm, normative); canvas frames 1a/1b/2d; Risks R2/R4 |

## 2. Purpose and Outcomes

L07 unions assertions from the three engines by URN triple, computes
agreement-based confidence on two axes, and manages the full edge lifecycle —
late arrival, disagreement, decay, rename, re-merge, and the proposal handoff.
It never destroys information and never writes the graph.

Measurable outcomes:

- Merge is idempotent: replaying any assertion N times yields one provenance
  entry and a stable version.
- Every band is derivable from provenance alone (audit re-computation
  matches).
- Disagreements surface as CONFLICTING in the review queue within one merge
  cycle — zero silent averaging.
- Cross-repo edges appear once both endpoints' repos are analyzed, with both
  provenances (stitching test).

## 3. Scope and Non-Goals

### In scope

- Edge ledger keyed hash(from[], to, type); union + provenance append.
- Band computation: 3-of-3 HIGHEST · SCA+runtime HIGH · +LLM MEDIUM · single
  SINGLE · LLM-only LOWEST; two axes (structural, derivational).
- Catalog-schema containment: dataset-level observation → half-step boost for
  listed column edges, marked "dataset".
- Transform selection (exact > probable); contradiction → CONFLICTING.
- Decay (STALE after N silent cycles), rename (tombstone + SAME_AS),
  determinant-bounded re-merge, published-edge re-open as proposal v+1.
- Join-rate computation (feeds the R1 gate).

### Non-goals

- Judging evidence quality beyond mechanism class (engines own validity).
- Publishing (L10) or approving (L09).
- Identity resolution (L01) — quarantined assertions never arrive here.

## 4. Component Boundary

### Owned behavior

The merge algorithm (LLD §5 pseudocode is normative); the consolidated-edge
schema (LLD §2.5); band tables; lifecycle status machine.

### Upstream inputs

Versioned evidence files (schema-checked); catalog schemas for containment.

### Downstream outputs

`ConsolidatedEdge` records; proposal attachments; review-queue entries;
join-rate + corroboration metrics.

### Forbidden behavior

- Deleting or rewriting a provenance entry (append-only).
- Averaging contradictory transforms.
- Demoting on absence (only N-cycle decay).
- Writing Neptune/OpenSearch directly.

## 5. Functional Requirements

| ID | Requirement | Priority |
|---|---|---|
| L07-FR-001 | onAssertion is idempotent by provenanceId; ordering across engines does not change the final record (commutativity test). | P0 |
| L07-FR-002 | Band computed from distinct mechanisms present; containment boosts are half-step and marked; LLM-only caps at LOWEST. | P0 |
| L07-FR-003 | Contradiction detection: transform conflict, or SCA WRITE with runtime-observed absence across COMPLETE sessions → CONFLICTING → review queue; provenance intact. | P0 |
| L07-FR-004 | Late arrival on a PUBLISHED edge emits proposal v+1 (band change) through the normal gate; never a direct projection write. | P0 |
| L07-FR-005 | Decay: mechanism silent N consecutive analyses → its provenance marked stale, band recomputed, status STALE; never deleted. | P0 |
| L07-FR-006 | Rename: tombstone old URN, mint new, link SAME_AS, carry human corrections forward by catalogRef. | P0 |
| L07-FR-007 | Re-merge triggers (new evidence, ruleset/prompt/resolver bump, registry change, accepted correction) touch only affected URN triples. | P0 |
| L07-FR-008 | Parser-exact class computed here (exact transform + SCA provenance + no conflict) and stamped for L09 auto-publish. | P0 |
| L07-FR-009 | Emit join-rate and per-repo corroboration coverage metrics. | P0 |

### Non-functional requirements

| ID | Requirement | Priority |
|---|---|---|
| L07-NFR-001 | Merge stage for a median incremental scope ≤ 60 s p95. | P0 |
| L07-NFR-002 | Ledger sustains 10k repos × daily runs without hot partitions (edgeKey distribution test). | P0 |

## 6. Data and Durable State

DynamoDB `edge_ledger` (PK edgeKey, SK version; GSIs: to#type, status,
system). All inputs already immutable in S3; the ledger is rebuildable by
replaying evidence (drill required).

## 7. Interfaces and Contracts

Consumes evidence schemas (contract with L04/L05/L06 — reject unknown
versions). Produces consolidated-edge schema (contract with L09/L11).
Containment semantics pinned in the observation contract.

## 8. Processing and State Model

Edge status: `PROPOSED → (AUTO_PUBLISHED | PUBLISHED) → STALE → TOMBSTONED`,
with `CONFLICTING` reachable from any active state. LLD §5 pseudocode is the
normative algorithm.

## 9. Failure Semantics

| Code | Class | Behavior |
|---|---|---|
| `UNKNOWN_EVIDENCE_VERSION` | DETERMINISTIC | Reject file; producer contract broken; alarm |
| `LEDGER_CONDITIONAL_FAIL` | TRANSIENT | Retry with fresh read (optimistic concurrency) |
| `CONTAINMENT_SCHEMA_MISSING` | EXPECTED | No boost; metric; catalog gap report |
| `REBUILD_DIVERGENCE` | DEFECT | Replay drill mismatch is a release blocker |

## 10. Security and Privacy

Ledger writes only from workflow roles; review-queue routing carries no
evidence bodies, only refs; band recomputation audit runs under read-only
role.

## 11. Observability

Bands distribution; CONFLICTING inflow; decay counts; re-merge volume by
trigger; join rate; commutativity canary (shadow re-merge comparison).

## 12. Acceptance Criteria and Test Matrix

Normative suite: Test Suite §3.5 (idempotency, band matrix, conflict, decay,
rename, publish interaction) + §4 cross-repo stitching and runtime
corroboration scenarios. Acceptance: full band matrix table-driven test
green; ledger-rebuild drill divergence = 0.

## 13. Integration Obligations

- **L07↔L08:** every provenance entry references immutable evidence.
- **L07↔L09:** proposal attachments carry auto-publishable stamps; corrections
  round-trip as edge v+1.
- **L07↔L10:** publish status transitions only via manifest feedback, never
  self-asserted.

## 14. Definition of Done

Algorithm implemented per LLD §5 with commutativity + idempotency proofs in
CI; band matrix exhaustively tested; lifecycle drills (decay, rename, re-open)
demonstrated on seeded data; rebuild drill green.

## 15. Pinned Decisions (readiness review, 2026-08-04)

- **Confidence representation (Q1):** ordinal bands only at MVP —
  LOWEST < SINGLE < MEDIUM < HIGH < HIGHEST. No numeric score is stored or
  rendered until the calibration corpus supports a governed mapping (P2+).
- **Containment made precise (closes G-L07-1):** the band is computed ONLY
  from element-level assertions. Dataset-level runtime containment never
  changes the band; it sets a separate field
  `corroboration: NONE | DATASET | ELEMENT` on the edge. Display always shows
  band + corroboration badge. Policy hooks (PR warnings) MAY treat
  (SINGLE, DATASET) as MEDIUM-equivalent for WARN rendering, but BLOCK
  decisions read the band alone.
- **Decay constant (Q6, closes G-L07-2):** N = 3 consecutive silent analyses
  of the asserting mechanism → provenance marked stale, band recomputed,
  status STALE. Config-seam constant `decay.n=3`.
- **Contradiction predicate (closes G-L07-3):** two transform assertions
  conflict iff both are class exact|probable AND their normalized forms
  differ. Normalization: parse with sqlglot/AST where parseable and compare
  canonical trees; else compare after whitespace collapse, case-fold of
  keywords (never identifiers), and paren normalization. Runtime never
  asserts transforms and therefore never triggers transform conflict; the
  runtime conflict case remains SCA-WRITE vs observed-absence across ≥ N
  COMPLETE sessions covering the writing path.
