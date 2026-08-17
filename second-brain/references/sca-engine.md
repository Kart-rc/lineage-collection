---
type: Reference
title: Static Code Analysis Engine
description: L04 deterministically turns a repository at an exact digest into provable lineage edges plus an explicit residue list of what it could not resolve.
resource: /Users/sowmiyamohankumar/Documents/lineage-collector-v1/docs/component-prds/04-sca-engine.md
tags: [lineage, component-prd, sca, static-analysis, residue]
timestamp: 2026-08-14T11:30:00Z
---

# Static Code Analysis Engine

## Purpose

L04 is the deterministic backbone: it never guesses. Given a task spec (repo, digest, scope, resolver pin), it shallow-clones at the digest with per-run scoped read-only credentials, routes rule packs by manifest-driven language detection, runs deterministic parsers (sqlglot dialect matrix, dbt manifest, OpenAPI/proto) and tree-sitter AST rule packs (Python, Java/Kotlin, JS/TS, Go), resolves dynamic names from config, and emits a `ScaEvidenceFile` of edges, residue, datasetsSeen, and stats. Auto-publish and the PR gate depend on it.

## Key requirements and invariants

- Determinism is CI-enforced: same (digest, ruleset, config) → byte-identical evidence file; any replay diff is a release blocker.
- Every edge carries file:line + AST-path (or parser-source) evidence — no edge without proof.
- Everything unresolved becomes residue with a reason code (dynamic-name, reflection, unparseable-type), the file/span, nearby SCA facts, and known repo URNs — this residue is the context handoff contract to L05, and nothing is silently skipped. No-grammar file types are listed for LLM Tier-2, never dropped.
- Config resolution is all-or-residue: every placeholder resolves deterministically or the span becomes residue.
- Names go through the L01 resolver at intake under one snapshot pin; raw names are never persisted as identity.
- Parser-exact edges qualify for the L09 auto-publish class.
- Analysis is repo-local (L07 stitches cross-repo by URN), static only (never runs user code), and workspaces/credentials never survive the run.

## Interfaces

Consumes L03 task specs. Produces `ScaEvidenceFile` keyed `evidence/sca/{repo}/{digest}/{ruleset-ver}.json` — a schema contract with L07 (unknown versions rejected). Rule packs implement `match(ast) → Candidate[]`. Pipeline: `CHECKOUT → DETECT/ROUTE → PARSE → AST-WALK → CONFIG-RESOLVE → RESOLVE(URN) → EMIT`.

## Constraints and failure semantics

Median repo ≤ 10 min on standard Fargate size; hostile repos fail bounded, never hung; one task per (repo, digest). Pack crashes are isolated (other packs continue, evidence flagged partial); resolver quarantines let the run continue with a gap report; workspace timeouts mark evidence PARTIAL, never silently complete. Egress is restricted to SCM, S3, and the resolver snapshot.

## Related

* [URN Grammar and Resolver Library](/references/urn-and-resolver-library.md) - all emitted names resolve through L01 at intake under one snapshot pin
* [LLM Inference Gateway](/references/llm-inference-gateway.md) - the residue schema and reason codes are the contract that drives L05 tiering
* [Consolidation, URN Merge, and Confidence](/references/consolidation-and-confidence.md) - L07 consumes the evidence contract and stamps parser-exact edges for auto-publish
* [SCA Worker and Rule Packs (b06)](/references/b06-sca-worker-and-rule-packs.md) - the build unit shipping the parsers, rule packs, and determinism gate
* [Lineage Platform Acceptance](/references/lineage-platform-acceptance.md) - seeded matrix repos with expected-lineage.json and the determinism replay gate are explicit acceptance machinery

## Citations

1. [04-sca-engine.md](/Users/sowmiyamohankumar/Documents/lineage-collector-v1/docs/component-prds/04-sca-engine.md)
