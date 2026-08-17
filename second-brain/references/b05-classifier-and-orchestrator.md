---
type: Reference
title: B05 Classifier and Workflow Orchestrator
description: Turns each durable command into an exact versioned workflow stage graph (Baseline, Incremental, PRGate, Nightly, deployment) with lease-based execution, deterministic redrive and complete coverage accounting.
resource: /Users/sowmiyamohankumar/Documents/lineage-collector-v1/docs/build-prds/B05-classifier-and-orchestrator.md
tags: [lineage, build-prd, orchestration, workflows, classification]
timestamp: 2026-08-14T11:30:00Z
---

# B05 Classifier and Workflow Orchestrator

B05 (Track B) owns classification policy, trigger decisions, workflow definitions, command/lease
execution, coverage completeness, redrive and terminal behavior. Its boundary: turn one durable
command into the exact versioned Baseline, Incremental, PRGate, Nightly or deployment stage graph,
where stages communicate only through immutable references and application ports. Domain engines
(B06-B13) own their bounded stage results; evidence bodies stay in B03, proposals/publications in
B11/B12.

## Key contracts and invariants

- Workflow definitions pin stage IDs, attempts, deadlines, retry class, side-effect mode,
  determinants, routes and terminal states.
- Coverage manifests account for every expected scope item and runtime join; unsupported or
  skipped work is recorded, and missing work makes coverage incomplete — unaccounted scope can
  never be COMPLETE.
- Commands transition through queued/running/retry/completed/terminal states under lease epochs;
  crashes redrive immutable checkpoints. Trigger policy never bypasses review or deployment
  artifact identity.
- B05-AC-001: kill after every stage side effect; redrive must converge to identical references,
  manifests and terminal state (every-PR fault matrix).

## Execution planes

AWS: Standard Step Functions for the four canonical workflows plus a dedicated deployment-promotion
Lambda, DynamoDB control tables, Lambda/ECS targets, EventBridge schedules and pinned aliases. The
local API/worker executes the *same definitions* with SQLite leases/checkpoints, fake clocks and
named failpoints — a correctness oracle, not a Step Functions emulator; local/ASL parity is part of
Definition of Done.

## Rollback and seams

Definitions are exported from code, drift is rejected, and state machines are published as
immutable versions routed through aliases; in-flight executions stay pinned and rollback restores
alias/routing. Depends on B01 contracts, B03 stores, B04 commands/lanes and B06-B13 stage ports.
Repository conventions and schedule/policy ownership remain CTX seams. Workflow latency targets are
release evidence — never inferred from local speed.

## Related

* [Orchestration and Scheduling](/references/orchestration-and-scheduling.md) - normative L03 requirements B05 implements
* [Repository Classification](/references/repository-classification.md) - normative L13 classification policy that drives trigger decisions
* [B04 Event Intake and Lane Router](/references/b04-event-intake-and-lanes.md) - upstream source of the durable commands B05 executes
* [B06 SCA Worker and Rule Packs](/references/b06-sca-worker-and-rule-packs.md) - downstream stage engine invoked through B05 stage ports
* [B03 Evidence and Control-Store Adapters](/references/b03-evidence-and-control-stores.md) - upstream stores holding checkpoints, control tables and evidence bodies

## Citations

1. [B05-classifier-and-orchestrator.md](/Users/sowmiyamohankumar/Documents/lineage-collector-v1/docs/build-prds/B05-classifier-and-orchestrator.md)
