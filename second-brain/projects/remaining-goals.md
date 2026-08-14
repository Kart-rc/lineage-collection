---
type: Project
title: Lineage Platform Remaining Goals
description: The G1-G7 goal ledger tracking what remains before the lineage platform is complete, with every open item blocked on an owner decision or human step.
resource: /Users/sowmiyamohankumar/Documents/lineage-collector-v1/docs/remaining-goals.md
tags: [lineage, goals, delivery, status]
timestamp: 2026-08-14T11:30:00Z
---

# Lineage Platform Remaining Goals

Goal: deliver a production-shaped lineage platform — safe exact-revision acquisition, durable
orchestration of static plus runtime evidence, reviewable proposals, fenced publication, product
API/UI, and a proven AWS deployment. Complete only when implementation, evidence, architecture,
live AWS proof, and PR review gates are all satisfied and merged to `main`.

## Goal state (last reconciled 2026-08-11)

| Goal | Status | What remains |
|---|---|---|
| G1 target AWS architecture | IN_PROGRESS | Owner sign-off on the rendered target-only review |
| G2 safe remote Git acquisition | IN_PROGRESS | One opted-in GitHub fetch of the pinned Petclinic commit (`LINEAGE_REAL_REMOTE_ACQUISITION=1`) |
| G3 durable collection submit/status API | IN_PROGRESS | AWS `submit_collection` deferred to G6; it fails closed with `501 COLLECTION_SUBMIT_NOT_CONFIGURED` |
| G4 repository-collection product flow | IN_PROGRESS | Owner sign-off on the browser-smoked experience |
| G5 Petclinic product-flow proof | IN_PROGRESS | Owner sign-off; API and browser flows already pass the 15/10/5, 131-path oracle |
| G6 live AWS evidence | EXTERNAL_REQUIRED | Approved account/profile/region, opt-in, ephemeral deploy/smoke/cleanup |
| G7 PR review closure and merge | IN_PROGRESS | Merge the review-findings follow-up branch, re-verify `origin/main` |

Execution order: G2 → G3 → G4 → G5, with G1 in parallel; G6 and G7 converge last. Nothing further
can be implemented autonomously — each item needs a named owner action.

## Standing safety boundaries

Never invent enterprise CTX values or convert `AWS_REQUIRED`/`NOT_CONFIGURED` into passes; never
enable production runtime collection; never leak source, paths, credentials, or raw process output
into evidence, errors, telemetry, or PR comments; never execute repository code during static
collection; do not use CodeRabbit.

Latest retained real-repository evidence checksum: `sha256:60585677f9ab…` (2026-08-12 run,
15 edges / 10 reads / 5 writes / 8 residue / 0 unresolved), superseding `sha256:84d6345e…`.

## Related

* [Lineage Collector](/projects/lineage-collector.md) - the project this goal ledger completes
* [Lineage Platform Target AWS Architecture](/references/lineage-platform-target.md) - the G1 deliverable awaiting owner review
* [Implementation and Evidence Coverage](/references/prototype-coverage.md) - the normative evidence labels the goal statuses rest on
* [Lineage Platform Executable Acceptance Specification](/references/lineage-platform-acceptance.md) - defines the verification matrix each goal must satisfy
* [PRD Ambiguity Register](/references/prd-ambiguities.md) - the CTX seams and open decisions that keep G6 external

## Citations

1. [remaining-goals.md](/Users/sowmiyamohankumar/Documents/lineage-collector-v1/docs/remaining-goals.md)
