# B13 — Query, impact and PRGate service

Normative requirements: [L11 APIs and PR gate](../component-prds/11-apis-review-ui-and-pr-gate.md)
and [L03 orchestration](../component-prds/03-orchestration-and-scheduling.md).

## Ownership

Track D owns versioned lineage/impact queries, traversal bounds, evidence detail and read-only PRGate
P1-P8 evaluation. Track B owns trigger integration; policy owners approve verdict rules.

## Boundary

Read one pinned active/requested projection and return bounded lineage/impact. PRGate pins head,
environment/fence, artifact, policy and coverage, then emits PASS/WARN/BLOCK without lineage writes.

## Contracts

APIs return namespace version, depth, truncation, edges/nodes or affected paths. PR checks include
stable check ID, all pins, verdict/reasons, deadline status and evaluated changes.

## State and failure model

Queries fail typed on missing version/invalid bounds. PRGate rechecks head/environment before return;
incomplete/stale/degraded/timeout/truncated/LLM-only evidence becomes explicit WARN, never false PASS.

## Data ownership

B13 owns query/check responses and stable provider check records. Graph/evidence truth is read-only;
check rendering cannot mutate proposals, coverage, packages or pointers.

## Infrastructure bill of materials

API Gateway, query/PRGate Lambdas, Neptune/OpenSearch read roles, DynamoDB check table, cache/CDN where
approved, provider check integration, WAF, KMS, reserved concurrency, alarms and traces.

## Local adapter

FastAPI endpoints, SQLite graph reader and fake head/environment/deadline providers prove bounded,
fresh and read-only behavior with deterministic snapshots.

## Security and privacy

Authorize domains/versions/evidence, avoid unauthorized existence leaks, constrain traversal and
payload size, authenticate provider writes and log decisions/pins rather than protected graph bodies.

## SLOs

PRGate returns by 120 seconds or WARN; traversal depth/result size are bounded. Production availability
and latency targets require deployed evidence and error-budget ownership.

## Observability

Emit query/check latency, version/pin, truncation, cache, verdict/reason, head/environment recheck,
deadline, projection degradation and read-only invariant failures.

## Acceptance criteria

| Acceptance ID | Requirement | Scenario and evidence | Gate |
|---|---|---|---|
| B13-AC-001 | L11 current bounded read-only gate | Force-push/environment change, incomplete coverage, projection outage, truncation and timeout never PASS; authoritative tables are byte-identical before/after | Every PR E2E/fault gate |

## Deployment and rollback

Canary API/check aliases and policy version; compare shadow verdicts. Roll back aliases/policy while
stable checks retain pinned inputs and prior outcomes.

## Dependencies

B01, B03, B05, B12 pointer/projection and provider/catalog CTX seams. B14 consumes APIs.

## Definition of Ready

Traversal limits, change types, verdict matrix, head/environment providers, deadline, authorization and
degradation policy are approved.

## Definition of Done

Bounds/freshness/read-only/degradation tests pass; check IDs are stable; IAM/alarms/runbooks and
`B13-AC-001` evidence exist.
