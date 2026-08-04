# 08-evidence-store-and-cache.md

# L08 Immutable Evidence Store and Cache PRD

## 1. Document Control

| Field | Value |
|---|---|
| Component | L08 |
| Status | Draft for implementation |
| Launch phase | MVP |
| Criticality | P0 — S3 is truth; everything else is rebuildable from here |
| Primary owner | Lineage platform team (storage) |
| Required approvers | Architecture, security, compliance (retention) |
| Upstream dependencies | KMS, S3 Object Lock policy approval |
| Downstream dependencies | All producers/consumers of evidence (L02–L11) |
| Authoritative sources | Lineage HLD §9; Lineage LLD §3.2; v3 deck slide 17 |

## 2. Purpose and Outcomes

L08 owns the truth layer: immutable, encrypted, content-organized storage for
evidence, residue, LLM artifacts, observations, proposals, approvals,
manifests, and snapshots — plus the content-addressed cache index that makes
unchanged work free.

Measurable outcomes:

- Zero in-place overwrites of evidence/proposals/manifests (Object Lock +
  write-path tests).
- Any projection or ledger is rebuildable from S3 alone (quarterly drill).
- Cache reuse: unchanged (digest, ruleset) analysis and unchanged LLM chunks
  are served from prior artifacts.
- Every stored artifact is checksummed and referenced by small, stable
  `EvidenceRef`s — workflows never carry large payloads.

## 3. Scope and Non-Goals

### In scope

- Bucket/prefix layout (LLD §3.2 normative); keying discipline per artifact
  kind; Object Lock retention classes.
- `EvidenceRef` format (bucket, key, checksum, kind, schemaVersion).
- Content-addressed cache indexes (SCA per-digest; LLM per cacheKey) in
  DynamoDB.
- Lifecycle policies (quarantine/rejects age-out; truth classes retained).
- Integrity verification jobs (checksum sweeps).

### Non-goals

- Interpreting evidence (L07) or serving user queries (L11).
- Catalog snapshots' content (L01 owns semantics; L08 stores).
- Backup of projections (they rebuild, not restore).

## 4. Component Boundary

### Owned behavior

Layout, retention, encryption, integrity, and the read/write client library
(the only sanctioned write path — no direct puts from engines).

### Upstream inputs

Artifacts from every producer with kind + schemaVersion + checksum.

### Downstream outputs

`EvidenceRef`s; cache hit decisions; integrity reports.

### Forbidden behavior

- Overwrites or deletes inside truth prefixes (deny policies + Object Lock).
- Unchecksummed writes; unversioned schema kinds.
- Serving a cache hit whose underlying object fails checksum.

## 5. Functional Requirements

| ID | Requirement | Priority |
|---|---|---|
| L08-FR-001 | Enforce the normative layout; unknown prefixes rejected by the client library. | P0 |
| L08-FR-002 | Truth classes (evidence, proposals, approvals, manifests) carry Object Lock; quarantine/rejects carry lifecycle expiry. | P0 |
| L08-FR-003 | Every write computes and stores checksum + schemaVersion; reads verify on demand and in scheduled sweeps. | P0 |
| L08-FR-004 | SCA cache: (repo, digest, rulesetVersion) → existing evidence served without re-analysis. | P0 |
| L08-FR-005 | LLM cache index cooperates with L05's staged invalidation (stale flag, not delete). | P0 |
| L08-FR-006 | Rebuild support: manifests enumerate everything needed to reconstruct projections and the edge ledger. | P0 |
| L08-FR-007 | Cross-Region replication for truth classes per DR policy. | P1 |

### Non-functional requirements

| ID | Requirement | Priority |
|---|---|---|
| L08-NFR-001 | Ref read ≤ 100 ms p95 for control-sized artifacts; large artifacts streamed. | P0 |
| L08-NFR-002 | 15-minute RPO / four-hour RTO for truth classes. | P0 |

## 6. Data and Durable State

S3 `ldp-truth` per LLD §3.2; DynamoDB cache indexes; integrity-sweep results
in operational storage.

## 7. Interfaces and Contracts

Client library API: `put(kind, key, body) → EvidenceRef`,
`get(ref) → verified body`, `cacheLookup(kind, key)`. EvidenceRef format is a
platform-wide contract; kinds registry is versioned.

## 8. Processing and State Model

Write path: validate kind → checksum → put (conditional, no overwrite) →
index. Integrity sweep: sample per class per day; full sweep quarterly.

## 9. Failure Semantics

| Code | Class | Behavior |
|---|---|---|
| `OVERWRITE_ATTEMPT` | DEFECT | Denied + alarmed; producer bug |
| `CHECKSUM_MISMATCH` | INTEGRITY | Serve refused; replica consulted; incident |
| `LOCK_POLICY_DRIFT` | SECURITY | Config-rule alarm; change audit |

## 10. Security and Privacy

KMS per class; TLS-only, private access; deny-delete SCPs on truth prefixes;
access logging on evidence reads (reviewer evidence views audited via L11).

## 11. Observability

Writes by kind; cache hit rates; integrity sweep results; replication lag;
storage growth per class.

## 12. Acceptance Criteria and Test Matrix

Normative suite: Test Suite §4 replay/rebuild scenario + §7 chaos drills
(projection rebuild). Acceptance: overwrite attempts provably denied;
rebuild drill from S3 alone green; cache reuse measured on seeded repos.

## 13. Integration Obligations

- **L08↔all producers:** writes only via the client library with kind +
  schemaVersion.
- **L08↔L10:** manifests sufficient for full projection rebuild (drill
  co-owned).

## 14. Definition of Done

Layout + lock policies deployed and probed; client library adopted by every
producer; integrity sweeps scheduled; rebuild drill evidence attached to
launch review.
