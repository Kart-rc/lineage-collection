---
type: Reference
title: B16 Platform Infrastructure and Delivery
description: Delivers the CDK-based IaC, packaging, and promotion pipeline that deploys nine Lambda compute units plus the SCA Fargate worker with least-privilege IAM and fail-closed production configuration.
resource: /Users/sowmiyamohankumar/Documents/lineage-collector-v1/docs/build-prds/B16-platform-iac-and-delivery.md
tags: [lineage, build-prd, iac, cdk, delivery]
timestamp: 2026-08-14T11:30:00Z
---

# B16 Platform Infrastructure and Delivery

The final build unit (normative sources: L14 AWS infrastructure, L15 NFRs, L16 delivery plan),
owned by Track A/platform. It packages nine independently configured Lambda compute units plus the
SCA Fargate worker together with their managed stores, queues, streams, workflow definitions,
graph, telemetry, and delivery controls. Component owners keep ownership of handler entry points,
sizing, alarms, and deploy checks.

## What it delivers

CDK stacks, environment configuration, IAM/network boundaries, runtime assets, promotion, and
rollback. B16 owns IaC source, templates, packaged assets, dependency locks/SBOMs, and deployment
outputs — never lineage domain data, runtime decisions, or mutable secrets.

## Configuration and fail-closed model

Fixture configuration is valid **only** for local synth/tests; production configuration is
mandatory and fail-closed — production resolution rejects fixture or missing values. Stack outputs
identify the exact resource, handler/image, version, alias, and policy artifacts, so every deploy
is traceable to acceptance evidence.

## Key invariants

- Synth/package is reproducible from a clean checkout (install, test, synthesize, package
  deterministically) — a per-PR gate.
- Deployment is ordered, observable, and rollbackable; **failed canaries cannot advance aliases or
  workflow definitions**; rollback proceeds in dependency order and never erases durable state.
- Least privilege is asserted: no wildcard data-plane IAM, no public data services, no standing
  deployment credentials — scoped roles, private endpoints, KMS, secret references, image and
  dependency scanning, auditable promotion (B16-AC-001).
- Local CDK assertions/synth and fake-client smoke tests prove templates only; live IAM, quotas,
  scale, and recovery remain `AWS_REQUIRED` until run in an approved account.

## Bill of materials and seams

CDK bootstrap/pipelines, VPC/endpoints, KMS, DynamoDB, S3, SQS/DLQ, Kinesis, Step Functions,
Lambda aliases with reserved concurrency, ECS/Fargate/ECR, Neptune, CloudWatch/OTel, IAM, backup,
budgets. It depends on versioned artifacts from B01-B15 plus CTX values for account, regions,
topology, network, identity, encryption, retention, quotas, recovery, observability, and cost.

## Related

* [Infrastructure and Deployment Spec](/references/infrastructure-and-deployment-spec.md) - the normative L14 component PRD defining the AWS infrastructure B16 codifies as CDK.
* [Delivery Plan and Dependencies](/references/delivery-plan-and-dependencies.md) - the L16 normative source for B16's promotion ordering and dependency rules.
* [Build PRD Overview](/references/build-prd-overview.md) - B16 is the final Delivery-tier unit consuming versioned artifacts from every prior build unit B01-B15.
* [B12 Publisher and Projection Manager](/references/b12-publisher-and-projection-manager.md) - B16 provisions the Neptune and control-plane resources B12 publishes into.
* [B15 Operations, Telemetry and Recovery](/references/b15-operations-telemetry-and-recovery.md) - B16 deploys the alarms, backup, and telemetry infrastructure B15 defines, and shares the AWS_REQUIRED evidence discipline.

## Citations

1. [B16-platform-iac-and-delivery.md](/Users/sowmiyamohankumar/Documents/lineage-collector-v1/docs/build-prds/B16-platform-iac-and-delivery.md)
