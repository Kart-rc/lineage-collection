# 04-sca-engine.md

# L04 Static Code Analysis Engine PRD

## 1. Document Control

| Field | Value |
|---|---|
| Component | L04 |
| Status | Draft for implementation |
| Launch phase | MVP |
| Criticality | P0 — the deterministic backbone; auto-publish and the PR gate depend on it |
| Primary owner | Lineage platform team (analysis) |
| Required approvers | Architecture, security (per-run credentials) |
| Upstream dependencies | L03 task specs; L01 resolver; SCM read access |
| Downstream dependencies | L08 evidence store; L05 (residue handoff); L07 merge |
| Authoritative sources | Lineage HLD §4; Lineage LLD §§2.2, 4.1; canvas frames 2a/2e; Test Suite §§2, 3.2 |

## 2. Purpose and Outcomes

L04 turns a repository at an exact digest into exact, provable lineage edges
plus an explicit residue list of what it could not resolve. Deterministic —
it never guesses.

Measurable outcomes:

- Same (digest, ruleset, config) → byte-identical evidence file (CI-enforced
  replay test).
- Every seeded matrix repo (Test Suite §2) produces its `expected-lineage.json`
  edges with correct file:line + AST-path evidence.
- 100% of unresolved spans appear in residue with a reason code and nearby
  facts — nothing silently skipped.
- Parser-exact edges qualify for the L09 auto-publish class.

## 3. Scope and Non-Goals

### In scope

- Checkout at digest with per-run scoped read-only credentials.
- Manifest-driven language detection and rule-pack routing (package.json,
  go.mod, pom.xml/build.gradle, requirements.txt/pyproject.toml).
- Deterministic parsers: sqlglot (embedded SQL, dialect matrix), dbt manifest,
  OpenAPI/proto.
- AST walk: tree-sitter grammars + rule packs (Python, Java/Kotlin, JS/TS, Go)
  producing Candidates with evidence.
- Config resolution of dynamic names (all-or-residue).
- Residue emission with reason codes: dynamic-name, reflection,
  unparseable-type.

### Non-goals

- Cross-repo resolution (repo-local by design; L07 stitches by URN).
- Inference of any kind (L05).
- Deciding confidence or publishing (L07/L09/L10).
- Running user code — analysis is static only.

## 4. Component Boundary

### Owned behavior

Rule-pack plugin interface `match(ast) → Candidate[]`; the deterministic
pipeline; residue semantics; evidence-file format + keying
`evidence/sca/{repo}/{digest}/{ruleset-ver}.json`.

### Upstream inputs

Task spec (repo, digest, scope, resolver pin); repo content; config manifests.

### Downstream outputs

`ScaEvidenceFile` (LLD §2.2): edges + residue + datasetsSeen + stats.

### Forbidden behavior

- Emitting an edge without file:line + AST-path (or parser-source) evidence.
- Partial config substitution (all placeholders resolve or the span is
  residue).
- Persisting raw names as identity — resolver at intake, quarantine on miss.
- Retaining checkout workspaces or credentials past the run.

## 5. Functional Requirements

| ID | Requirement | Priority |
|---|---|---|
| L04-FR-001 | Shallow clone at the event digest with per-run credentials; workspace destroyed on exit (success or failure). | P0 |
| L04-FR-002 | Manifest scan selects rule packs; no-grammar types are listed for LLM Tier-2, never skipped silently. | P0 |
| L04-FR-003 | Parser suite: sqlglot dialect matrix (CTE, MERGE, INSERT-SELECT, window fns → column mapping), dbt ref/source, OpenAPI/proto fields. | P0 |
| L04-FR-004 | Rule packs per framework (Test Suite §2 matrix) emit Candidates with rawName, kind, elements, transform, evidence; negative fixtures must not match. | P0 |
| L04-FR-005 | Config resolution: deterministic substitution from parsed config/env manifests; success → exact edge; failure → residue(dynamic-name). | P0 |
| L04-FR-006 | Residue entries carry file, span, reason, nearby SCA facts, and knownUrns for the repo — the LLM context handoff. | P0 |
| L04-FR-007 | Determinism replay in CI: two runs on identical inputs → byte-identical output; a diff is a release blocker. | P0 |
| L04-FR-008 | Emit via resolver only; datasetsSeen recorded for join-rate instrumentation. | P0 |
| L04-FR-009 | Monorepo path scoping: analyze only paths in scope per classification. | P1 |

### Non-functional requirements

| ID | Requirement | Priority |
|---|---|---|
| L04-NFR-001 | Median repo analysis ≤ 10 min on the standard Fargate task size; hostile-repo fixtures (huge files, malformed manifests) fail bounded, not hung. | P0 |
| L04-NFR-002 | Concurrency 1 task per (repo, digest); horizontal scale to baseline-window targets. | P0 |

## 6. Data and Durable State

Stateless task; output to S3 via L08 (immutable, per-digest); no local state
survives the run. Rule packs and parser versions are release artifacts with
their own fixture suites.

## 7. Interfaces and Contracts

Consumes task spec (L03 contract). Produces `ScaEvidenceFile` — schema
contract with L07 (Test Suite §5 “Evidence schemas”); merge rejects unknown
versions. Residue is a contract with L05 (reason codes drive tiering).

## 8. Processing and State Model

`CHECKOUT → DETECT/ROUTE → PARSE → AST-WALK → CONFIG-RESOLVE → RESOLVE(URN)
→ EMIT` (canvas 2a). Every stage appends to stats; failures carry the stage
name.

## 9. Failure Semantics

| Code | Class | Behavior |
|---|---|---|
| `CLONE_FAILED` | TRANSIENT | Retry with backoff; credential re-issue; DLQ on exhaustion |
| `PACK_CRASH` | DETERMINISTIC | Isolate pack, continue others, flag partial evidence; pack fix required |
| `RESOLVER_QUARANTINE` | EXPECTED | Assertion quarantined, run continues; gap report emitted |
| `WORKSPACE_TIMEOUT` | TRANSIENT | Bounded kill; partial evidence marked PARTIAL, never silently complete |

## 10. Security and Privacy

Per-run scoped read-only SCM credentials (no standing access); encrypted
ephemeral workspace; source code never persisted beyond evidence spans
(file:line refs + minimal snippets in evidence only); egress restricted to
SCM + S3 + resolver snapshot.

## 11. Observability

Tasks by outcome; stage durations; residue rate by reason; quarantine rate;
determinism-replay status; pack coverage per matrix cell.

## 12. Acceptance Criteria and Test Matrix

Normative suite: Test Suite §3.2 (rule-pack fixtures per framework, parser
matrix, residue reasons, determinism replay, manifest routing) + §2 seeded
repos + §4 baseline/incremental scenarios. Acceptance: every matrix cell has
green fixtures; determinism gate active in CI; hostile-repo suite bounded.

## 13. Integration Obligations

- **L04↔L01:** resolve-at-intake; one snapshot pin per run.
- **L04↔L05:** residue schema versioned; each reason code has a routing test.
- **L04↔L07:** evidence contract verified on every build.

## 14. Definition of Done

All four language packs + three parser families shipped with fixture suites;
determinism replay in CI; seeded-matrix acceptance green; per-run credential
issuance audited end-to-end.
