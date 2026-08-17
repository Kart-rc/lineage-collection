---
type: Reference
title: Component PRD Readiness Review — for Agent Implementation
description: "Per-component READY/GAPS/BLOCKED-ON-CTX verdicts on whether a coding agent can build from the PRDs, plus the nine decisions (Q1-Q9), the versioned constants table, and the 17-item open enterprise-context register."
resource: /Users/sowmiyamohankumar/Documents/lineage-collector-v1/docs/component-prds/99-implementation-readiness-review.md
tags: [lineage, component-prd, readiness, decisions, gaps]
timestamp: 2026-08-14T11:30:00Z
---

# Component PRD Readiness Review — for Agent Implementation

## Purpose

Audits every component PRD against one goal: confidence-scored lineage collection with impact analysis, implementable by a coding agent from these docs alone. Each PRD is checked for concrete schemas/algorithms per FR, valued or owner-named constants, no hidden enterprise knowledge, test coverage, and an explicit contribution to confidence + impact. Verdicts: READY, GAPS (start, but listed items must land before that FR), or BLOCKED-ON-CTX (enterprise input needed — kept OPEN, never invented). Historical "Test Suite" references map to the executable acceptance specification.

## Verdicts after the decision pass

READY: L02 intake, L08 store, L10 publication, L12 ops, L13 classification (new). READY after this pass: L01, L06, L07, L09, L11. Remaining authoring lifts (none block starting): G-L01-1 per-platform case/quote normalization tables as data; G-L03-3 stage-level ASL workflow specs; G-L04-1 Python rule-pack matcher tables as the template (the single largest lift); G-L05-1 prompt templates v1; G-L06-1 OTel attribute mapping table; G-L09-2 corpus JSON schema.

## Decisions applied (Q1–Q9, 2026-08-04)

- **Q1:** ordinal confidence bands only at MVP; numeric score deferred to calibrated P2+; math pinned in L07 §15. (One of the two decisions shaping the end goal.)
- **Q2/Q3:** impact analysis covers all six change types; bounded sync traversal (≤ 5) plus async full closure; spec written in L11 §15. (The other end-goal decision.)
- **Q4:** new L13 Repository Classification PRD (9 classes, 7-level precedence) closes G-L03-1.
- **Q5:** resolver MVP = Python + JVM; Node/Go in P1.
- **Q6:** decay N = 3. **Q7:** runtime OTel slice pulled into Phase 1.
- **Q8:** auto-publish active at MVP with 5% sampled audit and automatic narrowing; governance sign-off is a launch gate.
- **Q9:** stack pinned — monorepo, CDK (TypeScript), Python services, dual-runtime resolver from one rule source.

## Constants and CTX register

A versioned constants table (config, not code) pins decay N=3, 5% audit sample, 2% disagreement breach, ≥ 80% join-rate gate, 8k-token LLM chunk budget, ≤ 60 s PR-gate p95; leaves OPEN with named owners: LLM budget caps (cost governance), bulk-lane quotas (ops), retention durations (compliance). CTX-01…CTX-17 (catalog export schema, platform vocabulary, Jenkins/GitHub integration details, SQL dialects, Bedrock endpoint, Spark platform, TAS ownership mapping, PII flags, paging conventions, org audit baseline, …) stay open by design. The governing rule: every CTX item is a **named config seam** — implement the interface, stub with fixtures, never invent enterprise values.

## Addenda

Three gap-closing documents were spawned by this review: L14 (infrastructure/deployment), L15 (NFR/resiliency consolidation with proposed availability tiers), and L16 (delivery plan) — L16 supersedes this review's own build-order recommendation.

## Related

* [Delivery Plan and Dependencies](/references/delivery-plan-and-dependencies.md) - L16 was created by this review's addendum and supersedes its build-order section
* [Consolidation and Confidence](/references/consolidation-and-confidence.md) - Q1/Q6 pinned this component's band math and decay constant, the review's blocking confidence gap
* [APIs, Review UI, and PR Gate](/references/apis-review-ui-and-pr-gate.md) - Q2/Q3 produced its impact-analysis specification, the review's other blocking gap
* [Repository Classification](/references/repository-classification.md) - the L13 PRD exists because this review's Q4 gave classification a home
* [Lineage Platform Acceptance](/references/lineage-platform-acceptance.md) - the executable acceptance specification this review names as the normative test and evidence index

## Citations

1. [99-implementation-readiness-review.md](/Users/sowmiyamohankumar/Documents/lineage-collector-v1/docs/component-prds/99-implementation-readiness-review.md)
