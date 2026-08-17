---
type: Reference
title: L15 Non-Functional Requirements and Resiliency Specification
description: "Single consolidated table of every performance SLO plus the launch-gate additions: availability tiers with error budgets, RPO/RTO per data class, a dependency degradation-mode matrix, backpressure rules, and a claim-to-test verification map."
resource: /Users/sowmiyamohankumar/Documents/lineage-collector-v1/docs/component-prds/15-nfr-and-resiliency-spec.md
tags: [lineage, component-prd, nfr, slo, resiliency]
timestamp: 2026-08-14T11:30:00Z
---

# L15 Non-Functional Requirements and Resiliency Specification

## Purpose

L15 is the cross-cutting spec launch gates derive from: it merges every per-PRD NFR into one SLO table and adds what no component PRD owned — availability tiers, durability targets per data class, explicit degradation behavior, and backpressure rules. Several values are marked *proposed* pending operations sign-off.

## Consolidated SLOs (headline values)

Intake ack ≤ 500 ms p95 at 100/s; 10,000-event burst absorbed with zero loss; PR-gate verdict ≤ 60 s p95 in-check under bulk load; incremental push→proposal ≤ 30 min p95; SCA median repo ≤ 10 min; LLM T1 round-trip ≤ 30 s p95 including guardrails; merge ≤ 60 s p95; resolver ≤ 5 ms single / 300 ms batch; publish stage→swap ≤ 5 min median with 95% of approvals visible ≤ 60 s; one-hop query ≤ 2 s p95; evidence-ref read ≤ 100 ms p95.

## Availability tiers and error budgets (proposed)

T1 interactive (PR-gate path, query/review APIs) 99.9% — budget burn > 25%/week freezes features on that service. T2 pipeline 99.5%, measured as *runs completing without operator action* (queues mask short outages; DLQ growth counts against budget). T3 batch 99% weekly completion. The defining rationale: the pipeline is asynchronous, so availability means work *not lost and not stuck*, never synchronous uptime.

## Durability and degradation

Per data class: truth (evidence/proposals/approvals/manifests) and control state both RPO 15 min / RTO 4 h (S3 Object Lock + cross-Region replication; DynamoDB PITR); projections need no backup — rebuilt from manifests (≤ 8 h full rebuild, proposed); catalog snapshots serve last-good immediately. The degradation matrix names, per dependency failure, the automatic behavior, the user-visible state, and the invariant held — e.g. Bedrock down → skip-with-record, edges arrive later; Neptune down → PR gate WARN never silent PASS; Kinesis throttle → session closes INCOMPLETE and incomplete never promotes confidence; reviewer unavailability → queues age but the human gate is never bypassed.

## Backpressure and verification

Queues are the only elastic buffer; workflows never accept unbounded fan-out (Distributed Map concurrency caps). The interactive lane has reserved concurrency; only the bulk lane sheds; the LLM gateway sheds by tier (T3 first, then T2, never T1 residue); Kinesis never silently samples — producer backpressure surfaces in session manifests; shedding events are first-class telemetry counted against T2 budget. Every resiliency claim maps to a test with a cadence (nightly load/replay, weekly fencing chaos and prod hard-deny probe, quarterly rebuild and DR drills, monthly rotation of one fault-injection fixture per degradation row).

## Related

* [Infrastructure and Deployment Spec](/references/infrastructure-and-deployment-spec.md) - L15 makes L14's fault-tolerance and capacity behavior explicit as tiers, budgets, and gates
* [Security, Observability, and Operations](/references/security-observability-operations.md) - the DR drill, hard-deny probe, and error-budget dashboards are operated by L12
* [Fenced Publication and Projections](/references/fenced-publication-and-projections.md) - projections carrying no RPO because they rebuild from manifests is L10's disposability contract
* [Lineage Platform Acceptance](/references/lineage-platform-acceptance.md) - the claim-to-test verification map binds every resiliency claim to a scheduled acceptance test
* [B15 Operations, Telemetry, and Recovery](/references/b15-operations-telemetry-and-recovery.md) - the build unit wiring these budgets, alarms, and drill fixtures into running telemetry

## Citations

1. [15-nfr-and-resiliency-spec.md](/Users/sowmiyamohankumar/Documents/lineage-collector-v1/docs/component-prds/15-nfr-and-resiliency-spec.md)
