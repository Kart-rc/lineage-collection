# 13-repository-classification.md

# L13 Repository Classification and Eligibility PRD

## 1. Document Control

| Field | Value |
|---|---|
| Component | L13 |
| Status | Draft for implementation |
| Launch phase | MVP |
| Criticality | P0 — runs before any expensive analysis; baseline completeness depends on it |
| Primary owner | Lineage platform team (eligibility) |
| Required approvers | Architecture, governance (exclusion policy) |
| Upstream dependencies | Catalog/repo metadata, TAS associations, CI/CD artifact evidence, manifests, SBOM/locks, CloudWatch deploy association |
| Downstream dependencies | L03 scheduling; L04 path scoping; L11 (unknown queue) |
| Authoritative sources | v3 deck slide 06 (normative class + precedence tables); readiness review G-L03-1 (Q4 → new component) |

## 2. Purpose and Outcomes

L13 decides what each repository (or monorepo path) *is* before anything
costly runs: its class, its baseline treatment, and its on-change treatment.
UNKNOWN is never silent.

Measurable outcomes:

- 100% of inventoried repos carry a decision: included, excluded (with
  reason/evidence/owner/policy version), path-level MIXED, or UNKNOWN in the
  review queue.
- A critical active repository cannot remain UNKNOWN at baseline completion
  (blocks, per L03-FR-002).
- Same inputs + same policy version → identical decision (replay test).
- Policy preview enumerates 100% of decision changes with no side effects.

## 3. Scope and Non-Goals

### In scope

- Evidence precedence (normative, 7 levels): 1 governed catalog/repo
  metadata · 2 TAS association · 3 CI/CD deployable-artifact evidence ·
  4 deterministic manifests & structure · 5 SBOM/dependency/lock files ·
  6 CloudWatch deployment association · 7 heuristics (require review).
- Class taxonomy (normative, 9): APPLICATION_RUNTIME (included → incremental
  lineage) · DATA_PIPELINE (included → incremental + native) ·
  CONTRACT_SCHEMA_SOURCE (metadata-only → re-evaluate producers/consumers) ·
  SHARED_LIBRARY (excluded → re-analyze affected consumers) ·
  INFRASTRUCTURE (excluded → evaluate bindings, trigger apps) ·
  DOCUMENTATION (excluded → NO_LINEAGE_IMPACT) · TEST_AUTOMATION (excluded →
  update coverage/test mappings) · MIXED_MONOREPO (path-level → route each
  changed path) · UNKNOWN (never silent → quarantine for review).
- Guardrails: an LLM can propose but never exclude; evidence conflicts →
  UNKNOWN; overrides expire; exclusions carry reason, evidence, owner, policy
  version.
- Decision records, effective registry, policy versioning + preview mode.

### Non-goals

- Performing analysis (classification reads structure, never executes).
- Lane assignment mechanics (L03 consumes the decision).
- Ownership resolution (TAS is an input).

## 4. Component Boundary

### Owned behavior

Precedence engine; class policy file (versioned); decision schema; UNKNOWN
queue semantics; override lifecycle (expiring, audited).

### Upstream inputs

Inventory records; the seven evidence sources above.

### Downstream outputs

`ClassificationDecision { repoOrPath, class, baselineTreatment,
onChangeTreatment, evidenceLevelUsed, evidenceRefs, policyVersion, decidedAt,
expiry? }` + effective-registry index.

### Forbidden behavior

- Excluding on LLM or heuristic evidence alone.
- Silent UNKNOWN (must enter the review queue with evidence).
- Non-expiring manual overrides.
- Classifying from interaction context alone.

## 5. Functional Requirements

| ID | Requirement | Priority |
|---|---|---|
| L13-FR-001 | Evaluate evidence strictly in precedence order; the first decisive level wins and is recorded; conflicts within a level → UNKNOWN. | P0 |
| L13-FR-002 | Emit the full decision record with evidence refs; decisions are immutable history (new decision supersedes, never edits). | P0 |
| L13-FR-003 | MIXED_MONOREPO produces per-path decisions; changed-path routing exposed to L03/L04. | P0 |
| L13-FR-004 | UNKNOWN enters a review queue in L11; resolution writes a governed override or a policy improvement. | P0 |
| L13-FR-005 | Policy is a versioned artifact; preview mode diffs decisions across the estate with zero side effects. | P0 |
| L13-FR-006 | Re-classification triggers: inventory delta, manifest change in the repo, TAS/catalog change, policy release. | P0 |
| L13-FR-007 | Overrides expire (default 90d) and notify owners before expiry. | P1 |

### Non-functional requirements

| ID | Requirement | Priority |
|---|---|---|
| L13-NFR-001 | Classify 10,000 repos within the baseline planning window (cheap: metadata + manifests, no checkout beyond shallow file listing). | P0 |
| L13-NFR-002 | Decision replay determinism across policy-pinned runs. | P0 |

## 6. Data and Durable State

S3 decision history; DynamoDB effective registry (PK repoOrPath) + indexes by
class/review-state; policy files versioned in-repo.

## 7. Interfaces and Contracts

`ClassificationDecision` schema is a contract with L03/L04/L11; policy file
schema versioned; UNKNOWN queue API via L11.

## 8. Processing and State Model

`EVALUATED(class) | UNKNOWN → OVERRIDDEN(expiring) → RE-EVALUATED` on any
trigger; supersession chains preserved.

## 9. Failure Semantics

| Code | Class | Behavior |
|---|---|---|
| `EVIDENCE_CONFLICT` | EXPECTED | UNKNOWN + queue, evidence attached |
| `SOURCE_UNAVAILABLE` | TRANSIENT | Decide from remaining levels only if a higher level already decisive; else defer |
| `POLICY_REGRESSION` | DEFECT | Preview diff gate blocks release |

## 10. Security and Privacy

Read-only metadata access; overrides require governance role; all decisions
audited with actor + policy version.

## 11. Observability

Class distribution; UNKNOWN inflow/age (alarmed); override count/expiry;
preview diff sizes per policy release.

## 12. Acceptance Criteria and Test Matrix

Fixtures per class (incl. a mixed monorepo and a conflict case); replay
determinism; policy-preview test; baseline-blocking UNKNOWN scenario in Test
Suite §4 baseline flow. (Add L13 fixtures to Test Suite §2 seeded repos.)

## 13. Integration Obligations

- **L13↔L03:** baseline blocks on critical UNKNOWN; on-change treatments map
  to workflow branches.
- **L13↔L04:** path scoping honored (monorepo).
- **L13↔L11:** UNKNOWN queue + override flow round-trip.

## 14. Definition of Done

Precedence engine + policy v1 shipped; class fixtures green; UNKNOWN queue
live; preview demonstrated on the full inventory in staging.
