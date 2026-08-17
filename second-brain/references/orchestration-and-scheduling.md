---
type: Reference
title: Workflow Orchestration and Scheduling
description: L03 coordinates the four Step Functions workflows plus the deploy-promotion Lambda, turning queued envelopes into visible, bounded, redrivable runs.
resource: /Users/sowmiyamohankumar/Documents/lineage-collector-v1/docs/component-prds/03-orchestration-and-scheduling.md
tags: [lineage, component-prd, orchestration, workflows, scheduling]
timestamp: 2026-08-14T11:30:00Z
---

# Workflow Orchestration and Scheduling

## Purpose

L03 owns the four Step Functions workflows — Incremental, Baseline, PRGate, NightlyReconciliation — plus the deploy-promotion Lambda (a 3-step conditional pointer swap, not a workflow). It sequences engine work by classification and determinants, and owns the run ledger. It sequences analysis, merge, and publish but never performs them.

## Key requirements and invariants

- Every dequeued envelope maps to exactly one run or a recorded no-impact decision.
- Incremental scope is determinant-bounded: a determinant change (config, ruleset, resolver, snapshot, prompt version) re-queues exactly the affected scopes; a one-file push re-derives only its edges; scope computation is auditable.
- Baseline runs classification first; UNKNOWN classification blocks critical completion.
- PRGate is read-only, parsers + cache only — calling the LLM gateway from the PR path is forbidden by design; verdicts are computed against the env baseline within a CI latency budget.
- Any failed run is redrivable from its failed stage with stage-level idempotency tokens, without re-running completed stages.
- Workflow state carries S3 references only, never large payloads; expensive analysis never starts before classification; there is no manual "run now" path.
- Deploy promotion: verify digest → conditional pointer swap → audit record; rollback reactivates the prior pointer; the promotion Lambda is the only non-publisher pointer writer and only via L10's conditional contract.

## Interfaces

Consumes `EventEnvelope` (L02 contract) and classification decisions. Emits engine task specs (repo, digest, scope, evidence prefix, resolver pin — engines never run without a pin). Run states: `QUEUED → RUNNING(stage…) → MERGED → PROPOSED → PUBLISHED | NO_IMPACT | FAILED(redrivable)`, persisted in the DynamoDB run ledger and surfaced as the L11 timeline.

## Constraints and failure semantics

Incremental push-to-proposal ≤ 30 min p95 (excluding review); 10,000-repo baseline completes within the planned window. Engine task failures retry with backoff then DLQ + redrive; empty determinant scope records no-impact; pointer-swap conflict aborts promotion and re-verifies the digest. Merge is invoked exactly once per run scope (idempotent).

## Related

* [Event Intake, Normalization, and Priority Queues](/references/event-intake-and-queues.md) - L02 lanes feed L03, which must account for every envelope it dequeues
* [Static Code Analysis Engine](/references/sca-engine.md) - L04 receives determinant-bounded task specs with the resolver pin from L03
* [LLM Inference Gateway](/references/llm-inference-gateway.md) - L05 is scheduled by L03 on cache miss and is explicitly banned from the PRGate path
* [Classifier and Orchestrator (b05)](/references/b05-classifier-and-orchestrator.md) - the build unit implementing the workflows, run ledger, and determinants table
* [Fenced Publication and Projections](/references/fenced-publication-and-projections.md) - the promotion Lambda swaps pointers only through L10's fenced contract

## Citations

1. [03-orchestration-and-scheduling.md](/Users/sowmiyamohankumar/Documents/lineage-collector-v1/docs/component-prds/03-orchestration-and-scheduling.md)
