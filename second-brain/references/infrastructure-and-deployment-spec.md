---
type: Reference
title: L14 Infrastructure, Deployment, and Fault-Tolerance Specification
description: "The platform layer everything deploys through: monorepo + CDK (TypeScript) stack layout, four-account topology, per-component AWS mapping, CI/CD with canary/blue-green rollout, and a consolidated fault-tolerance model sized for 10,000 repos."
resource: /Users/sowmiyamohankumar/Documents/lineage-collector-v1/docs/component-prds/14-infrastructure-and-deployment-spec.md
tags: [lineage, component-prd, infrastructure, cdk, deployment]
timestamp: 2026-08-14T11:30:00Z
---

# L14 Infrastructure, Deployment, and Fault-Tolerance Specification

## Purpose

L14 pins the implementation stack (readiness decision Q9) and the deployment substrate for every component. Normative choices: a single-trunk **monorepo**; **AWS CDK in TypeScript** (one app, one stack per component plus shared stacks, no console resources); **Python 3.12** for all services/Lambdas/Fargate; resolver as Python + JVM libraries generated from one rule source (Node/Go in P1); TypeScript/React SPA. The repo layout puts contracts, resolver, and correlation libraries under `libs/`, services per component under `services/`, plus `seeded-repos/` fixtures and `runbooks/` linked from every alarm.

## Topology

Four accounts: `ldp-tooling` (CI/CD, deploys into all), `ldp-dev` (ephemeral, fixtures only), `ldp-staging` (full platform, hermetic integration + live canaries), `ldp-prod` (the platform — which collects lineage FOR all subject environments, while runtime observation of prod apps stays hard-denied). One VPC per account, private subnets across 3 AZs, VPC endpoints instead of NAT for data services; controlled egress (SCA tasks reach SCM + resolver snapshot only; LLM gateway reaches the enterprise Bedrock gateway only); Neptune/OpenSearch in isolated subnets reachable only via query/publisher services; WAF on the public API Gateway and webhook ingress.

## CI/CD and rollout

CDK Pipelines: PR → unit + contract gates → merge → build → staging deploy → integration smoke → manual approval → prod. Rollout per service class: Lambda CodeDeploy canary (10%/10 min), Fargate blue/green, Step Functions versioned by alias (in-flight runs finish on the old version). Stateful changes are additive-only with schemaVersion bumps; Neptune is versioned by namespace so publisher deploys never touch data. Data is never rolled back, only superseded. Resolver/rule-pack releases ride the determinant system (release → determinant bump → bounded re-queue); every policy constant and CTX seam lives in AppConfig or Secrets Manager, never code.

## Fault tolerance and capacity

Consolidated per-layer model: at-least-once + dedupe and DLQ redrive at intake; Step Functions retry/redrive with stage idempotency tokens; engines mark partial evidence, never silent, with a skippable-with-record LLM stage; merge idempotent by provenanceId; publish split-brain impossible by construction (fencing + conditional pointer); truth on S3 Object Lock with cross-Region replication, projections disposable; dependency outages degrade to last-good snapshot / skip-with-record / archive replay; Region loss handled by warm standby (RPO 15 min / RTO 4 h, drilled quarterly). Envelope: 10,000 repos, ~10k incremental runs/day, bursts absorbed in queues; interactive lane keeps reserved concurrency so PR-gate p95 ≤ 60 s holds under baseline load — all numbers are launch gates measured under production-like load, not assumptions. Cost controls: LLM budget hard caps, per-lane Fargate fleet caps, S3 lifecycle on non-truth prefixes, OpenSearch deferred to P1.

## Related

* [Security, Observability, and Operations](/references/security-observability-operations.md) - L14 deploys the policies, alarms, and DR machinery that L12 specifies as controls
* [NFR and Resiliency Spec](/references/nfr-and-resiliency-spec.md) - L15 consolidates and extends L14's fault-tolerance and capacity numbers into launch gates
* [Fenced Publication and Projections](/references/fenced-publication-and-projections.md) - namespace-versioned Neptune and the pointer contract are why publisher deploys never touch data
* [B16 Platform IaC and Delivery](/references/b16-platform-iac-and-delivery.md) - the build PRD that realizes this CDK app, pipeline, and account topology

## Citations

1. [14-infrastructure-and-deployment-spec.md](/Users/sowmiyamohankumar/Documents/lineage-collector-v1/docs/component-prds/14-infrastructure-and-deployment-spec.md)
