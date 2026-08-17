---
type: Reference
title: L16 Delivery Plan — Dependencies, Team Structure, Milestones
description: "The sequencing authority for the platform: hard dependency graph and critical path, seven interface freezes, four ownership tracks for a team of four, and vertical-slice milestones M0-M4 — because PRD numbering is data-flow order, not build order."
resource: /Users/sowmiyamohankumar/Documents/lineage-collector-v1/docs/component-prds/16-delivery-plan-and-dependencies.md
tags: [lineage, component-prd, delivery-plan, milestones, dependencies]
timestamp: 2026-08-14T11:30:00Z
---

# L16 Delivery Plan — Dependencies, Team Structure, Milestones

## Purpose

L16 corrects the trap in the L-numbering: it is data-flow order, and building serially would produce engines before consumers and defer integration risk to the end. The component cut stays as spec-of-record (each PRD owns one contract boundary — exactly what parallel teams need), and this plan adds the missing delivery mapping. The B01–B16 build PRDs translate these tracks into deployable boundaries; L16 remains the sequencing and dependency authority.

## Structural corrections

1. The **contracts library** (JSON Schemas, generated models, EvidenceRef, correlation lib) becomes a first-class shared artifact that freezes first. 2. **L03 + L13 ship as one delivery unit** (orchestration is untestable without classification decisions). 3. The **PR-gate slice spans L03 + L11** under a single owner (Track B builds end-to-end; Track D reviews the rendering contract).

## Dependency graph and freezes

Critical path: **contracts → L01/L08 → L02/L03 → L04 → L07 → L09/L10 → L11**; L05, L06, L13-deepening, and dashboards hang off it without blocking (skip-with-record makes the spine independent of the LLM). Seven interface freezes gate parallel work: F1 EvidenceRef/correlation/EventEnvelope (week 1, unblocks everyone) → F2 resolve() + URN grammar → F3 ScaEvidenceFile + residue → F4 ConsolidatedEdge + bands → F5 proposal/approval records → F6 pointer + manifest → F7 LlmResponse/Observation. Freezes are semver-pinned; a post-freeze change is a major bump with a migration note, enforced by contract pacts.

## Tracks and milestones

Four tracks: A Foundations & Identity (contracts, L01, L08, L12/L14 baseline), B Flow (L02, L03+L13, PR-gate slice), C Engines (L04, L05, L06 P1 slice), D Trust & Experience (L07, L09, L10, L11). Every track lands its test-suite sections with the code — no separate QA phase. Milestones are vertical slices: **M0** foundations (freezes, store, resolver, pipeline; exit = another track lands a service through the pipeline); **M1** walking skeleton — one real push through the entire spine with no LLM or UI polish (exit = incremental-push scenario green end-to-end in staging); **M2** breadth — full taxonomy, remaining rule packs, LLM T1/T2, auto-publish + narrowing, impact API + SPA (exit = MVP launch-gate dashboard live: join ≥ 80%, cache ≥ 90%, audit < 2%; baseline on 50 real repos); **M3** PR-gate slice, promotion lambda, OTel slice, OpenSearch; **M4** full emitters, Nightly + LLM T3, DR completion.

## Risk absorption

Every open CTX seam sits behind an M0/M1 interface with fixtures; late LLM never blocks the spine; auto-publish being MVP-active means M2 baseline volume gives the first real reviewer-economics measurement with narrowing as the safety valve; single-owner bottleneck on Track A is mitigated by keeping F1/F2 small and pairing on L08.

## Related

* [Implementation Readiness Review](/references/implementation-readiness-review.md) - L16 supersedes the readiness review's build-order recommendation and closes its delivery-structure addendum
* [System Context](/references/system-context.md) - the component boundaries and contract seams this plan sequences are defined there
* [Urn and Resolver Library](/references/urn-and-resolver-library.md) - first spine component after contracts; its resolve() types are freeze F2
* [B01 Contracts and Correlation](/references/b01-contracts-and-correlation.md) - the build unit realizing the promoted first-class contracts library and F1 freeze
* [Lineage Collector](/projects/lineage-collector.md) - this document is the project's delivery and milestone authority

## Citations

1. [16-delivery-plan-and-dependencies.md](/Users/sowmiyamohankumar/Documents/lineage-collector-v1/docs/component-prds/16-delivery-plan-and-dependencies.md)
