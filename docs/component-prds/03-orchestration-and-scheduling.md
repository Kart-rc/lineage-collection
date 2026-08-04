# 03-orchestration-and-scheduling.md

# L03 Workflow Orchestration and Scheduling PRD

## 1. Document Control

| Field | Value |
|---|---|
| Component | L03 |
| Status | Draft for implementation |
| Launch phase | MVP (Incremental, Baseline) · P1 (PRGate, deploy λ) · P2 (Nightly) |
| Criticality | P0 — every run is visible, bounded, and redrivable here |
| Primary owner | Lineage platform team (orchestration) |
| Required approvers | Architecture, operations |
| Upstream dependencies | L02 lanes; determinants table; repository classification |
| Downstream dependencies | L04/L05/L06 scheduling; L07 merge stage; L09 proposal creation; L10 publish tail |
| Authoritative sources | Lineage HLD §8; v3 deck slides 05/05B; DE simplification P2 (7→4 state machines) |

## 2. Purpose and Outcomes

L03 coordinates the four Step Functions workflows — Incremental, Baseline,
PRGate, NightlyReconciliation — plus the deploy-promotion Lambda step. It
turns queued envelopes into visible, bounded, redrivable runs; schedules
engine work by classification and determinants; and owns the run ledger.

Measurable outcomes:

- Every dequeued envelope maps to exactly one run (or recorded no-impact).
- Incremental scope is determinant-bounded: a one-file push re-derives only
  its edges (verified by the Test Suite §4 incremental scenario).
- Deploy promotion is a 3-step conditional pointer update, not a workflow.
- Any failed run is redrivable from its failed state without re-running
  completed stages.

## 3. Scope and Non-Goals

### In scope

- Workflow definitions, stage transitions, run ledger writes.
- Determinant resolution: (config, ruleset, resolver, snapshot, prompt)
  versions per scope; change → bounded re-queue.
- Scheduling policy: lane fairness by system; bulk-lane quotas; LLM batch
  windows.
- Redrive semantics and stage-level idempotency tokens.

### Non-goals

- Performing analysis (L04–L06), merging (L07), or publishing decisions
  (L09/L10) — L03 sequences them.
- Queue mechanics (L02).
- Classification policy content (consumed as input).

## 4. Component Boundary

### Owned behavior

Run ledger; workflow ASL definitions; determinants table; scheduling quotas;
the deploy-promotion λ (conditional 3-step pointer swap, delegated to L10's
pointer contract).

### Upstream inputs

Envelopes per lane; classification decisions; determinant records.

### Downstream outputs

Engine task submissions (S3-reference payloads, never large bodies); merge
stage invocations; run stage history for L11 timeline.

### Forbidden behavior

- Passing large payloads through workflow state (S3 references only).
- Starting expensive analysis before classification.
- Any manual invocation path (no CLI/console "run now").
- PRGate calling the LLM gateway (parsers + cache only).

## 5. Functional Requirements

| ID | Requirement | Priority |
|---|---|---|
| L03-FR-001 | Incremental: normalized push → determinant-bounded targeting → SCA → LLM on cache miss → merge → proposal → publish tail. | P0 |
| L03-FR-002 | Baseline: classification first; UNKNOWN blocks critical completion; all engines scheduled per class; merge + proposal + publish tail. | P0 |
| L03-FR-003 | PRGate: read-only, parsers + cache only, verdict against the env baseline, interactive lane, latency budget enforced. | P1 |
| L03-FR-004 | Deploy events run the promotion λ: verify digest → conditional pointer swap → audit record; rollback reactivates prior pointer. | P1 |
| L03-FR-005 | Nightly: rescan sample, compare with published, emit drift proposals; hosts LLM T3 verification batch and staged re-derivation. | P2 |
| L03-FR-006 | Determinant change (ruleset/prompt/resolver/snapshot/config) re-queues exactly the affected scopes; scope computation is auditable. | P0 |
| L03-FR-007 | Run ledger records every stage transition with correlation fields; redrive resumes from failed stage idempotently. | P0 |
| L03-FR-008 | Bulk-lane fairness: per-system quotas prevent one system's backfill starving others. | P1 |

### Non-functional requirements

| ID | Requirement | Priority |
|---|---|---|
| L03-NFR-001 | Incremental push-to-proposal ≤ 30 min p95 excluding review. | P0 |
| L03-NFR-002 | PRGate verdict within CI budget p95 under concurrent bulk load. | P1 |
| L03-NFR-003 | 10,000-repo baseline completes within the planned window at measured planning utilization. | P0 |

## 6. Data and Durable State

DynamoDB `run_ledger` (PK repo, SK runId; stage history, evidenceRefs);
`determinants` (PK scopeUrn, SK determinantId). Workflow state in Step
Functions; artifacts in S3 via L08.

## 7. Interfaces and Contracts

Consumes `EventEnvelope` (contract with L02). Emits engine task submissions
(taskSpec schema: repo, digest, scope, evidence prefix, resolver pin).
Run-timeline read model consumed by L11.

## 8. Processing and State Model

Per-workflow stage machines are normative in v3 deck slide 05B; run states:
`QUEUED → RUNNING(stage…) → MERGED → PROPOSED → PUBLISHED | NO_IMPACT |
FAILED(redrivable)`.

## 9. Failure Semantics

| Code | Class | Behavior |
|---|---|---|
| `CLASSIFICATION_UNKNOWN` | DETERMINISTIC | Baseline blocks critical completion; surfaced for review |
| `ENGINE_TASK_FAILED` | TRANSIENT | Stage retry with backoff; DLQ + redrive on exhaustion |
| `DETERMINANT_SCOPE_EMPTY` | EXPECTED | Record no-impact decision; no run started |
| `POINTER_SWAP_CONFLICT` | DETERMINISTIC | Promotion λ aborts; re-verify digest; alert on repeat |

## 10. Security and Privacy

Workflow roles are least-privilege per stage; the promotion λ is the only
non-publisher writer of pointers and only via L10's conditional contract;
all stage transitions CloudTrail-audited.

## 11. Observability

Runs by state/lane; stage duration p95; determinant re-queue volume; redrive
count; no-impact rate. Alarms: stuck runs, redrive spikes, PRGate latency.

## 12. Acceptance Criteria and Test Matrix

Normative suite: Test Suite §4 scenarios (baseline, incremental, PR gate,
deploy promotion, failure drills). Acceptance: incremental boundedness proven
on seeded repos; redrive drill green; promotion rollback drill green.

## 13. Integration Obligations

- **L03↔L04/L05/L06:** task specs carry the resolver pin; engines never run
  without one.
- **L03↔L07:** merge stage invoked exactly once per run scope (idempotent).
- **L03↔L10:** publish tail only via the fenced contract.

## 14. Definition of Done

Four workflows + promotion λ deployed; determinant re-queue demonstrated;
run timeline visible in L11; all §4 orchestration scenarios green nightly.
