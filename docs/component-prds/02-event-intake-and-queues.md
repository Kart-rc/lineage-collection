# 02-event-intake-and-queues.md

# L02 Event Intake, Normalization, and Priority Queues PRD

## 1. Document Control

| Field | Value |
|---|---|
| Component | L02 |
| Status | Draft for implementation |
| Launch phase | MVP |
| Criticality | P0 — the durable front door; loss here is unrecoverable |
| Primary owner | Lineage platform team (intake) |
| Required approvers | Architecture, security |
| Upstream dependencies | GitHub webhooks, Jenkins deploy events, TAS inventory, EventBridge schedules |
| Downstream dependencies | L03 (workflow starts); L12 (telemetry) |
| Authoritative sources | Lineage HLD §§1, 8; Lineage LLD §§2.1, 3.1 (dedupe), 7; v3 deck slides 04/04B |

## 2. Purpose and Outcomes

L02 authenticates every producer, drops repeat deliveries, normalizes each
trigger to one envelope, and routes it onto the right priority lane so spikes
are absorbed by queues, never by workflows.

Measurable outcomes:

- A 10,000-event burst is accepted without loss; 100 normalized triggers/s
  sustained.
- Zero duplicate business effects from redelivered webhooks (dedupe by
  provider event id).
- Every accepted event is traceable to a run, a coalesce record, or a
  quarantine record — never silently dropped.
- Interactive lane (PRGate) start latency unaffected by baseline/backfill
  load (never starved).

## 3. Scope and Non-Goals

### In scope

- Signature verification per producer (GitHub HMAC, Jenkins signed events).
- Dedupe (conditional put on eventId, TTL 14d).
- Envelope normalization per trigger type (Lineage LLD §2.1).
- EventBridge rules + archive (replay); three SQS lanes + DLQs:
  `interactive`, `events`, `bulk`.
- Quarantine of malformed/unauthenticated input with sanitized telemetry.

### Non-goals

- Starting or coordinating workflows (L03).
- Classifying repositories or resolving URNs (envelope carries raw context).
- Storing payload bodies beyond the normalized envelope + audit archive.

## 4. Component Boundary

### Owned behavior

- Producer registry (auth scheme, secret ref, allowed event types).
- Lane routing policy (trigger type → lane; normative: HLD §8 table).
- Archive + replay tooling (replay is safe by downstream idempotency).

### Upstream inputs

Raw webhooks/events; schedule fires; TAS inventory deltas.

### Downstream outputs

`EventEnvelope` (LLD §2.1) on a lane; quarantine records; dedupe metrics.

### Forbidden behavior

- Accepting an unauthenticated or unsigned event.
- Deduplicating under an empty/missing event id (quarantine instead).
- Re-routing around lane policy ad hoc; policy changes are versioned.
- Logging webhook payload bodies into general logs.

## 5. Functional Requirements

| ID | Requirement | Priority |
|---|---|---|
| L02-FR-001 | Verify provider signatures on every delivery; invalid → quarantine + sanitized security telemetry. | P0 |
| L02-FR-002 | Dedupe by provider event id via conditional put; repeats acknowledged and dropped with a metric. | P0 |
| L02-FR-003 | Normalize every trigger type to the envelope with correlationId stamped; unknown types quarantine. | P0 |
| L02-FR-004 | Route per lane policy: pr.updated → interactive; push + deploy → events; baseline/backfill/nightly/LLM batch → bulk. | P0 |
| L02-FR-005 | Missing immutable identity (no digest on a deploy, no sha on a push) → quarantine, never an empty dedupe key. | P0 |
| L02-FR-006 | EventBridge archive retains all normalized events; replay reproduces no duplicate effects (with L07 idempotency). | P0 |
| L02-FR-007 | Each lane has a DLQ with redrive runbook; DLQ depth alarmed. | P0 |
| L02-FR-008 | Schedule triggers (nightly, snapshot pull) are first-class producers with the same envelope discipline. | P1 |

### Non-functional requirements

| ID | Requirement | Priority |
|---|---|---|
| L02-NFR-001 | Intake ack ≤ 500 ms p95 under sustained 100/s. | P0 |
| L02-NFR-002 | Burst 10,000 events absorbed with zero loss (queue depth absorbs; workflows unaffected). | P0 |
| L02-NFR-003 | Envelope + dedupe state recover within platform RPO/RTO with no duplicate business effects. | P0 |

## 6. Data and Durable State

DynamoDB `dedupe` (PK eventId, TTL 14d). EventBridge archive (normalized).
Quarantine records in S3 `quarantine/`. Producer registry (auth config) in
AppConfig/DynamoDB, versioned.

## 7. Interfaces and Contracts

Ingress: provider webhook endpoints (API GW → normalizer λ). Egress: the
`EventEnvelope` JSON Schema per trigger type — consumer-driven contract with
L03 (Test Suite §5 “Event envelope”); webhook payload versions pinned as
recorded fixtures (§5 “Webhook ingress”).

## 8. Processing and State Model

`RECEIVED → AUTHENTICATED → DEDUPED → NORMALIZED → ROUTED` — any failure
branches to `QUARANTINED(reason)` or acknowledged-duplicate. No state is
skipped; each transition emits a metric.

## 9. Failure Semantics

| Code | Class | Behavior |
|---|---|---|
| `BAD_SIGNATURE` | DETERMINISTIC | Quarantine; sanitized security event; never retried |
| `DUPLICATE` | EXPECTED | Ack + drop + metric |
| `MISSING_IDENTITY` | DETERMINISTIC | Quarantine with provider payload ref |
| `LANE_UNAVAILABLE` | TRANSIENT | Bounded retry; DLQ on exhaustion |

## 10. Security and Privacy

Webhook secrets in Secrets Manager, rotated; endpoints TLS-only; payload
bodies only in the encrypted archive; quarantine views require operator role.

## 11. Observability

`intake.accepted/duplicate/quarantined` by type; lane depth + age alarms;
`interactive` lane age is a paging alarm (PR gate never starves).

## 12. Acceptance Criteria and Test Matrix

Normative suite: Test Suite §3.6 (intake unit), §4 “Replay & rebuild”, §6
canaries (GitHub webhook loop, Jenkins event). Acceptance: signature matrix
(valid/invalid/replayed) green; burst load test passes L02-NFR-002; replay
drill produces zero duplicate edges.

## 13. Integration Obligations

- **L02↔L03:** every routed envelope maps to exactly one run or a recorded
  no-impact decision.
- **L02↔L12:** quarantine and DLQ surfaces appear in operator dashboards with
  runbook links.

## 14. Definition of Done

All producer registrations + fixtures exist; lanes + DLQs deployed with
alarms; replay tooling demonstrated; burst test evidence attached to the
launch review.
