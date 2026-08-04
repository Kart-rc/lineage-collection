# 11-apis-review-ui-and-pr-gate.md

# L11 Query APIs, Review UI, and PR-Gate Experience PRD

## 1. Document Control

| Field | Value |
|---|---|
| Component | L11 |
| Status | Draft for implementation |
| Launch phase | MVP (query API, review SPA) · P1 (PR-gate rendering, OpenSearch discovery) |
| Criticality | P0 — where trust is experienced |
| Primary owner | Lineage platform team (experience) |
| Required approvers | Architecture, UX, security (RBAC) |
| Upstream dependencies | L10 pointers/projections; L09 lifecycle; L07 edge detail; L03 run ledger |
| Downstream dependencies | End users; CI (PR checks) |
| Authoritative sources | Lineage HLD §§1, 8; Lineage LLD §6; v3 deck slides 14/14B; PRD §3 use cases |

## 2. Purpose and Outcomes

L11 exposes approved lineage, the review workflow, quarantine triage, the run
timeline, and the PR-gate verdict — always with provenance and confidence
visible, never implying more certainty than the band supports.

Measurable outcomes:

- Bounded one-hop query ≤ 2 s p95; depth-limited traversal enforced.
- Every rendered edge shows band + mechanisms + evidence links (no bare
  edges).
- PR verdicts render affected consumers with the environment baseline named;
  only high-confidence edges block.
- Reviewers complete a proposal decision without leaving the SPA (evidence
  inline).

## 3. Scope and Non-Goals

### In scope

- Query API (LLD §6): lineage traversal, impact, edge detail — active
  namespace by default, explicit version pinning for history.
- Review SPA: queues (proposals, CONFLICTING, corrections, quarantine,
  audit samples), proposal diff view, correction flow, approval.
- Run timeline per repo/system (from L03 ledger).
- PR-gate check rendering (GitHub check output) with verdict classes per v3
  slide 14B.
- Coverage and gate dashboards (join rate, corroboration %, queue depth).

### Non-goals

- Authorization semantics beyond enforcement (policy from governance).
- Graph writes of any kind.
- Discovery search before P1 (OpenSearch deferral).

## 4. Component Boundary

### Owned behavior

API Gateway + SPA; response shapes; RBAC enforcement points; verdict
rendering rules; dashboard definitions.

### Upstream inputs

Pointer-resolved projections; proposal states; edge ledger reads; run ledger.

### Downstream outputs

Human decisions (to L09); PR check results (to GitHub); triage actions
(to L01 alias flow).

### Forbidden behavior

- Serving stale pointers silently (projection lag must be exposed).
- Rendering an edge without band + provenance.
- Client-side state transitions not backed by server state.
- Unbounded traversals.

## 5. Functional Requirements

| ID | Requirement | Priority |
|---|---|---|
| L11-FR-001 | `GET /lineage/{urn}` up/down, depth ≤ 5, env-scoped, active namespace via pointer; version param pins history. | P0 |
| L11-FR-002 | `GET /impact/{urn}?change=` returns affected consumers with bands; used read-only by the PR gate. | P0 |
| L11-FR-003 | Edge detail exposes full provenance (mechanisms, citations, sessions, rejects) and confidence axes. | P0 |
| L11-FR-004 | Review queues with ownership routing; proposal diff (added/removed/band-changed) with before/after; correction + approval flows per L09 contract. | P0 |
| L11-FR-005 | Quarantine triage view (URN + LLM rejects) with alias-decision handoff to L01. | P0 |
| L11-FR-006 | PR check output: verdict classes (pass, warn with consumers, block on high-confidence break), env baseline named, superseded runs collapsed. | P1 |
| L11-FR-007 | Run timeline: every stage of every run visible with correlation links to evidence. | P1 |
| L11-FR-008 | Gate dashboards: join rate, cache hit, audit disagreement, queue depth, corroboration coverage. | P0 |

### Non-functional requirements

| ID | Requirement | Priority |
|---|---|---|
| L11-NFR-001 | One-hop ≤ 2 s p95; proposal view ≤ 2 s p95; SPA initial load ≤ 3 s p95. | P0 |
| L11-NFR-002 | RBAC: domain-scoped reviewer actions; evidence reads ABAC-audited. | P0 |

