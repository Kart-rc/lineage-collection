---
type: Reference
title: L12 Security, Observability, Operations, and DR PRD
description: "The cross-cutting control plane: platform-wide IAM/SCP security model, the correlation and telemetry contract, alarm-runbook catalog, drill calendar, and disaster recovery that make every other component's invariants enforceable and auditable."
resource: /Users/sowmiyamohankumar/Documents/lineage-collector-v1/docs/component-prds/12-security-observability-operations.md
tags: [lineage, component-prd, security, observability, disaster-recovery]
timestamp: 2026-08-14T11:30:00Z
---

# L12 Security, Observability, Operations, and DR PRD

## Purpose

L12 owns the machinery that makes invariants across L01–L11 enforceable rather than aspirational: least-privilege IAM roles per stage, SCPs (deny-delete on truth classes, prod-deny conditions), KMS key policy per storage class, the correlation contract every hop must carry, the alarm/runbook catalog, replay/redrive tooling, and cross-Region DR. Its signature stance is *proving controls continuously*: the production hard-deny is probed weekly by a live attack that must fail — a passing injection is a sev-2 incident.

## Key requirements

- **Prod-deny in two layers:** IAM condition + validator check, both probed weekly (`PROD_INJECTION_SUCCEEDED` pages, freezes runtime ingestion, triggers incident review).
- **Correlation contract:** a shared library injects/propagates correlation fields; CI lints handlers for adoption; every platform hop traceable end to end by correlationId across all 5 stages (sampled audit trace).
- **Alarm-runbook coverage 100%:** every DLQ, quarantine, and gate metric (R1 join rate, R3 audit disagreement, R5 cache hit, queue depth, coverage) has an alarm with a linked runbook and defined paging tiers; the gate dashboards are the single source for launch review.
- **Zero standing credentials:** per-run SCM creds, time-boxed runtime sessions, role-scoped service access; webhook HMACs and session keys rotate in Secrets Manager; no secrets in long-lived env vars.
- **DR:** cross-Region replication of truth classes, warm-standby recovery; 15-minute RPO / 4-hour RTO demonstrated in a quarterly full drill (restore + projection rebuild + pointer verify) with retained evidence; `REPLICATION_LAG` alarms at RPO/2.
- **Drill calendar as state machine:** weekly (prod-deny probe, publish-lock chaos), quarterly (full DR + rebuild), continuous canaries — each drill yields immutable evidence or a defect, and `DRILL_FAILURE` blocks launch gates.

## Constraints

Forbidden: break-glass without expiry + audit; any alarm without a runbook link; undetected policy drift (config rules watch lock/deny policies, auto-revert where safe); telemetry embedding payload values or secrets (lint-enforced — the platform stores metadata only). Telemetry overhead ≤ 5% of stage latency budgets. Component-local logic stays with each PRD; L12 audits conformance.

## Related

* [System Context](/references/system-context.md) - the correlation contract and launch gates L12 enforces are defined in the system-context sections it cites
* [Fenced Publication and Projections](/references/fenced-publication-and-projections.md) - the DR drill (restore + rebuild + pointer verify) is co-owned with L10, and publish-lock chaos is on L12's weekly calendar
* [Runtime Observation Plane](/references/runtime-observation-plane.md) - the prod hard-deny probe is co-owned with L06, asserting both validator and IAM layers
* [B15 Operations, Telemetry, and Recovery](/references/b15-operations-telemetry-and-recovery.md) - the build PRD implementing the alarm catalog, drill automation, and recovery tooling
* [Lineage Platform Acceptance](/references/lineage-platform-acceptance.md) - live canaries, the hard-deny probe, and chaos-drill cadence are normative acceptance for this component

## Citations

1. [12-security-observability-operations.md](/Users/sowmiyamohankumar/Documents/lineage-collector-v1/docs/component-prds/12-security-observability-operations.md)
