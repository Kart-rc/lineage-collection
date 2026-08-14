---
type: Reference
title: Event Intake, Normalization, and Priority Queues
description: L02 is the durable front door that authenticates producers, deduplicates deliveries, normalizes triggers into envelopes, and routes them onto priority lanes.
resource: /Users/sowmiyamohankumar/Documents/lineage-collector-v1/docs/component-prds/02-event-intake-and-queues.md
tags: [lineage, component-prd, intake, queues, events]
timestamp: 2026-08-14T11:30:00Z
---

# Event Intake, Normalization, and Priority Queues

## Purpose

L02 authenticates every producer (GitHub HMAC, Jenkins signed events, schedules, TAS inventory), drops repeat deliveries, normalizes each trigger to one `EventEnvelope`, and routes it onto the right SQS priority lane so spikes are absorbed by queues, never by workflows. Loss here is unrecoverable, so it is P0.

## Key requirements and invariants

- Signature verification on every delivery; invalid → quarantine plus sanitized security telemetry — an unauthenticated event is never accepted.
- Dedupe by provider event id via DynamoDB conditional put (TTL 14d); missing immutable identity (no digest/sha) quarantines rather than forming an empty dedupe key.
- Every accepted event is traceable to a run, a coalesce record, or a quarantine record — never silently dropped.
- Three lanes with DLQs and redrive runbooks: `interactive` (pr.updated), `events` (push, deploy), `bulk` (baseline/backfill/nightly/LLM batch). Lane policy is versioned; ad hoc rerouting is forbidden. The interactive lane must never be starved by bulk load — its age is a paging alarm.
- EventBridge archive retains all normalized events; replay produces zero duplicate business effects (relying on downstream L07 idempotency).
- Payload bodies live only in the encrypted archive, never in general logs.

## Interfaces

Ingress: provider webhook endpoints (API Gateway → normalizer Lambda). Egress: the `EventEnvelope` JSON Schema per trigger type — a consumer-driven contract with L03; webhook payload versions pinned as recorded fixtures. State machine: `RECEIVED → AUTHENTICATED → DEDUPED → NORMALIZED → ROUTED`, with any failure branching to `QUARANTINED(reason)`; each transition emits a metric.

## Constraints and failure semantics

Intake ack ≤ 500 ms p95 at a sustained 100 events/s; a 10,000-event burst is absorbed with zero loss. Bad signatures are never retried; duplicates are acked and dropped with a metric; lane unavailability gets bounded retry then DLQ. Envelope and dedupe state must recover within platform RPO/RTO without duplicate effects.

## Related

* [Orchestration and Scheduling](/references/orchestration-and-scheduling.md) - L03 consumes every routed envelope and must map it to exactly one run or a recorded no-impact decision
* [System Context and Architecture](/references/system-context.md) - the burst-absorption launch gate and never-silently-dropped invariant originate there
* [Event Intake and Lanes (b04)](/references/b04-event-intake-and-lanes.md) - the build unit delivering the intake endpoints, lanes, and DLQs
* [Consolidation, URN Merge, and Confidence](/references/consolidation-and-confidence.md) - replay safety depends on L07 merge idempotency to avoid duplicate edges

## Citations

1. [02-event-intake-and-queues.md](/Users/sowmiyamohankumar/Documents/lineage-collector-v1/docs/component-prds/02-event-intake-and-queues.md)
