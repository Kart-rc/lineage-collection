---
type: Reference
title: B13 Query, Impact and PRGate Service
description: Delivers versioned read-only lineage/impact query APIs and the pinned PRGate evaluation that emits PASS/WARN/BLOCK verdicts without ever writing lineage state.
resource: /Users/sowmiyamohankumar/Documents/lineage-collector-v1/docs/build-prds/B13-query-impact-and-pr-gate.md
tags: [lineage, build-prd, query, pr-gate, apis]
timestamp: 2026-08-14T11:30:00Z
---

# B13 Query, Impact and PRGate Service

Track D's product-tier read surface (normative sources: L11 APIs and PR gate, L03 orchestration).
It reads exactly one pinned active/requested projection and returns bounded lineage/impact results;
its PRGate arm evaluates checks P1-P8 against pinned inputs and emits PASS/WARN/BLOCK. Track B owns
trigger integration; policy owners approve verdict rules.

## What it delivers

Versioned lineage/impact queries with traversal bounds and evidence detail, plus read-only PRGate
evaluation with stable provider check records. API responses carry namespace version, depth,
truncation, and edges/nodes or affected paths; PR checks carry a stable check ID, all pins
(head, environment/fence, artifact, policy, coverage), verdict/reasons, deadline status, and
evaluated changes.

## Key invariants

- **Strictly read-only**: check rendering cannot mutate proposals, coverage, packages, or
  pointers; authoritative tables must be byte-identical before/after evaluation (B13-AC-001).
- **Never a false PASS**: incomplete/stale/degraded/timeout/truncated/LLM-only evidence becomes an
  explicit WARN. Force-push or environment change is caught by a head/environment recheck before
  the verdict is returned.
- PRGate returns within 120 seconds or degrades to WARN; traversal depth and result size are
  always bounded; queries fail typed on missing versions or invalid bounds.

## Data ownership and boundary

B13 owns query/check responses and stable provider check records. Graph and evidence truth are
read-only inputs pinned to a projection version published by B12.

## Infrastructure and constraints

API Gateway, query/PRGate Lambdas, Neptune/OpenSearch read roles, DynamoDB check table, provider
check integration, WAF, optional approved cache/CDN. Local adapter: FastAPI + SQLite graph reader
with fake head/environment/deadline providers over deterministic snapshots. Production
availability/latency SLOs require deployed evidence and error-budget ownership. Definition-of-Ready
seams: traversal limits, change types, verdict matrix, head/environment providers, deadline,
authorization, and degradation policy. Rollback swaps API/check aliases and policy versions while
stable check IDs retain pinned inputs and prior outcomes.

## Related

* [APIs, Review UI and PR Gate](/references/apis-review-ui-and-pr-gate.md) - the normative L11 component PRD covering these APIs and the PR gate verdict rules.
* [B12 Publisher and Projection Manager](/references/b12-publisher-and-projection-manager.md) - upstream owner of the pointer/projection versions B13 pins its reads to.
* [B14 Review and Operations UI](/references/b14-review-and-operations-ui.md) - the direct downstream consumer of B13's query and gate APIs.
* [B05 Classifier and Orchestrator](/references/b05-classifier-and-orchestrator.md) - upstream orchestration dependency (L03) for triggers and correlation.
* [B01 Contracts and Correlation](/references/b01-contracts-and-correlation.md) - supplies the contract and correlation foundations B13's typed responses build on.

## Citations

1. [B13-query-impact-and-pr-gate.md](/Users/sowmiyamohankumar/Documents/lineage-collector-v1/docs/build-prds/B13-query-impact-and-pr-gate.md)
