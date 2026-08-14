---
type: Reference
title: B15 Operations, Telemetry and Recovery
description: Delivers the platform-wide OpenTelemetry conventions, SLO/alert framework, acceptance-evidence manifests, and coordinated backup/restore/writer-election/rebuild recovery procedures.
resource: /Users/sowmiyamohankumar/Documents/lineage-collector-v1/docs/build-prds/B15-operations-telemetry-and-recovery.md
tags: [lineage, build-prd, telemetry, recovery, slo]
timestamp: 2026-08-14T11:30:00Z
---

# B15 Operations, Telemetry and Recovery

Track A's cross-cutting operations unit (normative sources: L12 operations/telemetry, L15
non-functional requirements). It collects telemetry, calculates service indicators, retains
acceptance evidence, and coordinates backup, restore, warm-standby, writer-election, and
projection-rebuild procedures. Its core epistemic rule: **telemetry is not domain truth**, and
evidence must always distinguish local claims from deployed ones.

## What it delivers

OpenTelemetry conventions, the SLO/alert framework, recovery controls, versioned contracts for
correlation fields, resilience status, and the `AcceptanceEvidenceManifest`. Every build unit owns
its own signals and runbook; platform operations owns backup, restore, writer election, and drills.

## Key invariants

- Recovery operations are typed state machines (requested/running/verified/failed/rolled back)
  carrying requested/effective versions, fence, actor, idempotency key, and outcome.
- **Exactly one region owns writer authority**; stale leases/fences cannot write.
- Degraded signals and *missing drill evidence* fail closed — absence of proof blocks, it does not
  pass.
- B15 reads operational projections and checksums but never replaces evidence, the ledger, or
  active pointers; destructive recovery actions require authenticated, audited approval with
  separated writer/recovery roles.

## Honest-evidence model

Local gates cover deterministic recovery invariants only (fault-injection against queue, store,
lease, publisher, and projection faults — B15-AC-001, run every deployment plus scheduled drills).
Availability, scale, durability, RPO/RTO, regional, and cost claims remain `AWS_REQUIRED` until
signed deployed evidence exists with named owners and an error-budget policy.

## Infrastructure and seams

CloudWatch, OTel collectors/exporters, alarms/dashboards, SNS/pager integration, AWS Backup,
cross-region replication, encrypted evidence archive, budgets, canaries, runbook automation.
Paging, retention, and regional targets are CTX decisions; Definition-of-Ready requires owners,
indicators, thresholds, redaction, backup scope, topology, RPO/RTO, drill cadence, and cost
budgets approved per target environment. Signal schemas, dashboards, and recovery automation are
versioned with the runtime and rolled back together, preserving immutable evidence and fencing.

## Related

* [Security, Observability and Operations](/references/security-observability-operations.md) - the normative L12 component PRD for the telemetry and operations contracts B15 builds.
* [NFR and Resiliency Spec](/references/nfr-and-resiliency-spec.md) - the L15 normative source for the availability, RPO/RTO, and resilience objectives B15 measures and evidences.
* [B12 Publisher and Projection Manager](/references/b12-publisher-and-projection-manager.md) - B15 coordinates projection rebuild and publication-resume recovery around B12's fenced operations.
* [B16 Platform IaC and Delivery](/references/b16-platform-iac-and-delivery.md) - the downstream delivery unit that deploys B15's alarms, backup, and telemetry infrastructure.
* [B01 Contracts and Correlation](/references/b01-contracts-and-correlation.md) - upstream source of the correlation fields every B15 signal and recovery record carries.

## Citations

1. [B15-operations-telemetry-and-recovery.md](/Users/sowmiyamohankumar/Documents/lineage-collector-v1/docs/build-prds/B15-operations-telemetry-and-recovery.md)
