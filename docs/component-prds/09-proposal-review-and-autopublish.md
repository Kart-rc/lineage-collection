# 09-proposal-review-and-autopublish.md

# L09 Proposal, Human Review, and Auto-Publish Policy PRD

## 1. Document Control

| Field | Value |
|---|---|
| Component | L09 |
| Status | Draft for implementation |
| Launch phase | MVP (review AND auto-publish with sampled audit — baseline volume makes human review of parser-exact edges impractical; decided in readiness review Q8) |
| Criticality | P0 — the human gate and its economics |
| Primary owner | Lineage platform team (review) |
| Required approvers | Architecture, data governance (auto-publish policy), UX |
| Upstream dependencies | L07 consolidated edges; L01 quarantine triage |
| Downstream dependencies | L10 manifests; calibration corpus; L11 review UI |
| Authoritative sources | Lineage HLD §7; Lineage LLD §2.6; v3 deck slide 12; Risk R3; PRD FR-8 |

## 2. Purpose and Outcomes

L09 owns the proposal lifecycle, corrections, approval records, the sampled-
audit auto-publish policy for parser-exact edges, and the calibration corpus
every review decision feeds.

Measurable outcomes:

- Review queue holds only LLM-only, CONFLICTING, and corrections; queue depth
  stays below reviewer capacity (gate R3).
- Auto-publish sampled-audit disagreement < 2%, else the class narrows
  automatically (policy, not manual intervention).
- Corrections never mutate: every change is a new immutable version with full
  history.
- 100% of review decisions land in the calibration corpus with labels.

## 3. Scope and Non-Goals

### In scope

- Proposal states: DRAFT → IN_REVIEW → (APPROVED | REJECTED | SUPERSEDED) →
  FINALIZED; immutable versioning.
- Corrections API semantics (amend/remove → edge v+1 + proposal v+1).
- Auto-publish policy: parser-exact class, 5% weekly random audit, automatic
  narrowing on disagreement breach; optional PII-dataset exclusion flag.
- Reviewer routing by system ownership (TAS join key).
- Calibration corpus writes (labels, rationale, evidence links).

### Non-goals

- Rendering the UI (L11) or writing the graph (L10).
- Deciding bands (L07).
- Quarantine content decisions (L01 semantics; L09 routes triage work).

## 4. Component Boundary

### Owned behavior

Proposal/approval schemas; the auto-publish policy engine (versioned,
audited); audit-sample selection (seeded random, reproducible); corpus
format.

### Upstream inputs

Consolidated edges with auto-publishable stamps; reviewer actions; audit
verdicts.

### Downstream outputs

`Proposal` records; `AcceptedApproval` records (input to L10 manifests);
narrowing decisions; corpus entries.

### Forbidden behavior

- Editing a submitted proposal in place.
- Auto-publishing anything without the parser-exact stamp or during a
  narrowing state.
- Discarding rejected/superseded history.
- Approving without recorded reviewer identity and rationale option.

## 5. Functional Requirements

| ID | Requirement | Priority |
|---|---|---|
| L09-FR-001 | Proposal creation from merge output is deterministic against an expected base graph version; diffs (added/removed/band-changed) computed and displayed. | P0 |
| L09-FR-002 | State machine enforced server-side; every transition audited with actor + correlation. | P0 |
| L09-FR-003 | Correction creates edge v+1 and proposal v+1; original retained; supersession linked bidirectionally. | P0 |
| L09-FR-004 | Auto-publish: parser-exact stamped edges bypass review into the manifest, flagged AUTO; sample of 5%/week routed to audit queue. Active from MVP; governance sign-off on the policy file is a launch gate. | P0 |
| L09-FR-005 | Audit disagreement ≥ 2% (rolling 4 weeks) → class narrows (e.g. exclude a rule pack or dataset class) automatically with an audited policy event. | P0 |
| L09-FR-006 | Reviewer routing by owning system; escalation path for unowned datasets (catalog gap). | P0 |
| L09-FR-007 | Every decision (approve/reject/correct/audit verdict) writes a corpus record with label, evidence refs, band at decision time. | P0 |
| L09-FR-008 | Proposals expose provenance verbatim — a reviewer can always see which mechanism said what, incl. LLM citations and rejects. | P0 |

### Non-functional requirements

| ID | Requirement | Priority |
|---|---|---|
| L09-NFR-001 | Proposal detail assembly ≤ 2 s p95 (refs resolved lazily). | P0 |
| L09-NFR-002 | Queue metrics near-real-time (≤ 60 s lag) for capacity management. | P1 |

## 6. Data and Durable State

S3: `proposals/{system}/{proposalId}/v{n}.json`, `approvals/`,
`calibration/`. DynamoDB: proposal lifecycle state + queue indexes
(by system, state, age).

## 7. Interfaces and Contracts

Review API endpoints (LLD §6) — Pact contracts with L11 (Test Suite §5).
Approval record schema is the input contract to L10 manifests.

## 8. Processing and State Model

Normative in v3 deck slide 12 + LLD §2.6. Auto-publish policy is itself
versioned state: `ACTIVE(scope) → NARROWED(scope′) → RESTORED` with audited
transitions.

## 9. Failure Semantics

| Code | Class | Behavior |
|---|---|---|
| `STALE_BASE_VERSION` | EXPECTED | Proposal rebased against new active version; reviewer notified |
| `CONCURRENT_DECISION` | DETERMINISTIC | Conditional write loses → actor sees fresh state; no double-apply |
| `AUDIT_BREACH` | POLICY | Automatic narrowing event + governance notification |
| `UNOWNED_SYSTEM` | EXPECTED | Escalation queue + catalog gap report |

## 10. Security and Privacy

Reviewer authorization scoped by system ownership (OIDC groups); approval
records signed; separation of proposer/approver configurable; all decisions
immutable and audited.

## 11. Observability

Queue depth vs capacity (gate R3); time-to-decision; auto-publish volume +
audit disagreement (gate < 2%); correction rate by mechanism (calibration
signal).

## 12. Acceptance Criteria and Test Matrix

Normative suite: Test Suite §4 conflict & correction scenario + §5 review API
contracts. Acceptance: lifecycle property tests (no lost history under
concurrency); narrowing simulation demonstrates automatic breach response;
corpus completeness check (every decision labeled).

## 13. Integration Obligations

- **L09↔L07:** correction round-trip produces edge v+1 and re-merge.
- **L09↔L10:** only APPROVED (or AUTO within active policy) reaches a
  manifest.
- **L09↔L11:** UI states map 1:1 to server states; no client-side-only
  transitions.

## 14. Definition of Done

Lifecycle + corrections + auto-publish policy deployed; audit sampling
reproducible; narrowing drill executed; calibration corpus feeding measurable
reviewer-agreement stats.
