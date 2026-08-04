# 05-llm-inference-gateway.md

# L05 LLM Inference Gateway PRD

## 1. Document Control

| Field | Value |
|---|---|
| Component | L05 |
| Status | Draft for implementation |
| Launch phase | MVP (T1/T2) · P2 (T3 verification) |
| Criticality | P0 for coverage; never on the PR path |
| Primary owner | Lineage platform team (inference) |
| Required approvers | Architecture, security, cost governance |
| Upstream dependencies | L04 residue; L01 resolver; Bedrock via enterprise gateway |
| Downstream dependencies | L08 (responses/rejects/cache); L07 merge; L09 calibration corpus |
| Authoritative sources | Lineage HLD §5; Lineage LLD §§2.3, 4.2; canvas frames 2b/2e; Risk R5 |

## 2. Purpose and Outcomes

L05 answers only what SCA could not, with SCA's own facts as context, and
guarantees that nothing unvalidated reaches the merge. Its edges are always
marked inferred and capped.

Measurable outcomes:

- 100% of responses pass the four-stage guardrail chain or land in a logged
  reject file — zero unvalidated edges reach L07.
- Cache hit ≥ 90% steady-state; unchanged code is never re-billed (gate R5).
- Every emitted edge cites a file:line that exists in the digest and a URN
  that resolves in the catalog.
- LLM-only edges never gate a PR and never auto-publish.

## 3. Scope and Non-Goals

### In scope

- Tiered triggers: T1 residue (Incremental, cache miss), T2 no-grammar files
  (Baseline/Nightly), T3 nightly verification sample (calibration only).
- Chunking (span ± enclosing function + imports + config keys, ≤ 8k tokens).
- Context assembly: SCA facts + known repo URNs into the prompt (the SCA→LLM
  feed; SCA facts stored independently and never overwritten).
- Cache keyed (chunk digest, prompt version, model id); staged invalidation.
- Guardrail chain: schema → citation → URN → budget; reject persistence.
- Budget ledger: tokens/repo/day, $/workflow lane; separate rate limits.

### Non-goals

- Serving PRGate (refused by design).
- Whole-repo prompting (chunks only).
- Publishing decisions or confidence computation (L07/L09).
- Model hosting (enterprise Bedrock gateway).

## 4. Component Boundary

### Owned behavior

Prompt templates (versioned); structured-output schemas per promptVersion;
the guardrail chain as ordered middleware; cache lifecycle; staged
re-derivation rollout plans.

### Upstream inputs

Residue entries (L04 contract); T2 file lists; T3 sample sets; resolver pin.

### Downstream outputs

Validated `LlmResponse` (LLD §2.3) with `unresolvable[]` as a first-class
answer (routes spans to the runtime watch list); reject files with
failedGuardrail; cache records.

### Forbidden behavior

- Free-text responses (structured output enforced).
- Emitting an edge whose citation or URN failed validation.
- Fleet-wide synchronous cache invalidation on a prompt/model bump.
- Any call outside the enterprise gateway or above the budget caps.

## 5. Functional Requirements

| ID | Requirement | Priority |
|---|---|---|
| L05-FR-001 | Tier routing: residue → T1; registered no-grammar types → T2; verification samples → T3 on the bulk lane only; PR-path requests are rejected with a typed error. | P0 |
| L05-FR-002 | Chunker produces stable digests for identical code; budget ≤ 8k tokens; over-budget spans split along AST boundaries. | P0 |
| L05-FR-003 | Context assembly injects SCA facts + knownUrns; the prompt records which facts were provided (for audit and calibration). | P0 |
| L05-FR-004 | Cache: hit serves without a model call; key components (chunk digest, promptVersion, modelId); shared across all workflows. | P0 |
| L05-FR-005 | Guardrail chain in order — schema validate, citation exists in digest, URN resolves via L01, budget check; first failure stops the chain and persists a reject. | P0 |
| L05-FR-006 | `unresolvable[]` entries persist with reasons and feed the runtime watch list; they are answers, not errors. | P0 |
| L05-FR-007 | Prompt/model bump: rollout record created; nightly batch re-derives a bounded % of stale keys per day; old entries served until replaced. | P0 |
| L05-FR-008 | Every response/reject/rollout is persisted to L08 with correlation fields; nothing is discarded silently. | P0 |

### Non-functional requirements

| ID | Requirement | Priority |
|---|---|---|
| L05-NFR-001 | T1 call round-trip ≤ 30 s p95 including guardrails; budget caps enforced with zero overspend days. | P0 |
| L05-NFR-002 | Gateway degradation never blocks Incremental completion: LLM stage is skippable-with-record (edges arrive later via re-merge). | P0 |

## 6. Data and Durable State

DynamoDB `llm_cache` (PK cacheKey; responseRef, hitCount, staleFlag) +
budget ledger. S3: `evidence/llm/{cacheKey}.json`, `llm-rejects/{date}/`,
rollout records. Prompt templates + output schemas versioned in-repo.

## 7. Interfaces and Contracts

`POST /infer` (T1/T2), `POST /verify` (T3 batch) — internal only. Structured
output JSON Schema per promptVersion is a contract (Test Suite §5 “LLM
structured output”); a new promptVersion requires a new contract fixture set
and adversarial suite.

## 8. Processing and State Model

`INTAKE(tier) → CHUNK → CONTEXT → CACHE(hit|miss) → CALL → GUARDRAILS →
EMIT | REJECT` (canvas 2e worked example is normative for field semantics).

## 9. Failure Semantics

| Code | Class | Behavior |
|---|---|---|
| `SCHEMA_REJECT` / `CITATION_REJECT` / `URN_REJECT` | DETERMINISTIC | Persist reject with failedGuardrail; surfaced in review; never merged |
| `BUDGET_EXCEEDED` | POLICY | Defer to next window; alarm at threshold |
| `GATEWAY_UNAVAILABLE` | TRANSIENT | Skip-with-record; re-merge on later success |
| `PROMPT_ROLLOUT_STALLED` | OPERATIONAL | Alarm; rollout plan visible in L11 |

## 10. Security and Privacy

Calls only via the enterprise gateway; chunks contain source code — encrypted
in transit/at rest, retained only in evidence; no data-plane values ever
prompt-injected (code + metadata only); per-lane rate limits prevent noisy-
neighbor cost.

## 11. Observability

Cache hit rate (gate ≥ 90%); rejects by guardrail; spend per repo/day vs cap;
tier volumes; `unresolvable` rate; rollout progress.

## 12. Acceptance Criteria and Test Matrix

Normative suite: Test Suite §3.3 (chunker, cache, adversarial guardrail
fixtures, tier routing) + §5 contract + §6 daily live Bedrock canary (drift
signal). Acceptance: every guardrail has a failing adversarial fixture; cache
economics proven on seeded repos; staged-rollout drill executed.

## 13. Integration Obligations

- **L05↔L04:** residue schema pinned; reason codes drive tier tests.
- **L05↔L01:** URN guardrail uses the same resolver pin as the run.
- **L05↔L07:** only guardrail-passing edges reach merge; provenance carries
  promptVersion + citation.

## 14. Definition of Done

Gateway deployed behind budget ledger; guardrail chain with full adversarial
suite; cache + staged invalidation demonstrated; live canary green for 7
consecutive days in staging.
