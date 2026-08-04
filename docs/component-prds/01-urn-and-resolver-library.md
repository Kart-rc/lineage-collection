# 01-urn-and-resolver-library.md

# L01 URN Grammar and Resolver Library PRD

## 1. Document Control

| Field | Value |
|---|---|
| Component | L01 |
| Status | Draft for implementation |
| Launch phase | MVP (foundation) |
| Criticality | P0 — every edge, evidence file, and projection depends on stable identity |
| Primary owner | Lineage platform team (contracts) |
| Required approvers | Architecture, data governance, catalog owners, security |
| Upstream dependencies | Enterprise catalog snapshot export; platform vocabulary registry; TAS system inventory |
| Downstream dependencies | L04, L05, L06 (intake resolution); L07 (merge keys); L10/L11 (addressing) |
| Authoritative sources | Lineage HLD §3; Lineage LLD §1; Risk register R1 (canvas 4a) |

## 2. Purpose and Outcomes

L01 gives every engine the same meaning for a dataset and an element. It owns
the URN grammar, the normalization pipeline, and deterministic resolution
against a pinned enterprise-catalog snapshot. Resolution happens at intake,
never at emit; ambiguity is surfaced, never hidden.

Measurable outcomes:

- 100% of persisted assertions carry catalog-resolved URNs or an explicit
  quarantine record with reason and candidates.
- Identical (raw name, context, resolverVersion, snapshotId) inputs produce
  byte-identical results across all embedding engines.
- URN join rate — runtime-observed datasets matching an SCA-derived URN —
  ≥ 80% at launch (platform gate R1).
- A resolver release that changes any golden-corpus output requires explicit
  approval and emits a determinant bump.

## 3. Scope and Non-Goals

### In scope

- Grammar: `urn:ldp:{env}:{platform}:{system}:{dataset}#{element}`; parsing,
  validation, normalization, rendering, comparison.
- Normalization pipeline: decode, case-fold, default-schema injection, config
  substitution, shard collapse, temp/staging elision, view mapping, catalog
  match, vocabulary check (Lineage LLD §1, normative order).
- Catalog snapshot pull, content-addressing, pinning, and staleness alarms.
- `resolve()` / batch resolution API; quarantine records and catalog-gap
  reports; golden-corpus test harness.
- Cross-env `sameAs` derivation from (platform, system, dataset).

### Non-goals

- Owning dataset definitions (enterprise catalog does).
- Deciding whether an edge is true (L07) or publishable (L09/L10).
- Repairing identity by similarity: multiple candidates are AMBIGUOUS, never a
  pick.
- Rewriting URNs inside immutable evidence on rename — renames tombstone and
  link (L07).

## 4. Component Boundary

### Owned behavior

- The normative grammar and the ordered, versioned normalization rules.
- Snapshot lifecycle: hourly pull → contract check → content-address → pin.
- Resolution determinism: (resolverVersion, snapshotId) recorded on every
  result.

### Upstream inputs

- Catalog snapshot export (schema per Test Suite §5 contract).
- Platform vocabulary (closed set; additions by PR).
- Raw names with context: {kind, value, source} + {env, system, repo, digest,
  config map}.

### Downstream outputs

- `Resolved { urn, elementUrns?, catalogRef, rulesApplied }`.
- `Quarantined { raw, reason, candidates, gapReport }`.
- Join-rate and quarantine metrics (consumed by L12 dashboards).

### Forbidden behavior

- Returning a URN not present in the pinned snapshot (guessing).
- Resolving with partial config substitution (all-or-quarantine).
- Silent case-folding of authoritative element names.
- Blocking intake when the catalog is down — last-good snapshot serves, age
  alarmed.

## 5. Functional Requirements

| ID | Requirement | Priority |
|---|---|---|
| L01-FR-001 | Parse, validate, normalize, and render the grammar with round-trip equality; env, platform, system, dataset are mandatory; element is a dataset-scoped fragment. | P0 |
| L01-FR-002 | Environment is inside identity: prod and staging URNs never compare equal; `sameAs` is derived, never stored as identity. | P0 |
| L01-FR-003 | Implement the nine-step normalization pipeline in normative order; each rule is named, versioned, and individually testable. | P0 |
| L01-FR-004 | JDBC/connection-string decode covers ports, params, IAM-auth forms, and default-schema injection from connection context. | P0 |
| L01-FR-005 | Shard collapse maps date-suffixed physical names to a template URN and carries the concrete shard as an attribute; non-date suffixes pass through. | P0 |
| L01-FR-006 | Temp/staging names matching registered pipeline patterns are elided: the edge re-routes source → final target; the temp hop is preserved in provenance only. | P0 |
| L01-FR-007 | Views resolve to their own catalog URN plus DERIVES edges to base tables per catalog definition; never silently substituted. | P0 |
| L01-FR-008 | Zero catalog matches → UNKNOWN quarantine with gap report; 2+ matches at equal precedence → AMBIGUOUS quarantine with all candidates. | P0 |
| L01-FR-009 | Every result records (resolverVersion, snapshotId); a run observes exactly one snapshot. | P0 |
| L01-FR-010 | Batch resolution preserves order, isolates invalid items, and uses one snapshot for the whole batch. | P0 |
| L01-FR-011 | Golden corpus (raw → URN pairs per platform kind) is CI-gating; diffs require approval + determinant bump. | P0 |
| L01-FR-012 | Snapshot failing the catalog contract is refused; last-good retained; refusal is alarmed, not silent. | P0 |
| L01-FR-013 | Library ships from one rule source of truth with cross-language byte-equality tests. MVP scope: Python + JVM (covers SCA + Spark); Node/Go builds in Phase 1. | P0 (MVP: py+jvm) |

