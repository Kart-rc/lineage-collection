# B04 — Event intake and lane router

Normative requirements: [L02 intake](../component-prds/02-event-intake-and-queues.md),
[L12 security/operations](../component-prds/12-security-observability-operations.md), and
[L15 resilience](../component-prds/15-nfr-and-resiliency-spec.md).

## Ownership

Track B owns provider authentication, normalization, atomic acceptance, deduplication, routing,
archives, lane fairness and DLQ/redrive. Provider credential owners control secret rotation.

## Boundary

Accept provider events only after authentication and strict normalization; atomically persist the
receipt, durable command and outbox record; route a bounded S3 reference into the selected lane.

## Contracts

Versioned provider envelopes normalize to event/command IDs, correlation/causation, repository,
artifact, environment, workflow and payload reference. Duplicate identity returns the original command.

## State and failure model

`RECEIVED -> ACCEPTED|QUARANTINED`; lane messages are available, leased, acknowledged, retried,
dead-lettered or superseded. Invalid auth writes no command. Poison work is bounded and isolated.

## Data ownership

B04 owns receipts, normalized envelopes, routing decisions, provider sequence gaps, archives and
lane/DLQ metadata; workflow outputs belong to B05+.

## Infrastructure bill of materials

API Gateway/EventBridge intake, archive/replay, intake Lambda, DynamoDB receipt transaction,
interactive/events FIFO queues, bulk/runtime Standard queues, per-lane DLQs, KMS, WAF, alarms and
reserved interactive concurrency.

## Local adapter

HMAC intake, SQLite receipt/command/outbox transaction and a persistent lane broker simulate FIFO,
fairness, supersession, leases and logical DLQ with deterministic clocks.

## Security and privacy

Authenticate before durable acceptance, rotate provider secrets, restrict replay, validate safe S3
references and log identifiers/reasons rather than provider bodies or credentials.

## SLOs

Zero acknowledged loss and zero duplicate effect are hard gates. The 500 ms p95, 100/s sustained
and 10k burst claims are `AWS_REQUIRED` until approved load evidence exists.

## Observability

Measure accept/reject/dedupe, oldest age/depth per lane, saturation, retry/DLQ, fairness, provider
sequence gaps, archive replay and correlation propagation.

## Acceptance criteria

| Acceptance ID | Requirement | Scenario and evidence | Gate |
|---|---|---|---|
| B04-AC-001 | L02 durable authenticated intake | Duplicate/reordered/poison/burst events yield one command, bounded redrive and no loss; local proof plus AWS load report with explicit environment outcome | Every PR; AWS deployment gate for scale |

## Deployment and rollback

Canary provider normalization and Lambda alias while retaining archives. Roll back routing/alias;
replay the scoped archive through the same dedupe transaction. Never purge shared queues for rollback.

## Dependencies

B01 contracts, B03 stores; provider payloads/secrets, tenant quotas and routing ownership are CTX seams.

## Definition of Ready

Provider fixtures, authentication method, ordering key, lane/dead-letter policy, rate owner and replay
authorization are named.

## Definition of Done

Atomic acceptance and duplicate identity pass shared tests; lanes are isolated/fair/bounded;
alarms/runbooks and `B04-AC-001` evidence are retained.