## 6. Data and Durable State

Stateless services; read models from L03/L07/L09/L10; dashboard definitions
versioned in-repo.

## 7. Interfaces and Contracts

API shapes per LLD §6 — Pact contracts with the SPA and the PR-gate renderer
(Test Suite §5 “Review & Query APIs”); pagination + error shapes included.
GitHub check API payloads pinned as fixtures.

## 8. Processing and State Model

UI states mirror server states 1:1 (L09 lifecycle, L03 run states); the SPA
holds no authoritative state.

## 9. Failure Semantics

| Code | Class | Behavior |
|---|---|---|
| `PROJECTION_LAG` | EXPECTED | Response includes watermark; UI banners staleness |
| `DEPTH_EXCEEDED` | DETERMINISTIC | 400 with guidance; never partial-silent |
| `UNAUTHORIZED_DOMAIN` | SECURITY | 403 + audit; no existence leak of unauthorized systems |
| `UPSTREAM_TIMEOUT` | TRANSIENT | Degraded read with retry hint; PR gate falls to WARN, never silent PASS |

## 10. Security and Privacy

Cognito OIDC SSO; domain RBAC on review actions; evidence access audited;
PR-gate tokens scoped to check-write only; no payload values exist anywhere
to leak (metadata-only platform contract).

## 11. Observability

API latency/error budgets; queue SLA views; PR-gate verdict distribution;
dashboard freshness.

## 12. Acceptance Criteria and Test Matrix

Normative suite: Test Suite §4 PR-gate scenario + §5 API pacts + §6
Neptune/OpenSearch smoke. Acceptance: pacts green both sides; PR-gate render
matrix (all verdict classes) fixture-tested; a reviewer completes an
end-to-end decision on seeded data.

## 13. Integration Obligations

- **L11↔L09/L10/L03:** contracts above; no direct table reads outside read
  models.
- **L11↔GitHub:** check payload fixtures per provider version.
- **L11↔L01:** triage → alias decision handoff round-trips.

## 14. Definition of Done

Query API + SPA + PR check live on staging with seeded data; all pacts in CI;
gate dashboards populated from real telemetry; accessibility pass on the
review flows.

## 15. Impact Analysis Specification (closes G-L11-1; decisions Q2/Q3)

**Change types (all covered):** COLUMN_DROP · COLUMN_TYPE_CHANGE ·
COLUMN_RENAME · DATASET_REMOVAL · TRANSFORM_CHANGE ·
FINGERPRINT_DRIFT (runtime-detected declared-vs-executed schema mismatch).

**Traversal (Q3):** bounded synchronous downstream traversal (depth ≤ 5) for
interactive + PR-gate use, AND a full-closure asynchronous job
(`POST /impact-jobs`) that walks the complete downstream closure and
notifies (webhook + UI) on completion. Both traverse the active namespace via
the pointer; version pinning supported.

**Severity mapping (band-gated):**

| Change type | Edge band HIGH/HIGHEST | MEDIUM | SINGLE/LOWEST |
|---|---|---|---|
| COLUMN_DROP, COLUMN_TYPE_CHANGE, DATASET_REMOVAL | BLOCK | WARN | INFO |
| COLUMN_RENAME, TRANSFORM_CHANGE | WARN | WARN | INFO |
| FINGERPRINT_DRIFT | WARN + review item | WARN | INFO |

BLOCK is only ever produced from the band (never from corroboration badges);
LLM-only (LOWEST) edges can never BLOCK — consistent with L07 §15.

**Response schema:**

```json
{ "subject": "urn:…#element", "changeType": "COLUMN_DROP",
  "namespaceVersion": "…", "depthSearched": 5, "truncated": false,
  "affected": [ { "urn": "urn:…#field", "system": "payments",
      "owner": "team-x", "band": "HIGH", "corroboration": "ELEMENT",
      "pathLength": 2, "viaEdges": ["edgeKey…"], "severity": "BLOCK" } ],
  "summary": { "block": 1, "warn": 4, "info": 7 } }
```

PR-gate verdict = max severity over affected set, rendered with the env
baseline named and evidence links per affected consumer.
