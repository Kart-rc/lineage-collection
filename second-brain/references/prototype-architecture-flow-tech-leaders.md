---
type: Reference
title: "Throughline Prototype: Architecture Flow for Tech Leaders"
description: "Architecture brief for technology leadership presenting the two-plane collection design, trust bands, four de-risking amendments, gated rollout phases, and the three risks that matter."
resource: /Users/sowmiyamohankumar/Documents/lineage-collector-v1/Data Lineage Impact Platform-10/Architecture Flow - Tech Leaders.dc.html
tags: [prototype, throughline, lineage-collection, confidence, rollout]
timestamp: 2026-08-14T12:10:00Z
---

# Throughline Prototype: Architecture Flow for Tech Leaders

An architecture brief (v1.0, for technology leadership) that compresses the lineage-collection design
into one argument: a canonical, column-level lineage graph built from two independent kinds of
evidence — what the code says and what production proves — with a confidence score on every edge.
The wedge is explicitly "not another catalog" but column-level impact answers delivered in the pull
request with an explicit trust statement.

Key claims and structures:

- **Two planes of evidence.** Build time (predicted) supplies the complete superset of flows — every
  branch, guard, and transform tied to file and line — but is unproven. Runtime (observed) supplies
  proof of what executed but is silent about the rare-but-critical tail (quarter-end jobs, error
  branches, EU-only flows). Reconciled, each covers the other's blind spot; confidence = agreement × recency.
- **Pipeline** — Collection → Normalize (OpenLineage events, identity resolution to canonical URNs) →
  Reconcile (merge engine deduping by source/target/level; confidence engine) → Serve (canonical
  typed property graph, impact analysis, CI/CD gate, UI and agents).
- **Six signals** — code analysis (parsers first, tree-sitter locates, LLM resolves only the residue),
  CI/CD diff per PR, Spark via OpenLineage, warehouse-native lineage (Unity Catalog, Snowflake
  ACCESS_HISTORY, BigQuery Dataplex — zero instrumentation), orchestrator and dbt collectors, and
  OTel traces (interactions and recency only, never column lineage). A Dask collector is deferred as
  genuine R&D since no lineage ecosystem exists for it.
- **Trust bands** — Verified (score ≥ 85, code and runtime agree recently; the only band allowed to
  block a merge), Probable (65–84, warns never blocks), Inferred (< 65, static-only, drawn dashed).
  The one rule: prediction alone is capped below Verified (max 64), which is what makes LLM-assisted
  extraction safe to use at all.
- **Four de-risking amendments** from a feasibility review: parser-first/LLM-residual, Dask deferred
  in favor of warehouse-native plus orchestrator collectors, OTel scoped to interactions (OTel
  formally declined lineage as a signal), and OpenLineage end-to-end as the single wire format.
- **Rollout with gates** — Phase 0 (Q1) vertical slice with an identity-resolution spike gated at
  ≥95% precision / ≥90% recall (GO/PIVOT/STOP); Phase 1 (Q2) 8–10 applications plus a published
  coverage map; Phase 2 (Q3) org-wide ingest at 2,000+ services; Phase 3 (Q4) continuous trust
  (drift detection, stale-edge decay, confidence SLAs).
- **Three risks** — identity resolution (silent under-reporting), the coverage tail (invisible edges
  behind an implied-complete UI), and LLM accuracy/cost (published extraction tops out ~0.77 and
  models are systematically overconfident), each with a named mitigation.

## Related

* [Throughline Prototype: Lineage Collection Deep Dive](/references/prototype-deep-dive-lineage-collection.md) - the named companion document that grounds every claim here in primary sources.
* [Throughline Prototype: PRD 1 Lineage Collection](/references/prototype-prd-1-lineage-collection.md) - the PRD this brief summarizes and amends for a leadership audience.
* [Consolidation, URN Merge, and Confidence](/references/consolidation-and-confidence.md) - the implemented reconcile stage (merge engine plus confidence engine) specified here.
* [Static Code Analysis Engine](/references/sca-engine.md) - the implemented build-time plane following the parser-first, LLM-residual design.
* [Runtime Observation Plane](/references/runtime-observation-plane.md) - the implemented runtime evidence plane that confirms the predicted superset.

## Citations

1. [Architecture Flow - Tech Leaders.dc.html](/Users/sowmiyamohankumar/Documents/lineage-collector-v1/Data Lineage Impact Platform-10/Architecture Flow - Tech Leaders.dc.html)