### Non-functional requirements

| ID | Requirement | Priority |
|---|---|---|
| L01-NFR-001 | Cached single resolution ≤ 5 ms p95 in-process; 1,000-item batch ≤ 300 ms p95. | P0 |
| L01-NFR-002 | Snapshot age alarm at 3 h; intake continues on last-good during catalog outage. | P0 |
| L01-NFR-003 | Deterministic across languages: identical inputs → byte-identical serialized results. | P0 |

## 6. Data and Durable State

- S3: `catalog-snapshots/{snapshotId}.json` (content-addressed, immutable);
  `quarantine/{date}/{id}.json`.
- DynamoDB: alias/normalized-name lookup index keyed (kind, normalizedValue,
  snapshotId); quarantine triage queue index.
- The golden corpus lives in the resolver repo, versioned with the rules.

## 7. Interfaces and Contracts

`resolve(raw: RawName, ctx: ResolveContext) → Resolved | Quarantined`
(types normative in Lineage LLD §1). Behavioral contract = golden corpus;
engines pin a semver range (Test Suite §5 “Resolver API” contract). Quarantine
triage: `POST /quarantine/{id}/resolve` via L11 adds an alias decision to the
next snapshot build — it never mutates a pinned snapshot.

## 8. Processing and State Model

Snapshot lifecycle: `PULLED → CONTRACT_CHECKED → PINNED(ACTIVE) → SUPERSEDED`;
activation is atomic; rollback re-pins a prior snapshot as a new event.
Resolution: normalize → precedence match (exact catalog id → registered alias
→ normalized-name match) → single winner or quarantine → serialize + checksum.

## 9. Failure Semantics

| Code | Class | Behavior |
|---|---|---|
| `UNKNOWN_DATASET` | DETERMINISTIC | Quarantine + gap report; never a guessed URN |
| `AMBIGUOUS` | DETERMINISTIC | Quarantine with all candidates; human alias decision |
| `VOCAB_VIOLATION` | DETERMINISTIC | Reject; platform additions only by PR |
| `SNAPSHOT_STALE` | TRANSIENT | Serve last-good; alarm at threshold |
| `SNAPSHOT_CONTRACT_FAILED` | DETERMINISTIC | Refuse snapshot; retain last-good; alarm |

## 10. Security and Privacy

Snapshot bucket KMS-encrypted, read-only to engines; alias decisions require
governance authorization and are immutable, reviewer-attributed records; raw
names may contain connection strings — credentials are stripped at decode and
never persisted.

## 11. Observability

| Signal | Dimensions / alert |
|---|---|
| `resolver.join_rate` | per repo/day; launch gate ≥ 80%; alarm on 7-day decline |
| `resolver.quarantine_count` | by reason; triage SLA alarm |
| `resolver.snapshot_age` | alarm ≥ 3 h |
| `resolver.golden_diff` | CI signal; release blocker |

## 12. Acceptance Criteria and Test Matrix

Normative suite: Test Suite §3.1 (golden corpus, per-rule tests, quarantine
paths, snapshot pinning) plus §5 contracts (resolver API, catalog snapshot).
Acceptance: all §3.1 fixtures green; cross-language equality suite green;
join-rate instrumentation emitting in staging; a seeded dynamic-names repo
resolves per its `expected-lineage.json`.

## 13. Integration Obligations

- **L01↔L04/L05/L06:** all three engines call resolve() at intake under one
  pinned snapshot per run; no engine persists an unresolved name as identity.
- **L01↔L07:** merge keys use resolved URNs only; quarantined assertions never
  reach the ledger.
- **L01↔catalog:** snapshot contract verified hourly (live canary, Test Suite
  §6).

## 14. Definition of Done

Grammar + pipeline implemented from one rule source; golden corpus ≥ every
naming edge case in the estate inventory (temp, views, shards); Python + JVM
builds published (Node/Go in Phase 1); join-rate dashboard live; quarantine triage flow
demonstrated end-to-end on real catalog data.
