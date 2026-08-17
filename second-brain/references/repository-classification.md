---
type: Reference
title: L13 Repository Classification and Eligibility PRD
description: "Decides what every repository or monorepo path is before any expensive analysis runs — its class, baseline treatment, and on-change treatment — via a strict 7-level evidence precedence over a 9-class taxonomy, where UNKNOWN is never silent."
resource: /Users/sowmiyamohankumar/Documents/lineage-collector-v1/docs/component-prds/13-repository-classification.md
tags: [lineage, component-prd, classification, eligibility, policy]
timestamp: 2026-08-14T11:30:00Z
---

# L13 Repository Classification and Eligibility PRD

## Purpose

L13 (created by readiness decision Q4 to close gap G-L03-1) classifies every inventoried repo or monorepo path before costly analysis: what class it is, how the baseline treats it, and what happens on change. It reads structure only — never executes code — and guarantees 100% of repos carry a decision: included, excluded (with reason/evidence/owner/policy version), path-level MIXED, or UNKNOWN routed to a review queue. A critical active repo cannot remain UNKNOWN at baseline completion (blocks, per L03).

## Key requirements

- **Evidence precedence (7 levels, strict order):** governed catalog metadata → TAS association → CI/CD deployable-artifact evidence → deterministic manifests/structure → SBOM/locks → CloudWatch deploy association → heuristics (which always require review). The first decisive level wins and is recorded; conflicts *within* a level yield UNKNOWN with evidence attached.
- **Class taxonomy (9):** APPLICATION_RUNTIME and DATA_PIPELINE are included (incremental lineage, plus native for pipelines); CONTRACT_SCHEMA_SOURCE is metadata-only; SHARED_LIBRARY, INFRASTRUCTURE, DOCUMENTATION, TEST_AUTOMATION are excluded with class-specific on-change treatments (e.g. shared-library change re-analyzes affected consumers); MIXED_MONOREPO routes per changed path; UNKNOWN quarantines for review.
- **Guardrails:** an LLM can propose a class but never exclude; excluding on LLM or heuristic evidence alone is forbidden; manual overrides always expire (default 90 d, owner notified) and require a governance role.
- **Determinism and policy governance:** same inputs + same policy version → identical decision (replay-tested); policy is a versioned artifact with a preview mode that diffs decisions across the whole estate with zero side effects; a preview-diff gate blocks regressive policy releases.
- **Decision record:** `ClassificationDecision { repoOrPath, class, baselineTreatment, onChangeTreatment, evidenceLevelUsed, evidenceRefs, policyVersion, decidedAt, expiry? }` — immutable history, superseded never edited. Re-classification triggers on inventory delta, manifest change, TAS/catalog change, or policy release.
- **Scale:** classify 10,000 repos within the baseline planning window cheaply (metadata + manifests, no checkout beyond shallow file listing).

## Interfaces

The decision schema is a contract with L03 (workflow branch selection, baseline blocking), L04 (monorepo path scoping), and L11 (UNKNOWN queue and override flow). State: S3 decision history + DynamoDB effective registry.

## Related

* [Orchestration and Scheduling](/references/orchestration-and-scheduling.md) - L03 consumes classification decisions to pick workflow branches and blocks baseline on critical UNKNOWNs; the delivery plan ships them as one unit
* [SCA Engine](/references/sca-engine.md) - L04 honors L13's per-path scoping for mixed monorepos
* [APIs, Review UI, and PR Gate](/references/apis-review-ui-and-pr-gate.md) - the UNKNOWN review queue and governed-override flow round-trip through L11
* [B05 Classifier and Orchestrator](/references/b05-classifier-and-orchestrator.md) - the build PRD implementing the precedence engine and policy versioning

## Citations

1. [13-repository-classification.md](/Users/sowmiyamohankumar/Documents/lineage-collector-v1/docs/component-prds/13-repository-classification.md)
