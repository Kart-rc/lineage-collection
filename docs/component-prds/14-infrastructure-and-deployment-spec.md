# 14-infrastructure-and-deployment-spec.md

# L14 Infrastructure, Deployment, and Fault-Tolerance Specification

## 1. Document Control

| Field | Value |
|---|---|
| Component | L14 (cross-cutting spec; implemented as the monorepo's platform layer) |
| Status | Draft for implementation |
| Launch phase | MVP foundation |
| Criticality | P0 — everything deploys through this |
| Primary owner | Lineage platform team (infrastructure) |
| Required approvers | Architecture, security, operations, cost governance |
| Authoritative sources | Lineage HLD §§1, 8–10; L12 PRD; readiness review Q9 (stack decision); 00-system-context §7 gates |

## 2. Language Stack and Repository Layout (Q9, normative)

- **Monorepo**, single trunk, one release train per component.
- **IaC: AWS CDK (TypeScript)** — one CDK app, one stack per component plus
  shared stacks; no console-created resources.
- **Services: Python 3.12** — Lambdas, Fargate task images, the LLM gateway,
  validators. **Resolver: Python + JVM** libraries generated from one rule
  source (MVP), Node/Go in P1.
- **SPA: TypeScript/React** behind API Gateway.

```text
lineage/
  infra/            # CDK app: stacks per component + shared
    stacks/{network,data,intake,orchestration,sca,llm,runtime,
            merge,review,publish,api,ops,classification}.ts
  libs/resolver/    # rule source + py/jvm builds + golden corpus
  libs/contracts/   # JSON Schemas, generated models (py+ts), EvidenceRef
  libs/correlation/ # L12 telemetry/correlation library (py+ts)
  services/{intake,orchestration,sca,llm-gateway,runtime-validator,
            merge,review-api,publisher,query-api,classification}/
  spa/review-ui/
  seeded-repos/     # Test Suite §2 fixtures + expected-lineage.json
  tests/{unit,integration,contract,live}/
  runbooks/         # linked from every alarm (L12)
```

## 3. Account and Environment Topology

| Account | Purpose | Notes |
|---|---|---|
| `ldp-tooling` | CI/CD, artifact registry, CDK pipelines | deploys into all others |
| `ldp-dev` | ephemeral developer stacks | seeded fixtures only |
| `ldp-staging` | full platform, hermetic integration + live canaries | mirrors prod sizing at reduced scale |
| `ldp-prod` | the platform itself | collects lineage FOR all envs; runtime observation of prod apps remains hard-denied (L06/L12) |

Note the distinction: the platform runs in `ldp-prod`, but observed-lineage
`env` (prod/staging URNs) refers to the *subject* systems' environments.
Cross-account roles: read-only SCM/catalog/TAS access via dedicated
integration roles per source (CTX-04/05/06 seams).

## 4. Network Topology

- One VPC per platform account; private subnets across **3 AZs**.
- Fargate tasks and Lambdas in private subnets; VPC endpoints for S3,
  DynamoDB, Kinesis, Secrets Manager, Bedrock gateway, CloudWatch — no NAT
  path for data services.
- Controlled egress: SCA task security group allows SCM + resolver snapshot
  only (L04 §10); LLM gateway egress allows the enterprise Bedrock gateway
  only (CTX-09).
- Neptune + OpenSearch in isolated subnets; access via the query/publisher
  services only.
- Public surface: API Gateway (Cognito OIDC) + webhook ingress endpoints;
  WAF on both.

## 5. Per-Component AWS Mapping (CDK stacks)

| Stack | Resources | Scaling |
|---|---|---|
| network | VPC, subnets, endpoints, SGs | — |
| data (L08) | S3 buckets + Object Lock, KMS keys, DynamoDB tables, replication | on-demand tables; alarms on throttle |
| intake (L02) | API GW webhook routes, normalizer λ, EventBridge bus + archive + rules, 3 SQS lanes + DLQs | λ concurrency reserved for interactive lane |
| classification (L13) | classifier λ/Fargate, policy in AppConfig | batch fan-out via Distributed Map |
| orchestration (L03) | 4 Step Functions, promotion λ, run-ledger table | Standard workflows; Distributed Map for baseline |
| sca (L04) | ECR image, Fargate task def, per-run credential broker | queue-depth target tracking on bulk lane |
| llm (L05) | gateway service (Fargate), cache table, budget ledger | per-lane rate limits; concurrency cap |
| runtime (L06) | session API (AppConfig+DDB), validator λ, Kinesis + Firehose | on-demand Kinesis; validator reserved concurrency |
| merge (L07) | merge λ/Fargate step, edge-ledger table + GSIs | idempotent; retry-safe |
| review (L09) | review API (Fargate), proposal tables, corpus writer | — |
| publish (L10) | publisher Fargate task, lock/pointer tables, Neptune cluster, OpenSearch (P1) | Neptune: 1 writer + 2 readers, 3 AZ |
| api (L11) | query API, SPA hosting (S3+CloudFront), PR-check renderer | CloudFront cached SPA |
| ops (L12) | dashboards, alarms, canaries, drill automation, CloudTrail wiring | — |

## 6. CI/CD and Deployment Strategy

- **Pipeline (CDK Pipelines in `ldp-tooling`):** PR → unit + contract suites
  (Test Suite §7 gates) → merge → build images/libs → deploy `ldp-staging` →
  integration smoke (§4 subset) → manual approval gate → deploy `ldp-prod`.
- **Rollout per service:** Lambda versions with CodeDeploy canary
  (10%/10min); Fargate services blue/green behind target groups; Step
  Functions versioned by alias — in-flight runs finish on the old version.
- **Stateful changes:** DynamoDB/S3 schema changes are additive-only with
  schemaVersion bumps (contracts enforce); Neptune is versioned by namespace
  (L10) so publisher deploys never touch data.
- **Rollback:** infrastructure = CDK stack rollback; services = alias/target
  swap back; data = never rolled back, only superseded (immutability).
- **Release trains:** resolver and rule-pack releases ride the determinant
  system (L01-FR-011, L04-FR-007): release → determinant bump → bounded
  re-queue. Prompt/model bumps follow L05's staged re-derivation.
- **Config:** AppConfig for policies/constants (decay.n, quotas, budget
  caps — the readiness constants table); every CTX seam is an AppConfig
  entry or Secrets Manager ref, never code.

## 7. Fault-Tolerance Model (consolidated)

| Layer | Mechanism |
|---|---|
| Intake | at-least-once everywhere + dedupe table; EventBridge archive replay; DLQ per lane with redrive runbooks |
| Workflows | Step Functions retry/backoff per stage; redrive from failed state; stage idempotency tokens; S3-reference payloads |
| Engines | task retry with fresh per-run creds; partial-evidence marked, never silent; skippable-with-record LLM stage (L05-NFR-002) |
| Merge | idempotent by provenanceId; optimistic concurrency on the ledger; commutativity canary |
| Publish | fencing token + conditional pointer transaction; staging discarded on any verify failure; split-brain impossible by construction (L10 property tests) |
| Data | S3 11-nines + Object Lock + cross-Region replication (truth classes); DynamoDB PITR + on-demand backup; projections disposable/rebuildable |
| Dependencies | catalog outage → last-good snapshot (L01); Bedrock outage → skip-with-record; GitHub outage → archive replay on recovery |
| Region | warm standby: replicated truth + CDK re-deploy; RPO 15 min / RTO 4 h drilled quarterly (L12-FR-006) |

## 8. Capacity and Scaling Envelope

10,000 repos; ≥1 deploy/repo/day (~10k incremental runs/day sustained,
bursts to 10k events absorbed in queues); baseline window sized by Fargate
bulk-lane fleet (target-tracking on queue depth, cap by cost policy);
interactive lane reserved concurrency so PRGate p95 ≤ 60 s holds under
baseline load. All numbers are launch gates measured under production-like
load (00-system-context §7), not assumptions.

## 9. Cost Controls

LLM budget ledger (L05) with hard caps; Fargate fleet caps per lane; S3
lifecycle on non-truth prefixes; OpenSearch deferred to P1 (DE P7); Neptune
reader scaling manual until query volume justifies auto.

## 10. Definition of Done

CDK app deploys the full platform into a clean account from trunk; pipeline
gates wired to the Test Suite; canary rollout + rollback demonstrated per
service class; DR drill green; every constant/CTX seam externalized to
AppConfig/Secrets with fixture defaults.
