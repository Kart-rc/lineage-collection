---
type: Reference
title: Lineage Platform Target AWS Architecture
description: The single canonical view of the production AWS target, an eight-layer topology with truthful per-component evidence status and explicit trust invariants.
resource: /Users/sowmiyamohankumar/Documents/lineage-collector-v1/docs/architecture/lineage-platform-target.md
tags: [lineage, architecture, aws, target, invariants]
timestamp: 2026-08-14T11:30:00Z
---

# Lineage Platform Target AWS Architecture

The authoritative production-AWS-only view (59 nodes, 88 labeled edges, normative Mermaid with a
generated offline HTML companion via `make architecture`). It deliberately shows no SQLite, local
filesystem, Vite, or in-process worker; local adapters are normative for verification but live in
the architecture refactor design, not here.

## Eight layers

1. Triggers and evidence sources (GitHub App, Jenkins, TAS, schedules, catalog snapshots, instrumented non-production runtimes)
2. Edge and durable intake (API Gateway, normalization Lambda, EventBridge bus/archive, interactive/events/bulk SQS lanes with DLQs, Kinesis runtime-evidence lane)
3. Durable control plane (DynamoDB command/ledger/proposal/pointer tables, stage execution and lease manager, coverage completeness gate)
4. Versioned orchestration (Baseline, Incremental, PRGate, NightlyReconciliation, Deployment promotion, runtime-session Step Functions; repository classifier)
5. Collection and evidence engines (exact-revision acquisition, Fargate SCA workers, Java/Spring + SQL + Python analyzer cells, bounded residue, OTel/OpenLineage metadata-only paths)
6. Evidence, consolidation, and trust (immutable S3 evidence/package stores, deterministic consolidation, proposal, human review gate, fenced publisher)
7. Projections and product surfaces (Neptune, OpenSearch, product API, read-only PR gate, CloudFront SPA, React app, OIDC identity)
8. Security, operations, and recovery (IAM, KMS, AppConfig kill switches, CloudWatch, X-Ray, CloudTrail, backup, redrive/replay)

## Invariants drawn into the diagram

- Human review is a gate, not a step — the only path from proposal to publication.
- Deployment promotes an exact artifact digest compared against the pointer table.
- Runtime evidence is non-blocking, metadata-only corroboration; it never gates consolidation.
- Coverage completeness gates consolidation: every tracked path needs a terminal disposition.
- Publication is fenced by a monotonic fencing-token compare-and-set on the pointer table.

Status classes are truthful evidence levels (`verified`, `synthesized`, `partial`, `planned`); no
component carries live AWS evidence — every AWS runtime path stays `AWS_REQUIRED` until the
ephemeral deploy/smoke runs. Evidence boundaries forbid repository content, runtime payloads, or
secrets from leaving the analysis engines; API errors carry stable codes and correlation IDs only.

## Related

* [Lineage Collector](/projects/lineage-collector.md) - the implementation this target architecture governs
* [System Context](/references/system-context.md) - the normative domain context the target realizes
* [Lineage Platform Executable Acceptance Specification](/references/lineage-platform-acceptance.md) - supplies the evidence labels behind the status legend
* [Implementation and Evidence Coverage](/references/prototype-coverage.md) - the per-component evidence levels behind each status class
* [B16 Platform IaC and Delivery](/references/b16-platform-iac-and-delivery.md) - the build unit that synthesizes and deploys this topology

## Citations

1. [lineage-platform-target.md](/Users/sowmiyamohankumar/Documents/lineage-collector-v1/docs/architecture/lineage-platform-target.md)
