---
type: Reference
title: System Context and Architecture
description: Normative shared specification of the lineage platform's boundary, component topology (L01-L16), platform invariants, and launch gates.
resource: /Users/sowmiyamohankumar/Documents/lineage-collector-v1/docs/component-prds/00-system-context.md
tags: [lineage, component-prd, architecture, invariants, system-context]
timestamp: 2026-08-14T11:30:00Z
---

# System Context and Architecture

## Purpose

The platform collects element-level lineage across ~10,000 repositories via three independent engines — static code analysis (SCA), LLM inference, and runtime observation — merges assertions by canonical URN, and publishes only human- or policy-approved edges to versioned graph projections. It owns lineage metadata and audit history only; source repos, the enterprise catalog, deployment, test execution, and data-plane payloads stay out of scope.

## Authoritative architecture rules

The one-line rule: EventBridge routes, SQS buffers, Step Functions coordinates, Fargate analyzes, S3 preserves, DynamoDB controls, Neptune traverses, and a human (or audited auto-publish policy) approves. Key corollaries:

- The enterprise catalog is the URN authority; one shared resolver (L01) normalizes every name at intake — a guessed URN is a defect.
- S3 evidence, proposals, and accepted manifests are immutable truth; Neptune/OpenSearch are rebuildable projections only.
- The LLM answers only what SCA could not; it proposes, never gates.
- Runtime observation is integration-test only, metadata only; production is hard-denied at IAM and again at validation.
- Agreement across independent mechanisms sets confidence; absence is never negative evidence.
- Only parser-exact SCA edges may auto-publish (sampled-audit policy); all other material change needs explicit human approval.

## Topology

Sixteen components: L01 resolver, L02 intake/queues, L03 orchestration, L04 SCA, L05 LLM gateway, L06 runtime plane, L07 consolidation/confidence, L08 evidence store, L09 proposal/review, L10 fenced publication, L11 APIs/UI, L12 security/operations, L13 classification, L14 infrastructure, L15 NFR spec, L16 delivery plan. Flow: intake → orchestration → engines → evidence store → consolidation → proposal/review → fenced publication → APIs/UI.

## Platform invariants (the nine)

Every trigger maps to a run, dedupe, or quarantine — never silently dropped. Every name resolves or is quarantined with a gap report. Every consolidated edge carries verbatim provenance and a derivable band. LLM-only provenance never gates a PR or reaches the highest band. Absent runtime evidence never demotes; decay needs N observed-silent cycles. Manifests and approvals are never updated in place. The graph pointer advances only under a valid fencing token. Projections rebuild from S3 without losing approved state. Production cannot grant runtime sessions.

## Launch gates and correlation

Measured gates: URN join rate ≥ 80%, LLM cache hit ≥ 90%, auto-publish audit disagreement < 2%, review queue below reviewer capacity, 10,000-event burst absorbed, projection rebuild drill passes. Every artifact carries the correlation contract fields (correlationId, runId, repo, digest, env, system, plus contextual ids); unknown fields are absent, never empty strings.

## Related

* [URN Grammar and Resolver Library](/references/urn-and-resolver-library.md) - L01 is the identity foundation every rule in this context depends on
* [Consolidation, URN Merge, and Confidence](/references/consolidation-and-confidence.md) - L07 is the architectural seam where the three-engine agreement model is realized
* [Evidence Store and Cache](/references/evidence-store-and-cache.md) - L08 embodies the "S3 is immutable truth, projections rebuild" rule
* [Lineage Platform Target](/references/lineage-platform-target.md) - hub concept for the target architecture this document normatively defines
* [Contracts and Correlation (b01)](/references/b01-contracts-and-correlation.md) - build unit implementing the correlation contract and shared schemas defined here

## Citations

1. [00-system-context.md](/Users/sowmiyamohankumar/Documents/lineage-collector-v1/docs/component-prds/00-system-context.md)
