---
type: Reference
title: B04 Event Intake and Lane Router
description: Authenticated provider-event intake that atomically persists receipt, durable command and outbox record, then routes bounded S3 references into fair, isolated processing lanes with DLQ/redrive.
resource: /Users/sowmiyamohankumar/Documents/lineage-collector-v1/docs/build-prds/B04-event-intake-and-lanes.md
tags: [lineage, build-prd, intake, queues, deduplication]
timestamp: 2026-08-14T11:30:00Z
---

# B04 Event Intake and Lane Router

B04 (Track B) accepts provider events only after authentication and strict normalization, then
atomically persists the receipt, durable command and outbox record, and routes a bounded S3
reference into the selected lane. It owns provider auth, normalization, atomic acceptance,
deduplication, routing, archives, lane fairness and DLQ/redrive; workflow outputs belong to B05+.

## Key contracts and states

- Versioned provider envelopes normalize to event/command IDs, correlation/causation, repository,
  artifact, environment, workflow and payload reference.
- Duplicate identity returns the original command — zero duplicate effect is a hard gate.
- `RECEIVED -> ACCEPTED|QUARANTINED`; lane messages are available, leased, acknowledged, retried,
  dead-lettered or superseded. Invalid auth writes no command; poison work is bounded and isolated.
- B04-AC-001: duplicate/reordered/poison/burst events yield one command, bounded redrive and no
  loss (every PR; AWS deployment gate for scale).

## Infrastructure

API Gateway/EventBridge intake with archive/replay, intake Lambda, DynamoDB receipt transaction,
interactive/events FIFO queues, bulk/runtime Standard queues, per-lane DLQs, KMS, WAF and reserved
interactive concurrency. The local adapter uses HMAC intake, a SQLite receipt/command/outbox
transaction and a persistent lane broker simulating FIFO, fairness, supersession, leases and
logical DLQ with deterministic clocks.

## Constraints, rollback and seams

Zero acknowledged loss is a hard gate, but the 500 ms p95, 100/s sustained and 10k burst claims
are `AWS_REQUIRED` until approved load evidence exists. Rollback restores routing/alias and replays
the scoped archive through the same dedupe transaction — shared queues are never purged. Provider
payloads/secrets, tenant quotas and routing ownership are CTX seams; Definition of Ready names
provider fixtures, auth method, ordering key, lane/dead-letter policy, rate owner and replay
authorization.

## Related

* [Event Intake and Queues](/references/event-intake-and-queues.md) - normative L02 intake requirements B04 implements
* [NFR and Resiliency Spec](/references/nfr-and-resiliency-spec.md) - normative L15 resilience requirements behind loss/duplicate gates and load claims
* [Security, Observability and Operations](/references/security-observability-operations.md) - normative L12 requirements for auth-before-acceptance and secret rotation
* [B03 Evidence and Control-Store Adapters](/references/b03-evidence-and-control-stores.md) - upstream store ports used for the atomic receipt/command/outbox transaction
* [B05 Classifier and Workflow Orchestrator](/references/b05-classifier-and-orchestrator.md) - downstream consumer of durable commands routed by B04 lanes

## Citations

1. [B04-event-intake-and-lanes.md](/Users/sowmiyamohankumar/Documents/lineage-collector-v1/docs/build-prds/B04-event-intake-and-lanes.md)
