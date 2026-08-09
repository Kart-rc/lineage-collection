# B16 — Platform infrastructure and delivery

Normative requirements: [L14 AWS infrastructure](../component-prds/14-aws-infrastructure-and-deployment.md),
[L15 non-functional requirements](../component-prds/15-non-functional-requirements.md) and
[L16 delivery plan](../component-prds/16-delivery-plan-and-dependencies.md).

## Ownership

Track A/platform owns CDK stacks, environment configuration, IAM/network boundaries, runtime assets,
promotion and rollback. Component owners own handler entry points, sizing, alarms and deploy checks.

## Boundary

Package nine independently configured Lambda compute units plus the SCA Fargate worker and their
managed stores, queues, streams, workflow definitions, graph, telemetry and delivery controls.

## Contracts

Fixture configuration is valid only for local synth/tests; production configuration is mandatory and
fail-closed. Stack outputs identify exact resource, handler/image, version, alias and policy artifacts.

## State and failure model

Synth/package is reproducible. Production resolution rejects fixture/missing values. Deployment is
ordered, observable and rollbackable; failed canaries cannot advance aliases or workflow definitions.

## Data ownership

B16 owns IaC source, templates, packaged assets, dependency locks/SBOMs and deployment outputs. It does
not own lineage domain data, runtime decisions or mutable secrets.

## Infrastructure bill of materials

CDK bootstrap/pipelines, VPC/endpoints, KMS, DynamoDB, S3, SQS/DLQ, Kinesis, Step Functions, Lambda
aliases/reserved concurrency, ECS/Fargate/ECR, Neptune, CloudWatch/OTel, IAM, backup and budgets.

## Local adapter

CDK assertions/synth, deterministic packaging and fake-client smoke tests validate structure without
AWS credentials. These checks prove templates, never live IAM, service quotas, scale or recovery.

## Security and privacy

No wildcard data-plane IAM, public data services or standing deployment credentials. Use scoped roles,
private endpoints, KMS, secret references, image/dependency scanning and auditable promotion.

## SLOs

Local synth/package must be reproducible and bounded. Production deployment success, cold-start,
capacity, availability and recovery objectives remain `AWS_REQUIRED` until measured in the target.

## Observability

Record source/lock/image/template digests, config/environment, deploy/canary/rollback outcome, drift,
resource versions, alarm state and correlation to acceptance evidence.

## Acceptance criteria

| Acceptance ID | Requirement | Scenario and evidence | Gate |
|---|---|---|---|
| B16-AC-001 | L14-L16 deployable, least-privilege units | Clean synth/package proves all compute/data/workflow resources and scoped IAM; live deploy/smoke/scale/DR remains AWS_REQUIRED until run in an approved account | Every PR, deployment and environment gate |

## Deployment and rollback

Promote immutable assets through environment-specific stacks, database/workflow compatibility checks
and canary aliases. Roll back aliases/definitions/config in dependency order; never erase durable state.

## Dependencies

B01-B15 versioned artifacts plus approved account, regions, topology, network, identity, encryption,
retention, quotas, recovery, observability and cost CTX values.

## Definition of Ready

Target account/region, CTX configuration, bootstrap/trust, boundaries, quotas, rollback compatibility,
asset provenance, acceptance gate and operational owners are approved.

## Definition of Done

Clean checkout installs, tests, synthesizes and packages deterministically; least-privilege assertions
and fail-closed config pass; deployed claims carry evidence; `B16-AC-001` is retained.
