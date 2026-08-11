# B15 — Operations, telemetry and recovery

Normative requirements: [L12 operations and telemetry](../component-prds/12-operations-telemetry-and-recovery.md)
and [L15 non-functional requirements](../component-prds/15-non-functional-requirements.md).

## Ownership

Track A owns the OpenTelemetry conventions, SLO/alert framework and recovery controls. Every build
unit owns its signals/runbook; platform operations owns backup, restore, writer election and drills.

## Boundary

Collect telemetry, calculate service indicators, retain acceptance evidence and coordinate backup,
restore, warm-standby, writer-election and projection-rebuild procedures. Telemetry is not domain truth.

## Contracts

Correlation fields, resilience status and `AcceptanceEvidenceManifest` are versioned contracts.
Recovery operations carry requested/effective versions, fence, actor, idempotency key and outcome.

## State and failure model

Operations are requested, running, verified, failed or rolled back. Exactly one region owns writer
authority; stale leases/fences cannot write. Degraded signals and missing drill evidence fail closed.

## Data ownership

B15 owns telemetry configuration, evidence manifests, drill/restore records, runbooks and cost records.
It reads operational projections and checksums but does not replace evidence, ledger or active pointers.

## Infrastructure bill of materials

CloudWatch, OpenTelemetry collectors/exporters, alarms/dashboards, SNS/Pager integration, AWS Backup,
cross-region replication, encrypted evidence archive, budgets, canaries, runbook automation and KMS.

## Local adapter

Structured logs/metrics/traces, SQLite snapshots and deterministic fault-injection smoke tests prove
contracts and procedures. Availability, scale, RPO/RTO and regional claims remain `AWS_REQUIRED`.

## Security and privacy

Redact telemetry at source, restrict evidence/drill records, encrypt in transit/at rest, separate
writer/recovery roles and require authenticated, audited approval for destructive recovery actions.

## SLOs

Local gates cover deterministic recovery invariants only. Production availability, latency, durability,
RPO, RTO and cost thresholds require deployed measurement, named owners and an error-budget policy.

## Observability

Required signals include queue age/depth, run/stage latency, retries/DLQ, lease/fence rejection,
coverage/join/confidence, outbox lag, publication resume, projection lag, restore/drill and cost.

## Acceptance criteria

| Acceptance ID | Requirement | Scenario and evidence | Gate |
|---|---|---|---|
| B15-AC-001 | L12/L15 truthful resilience evidence | Inject queue, store, lease, publisher and projection faults; prove bounded recovery locally and mark scale/DR checks AWS_REQUIRED until signed deployed evidence exists | Every deployment plus scheduled drills |

## Deployment and rollback

Version signal schemas, dashboards, alarms, runbooks and recovery automation with the runtime. Roll
back incompatible exporters/automation while preserving immutable evidence and active writer fencing.

## Dependencies

B01 correlation, B03 stores, B05 orchestration, B12 publication/projection, B13 status APIs and all
remaining build units as signal producers; paging, retention and regional targets are CTX decisions.

## Definition of Ready

Owners, indicators, thresholds, redaction, retention, backup scope, topology, RPO/RTO, drill cadence,
escalation and cost budgets are approved for the target environment.

## Definition of Done

Signal/fault/recovery contract tests pass; runbooks and alerts have owners; evidence distinguishes
local from deployed claims; `B15-AC-001` manifests are retained.
