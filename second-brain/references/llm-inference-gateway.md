---
type: Reference
title: LLM Inference Gateway
description: L05 answers only what SCA could not, with SCA's own facts as context, and guarantees nothing unvalidated reaches the merge via a four-stage guardrail chain.
resource: /Users/sowmiyamohankumar/Documents/lineage-collector-v1/docs/component-prds/05-llm-inference-gateway.md
tags: [lineage, component-prd, llm, guardrails, cache]
timestamp: 2026-08-14T11:30:00Z
---

# LLM Inference Gateway

## Purpose

L05 fills SCA's coverage gaps by inference and nothing more. Its edges are always marked inferred and capped: LLM-only provenance never gates a PR, never auto-publishes, and never reaches the highest confidence band. Serving the PRGate is refused by design; model hosting stays behind the enterprise Bedrock gateway.

## Key requirements and invariants

- Tiered triggers: T1 residue from Incremental on cache miss; T2 registered no-grammar files on Baseline/Nightly; T3 nightly verification samples (calibration only, bulk lane). PR-path requests get a typed rejection.
- Chunking, not whole-repo prompting: span ± enclosing function + imports + config keys, ≤ 8k tokens, split along AST boundaries; chunk digests are stable for identical code.
- Context assembly injects SCA facts + known repo URNs, and the prompt records which facts were provided (audit and calibration); SCA facts are stored independently and never overwritten.
- Guardrail chain, ordered: schema validate → citation exists in the digest → URN resolves via L01 (same resolver pin as the run) → budget check. First failure stops the chain and persists a reject with failedGuardrail. 100% of responses either pass or land in a logged reject — zero unvalidated edges reach L07; free text is forbidden.
- Cache keyed (chunk digest, promptVersion, modelId), shared across workflows; hit ≥ 90% steady-state is a launch gate — unchanged code is never re-billed. Prompt/model bumps use staged invalidation: a rollout record, bounded nightly re-derivation, old entries served until replaced; fleet-wide synchronous invalidation is forbidden.
- `unresolvable[]` is a first-class answer (not an error) that feeds the runtime watch list.
- Budget ledger enforces tokens/repo/day and per-lane caps; nothing is discarded silently — every response, reject, and rollout persists to L08 with correlation fields.

## Constraints and failure semantics

T1 round-trip ≤ 30 s p95 including guardrails; zero overspend days. Gateway degradation never blocks Incremental completion — the LLM stage is skippable-with-record and edges arrive later via re-merge. Budget exhaustion defers to the next window. Chunks contain source code (encrypted, evidence-only retention); data-plane values are never prompt-injected.

## Related

* [Static Code Analysis Engine](/references/sca-engine.md) - L04 residue and its reason codes are the input contract that drives tier routing
* [URN Grammar and Resolver Library](/references/urn-and-resolver-library.md) - the URN guardrail resolves every cited name via L01 under the run's pin
* [Consolidation, URN Merge, and Confidence](/references/consolidation-and-confidence.md) - only guardrail-passing edges reach the L07 merge, with promptVersion and citation in provenance
* [Runtime Observation Plane](/references/runtime-observation-plane.md) - unresolvable spans route to the runtime watch list for later corroboration
* [LLM Inference Gateway (b07)](/references/b07-llm-inference-gateway.md) - the build unit delivering the gateway, cache, and guardrail chain

## Citations

1. [05-llm-inference-gateway.md](/Users/sowmiyamohankumar/Documents/lineage-collector-v1/docs/component-prds/05-llm-inference-gateway.md)
