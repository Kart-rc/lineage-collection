---
type: Reference
title: "Throughline Prototype: PRD 1 Lineage Collection"
description: The prototype PRD specifying four-signal lineage collection reconciled into one canonical graph with a calibrated confidence band on every edge.
resource: /Users/sowmiyamohankumar/Documents/lineage-collector-v1/Data Lineage Impact Platform-10/PRD 1 - Lineage Collection.dc.html
tags: [prototype, throughline, lineage-collection, confidence, prd]
timestamp: 2026-08-14T12:10:00Z
---

# Throughline Prototype: PRD 1 Lineage Collection

First of the three Throughline PRDs (v0.9 draft, owner Data Platform). Core thesis: lineage is only
useful if people trust it — hand-curated lineage rots and single-source automation is incomplete —
so Throughline collects from four independent signals (static code analysis with LLM inference, a
CI/CD diff on every change, runtime collectors for Spark and Dask, and OpenTelemetry traces) and
reconciles them into one canonical graph where every edge carries a confidence score derived from
signal agreement.

Key specifications:

- Goals: one canonical column-level graph spanning services, streams, S3, and file transfers;
  calibrated confidence with full provenance on every node/edge; automatic currency via CI/CD;
  graceful degradation when a signal is missing. Non-goals for v1: auto-remediation, a data
  catalog/glossary product, sub-second lineage, and every language at launch.
- Scale claim: 2,000+ services and datasets, batch and streaming; value proven on one vertical
  slice (one business application plus immediate cross-app dependencies) before widening.
- Data model: a typed property graph whose containment hierarchy provides the UI altitudes. Every
  edge carries {source, target, level (system|dataset|column), transform, channel
  (batch|stream|rest|graphql|grpc|async), confidence, signals[], lastObservedAt} plus a path
  qualifier; column edges roll up to dataset and app edges.
- Path-qualified edges: static + LLM emits the superset of all potential code paths (including
  dead and error branches); runtime confirms the executed subset with hot/warm/cold frequency.
  Branch coverage — the share of paths runtime-confirmed — becomes a measurable risk surface.
- Confidence model: signal agreement combined with runtime recency; baseline-only edges are capped
  below the runtime-confirmed range. Bands: Verified (>= 85, runtime-confirmed plus baseline, safe
  for automated impact gating), Probable (65-84, strong baseline with partial/aging runtime,
  caution-flagged), Inferred (< 65, LLM-derived and never observed, drawn dashed and excluded from
  automated gates).
- Baseline (what code should do) versus runtime (what actually happened) is the load-bearing
  distinction; confidence is highest where the two agree recently.

## Related

* [Throughline Prototype: PRD 2 Impact Analysis](/references/prototype-prd-2-impact-analysis.md) - consumes these confidence bands and path coverage as its severity and gating inputs
* [Throughline Prototype: PRD 3 UI Representation](/references/prototype-prd-3-ui-representation.md) - renders this PRD's bands and channels as its visual encoding system
* [Throughline Prototype: Deep Dive Lineage Collection](/references/prototype-deep-dive-lineage-collection.md) - the engineering deep dive expanding this PRD's collector design
* [SCA Engine](/references/sca-engine.md) - the implemented static-analysis collector realizing this PRD's baseline signal
* [Consolidation and Confidence](/references/consolidation-and-confidence.md) - the implemented merge-and-reconciliation engine this PRD specifies

## Citations

1. [PRD 1 - Lineage Collection.dc.html](/Users/sowmiyamohankumar/Documents/lineage-collector-v1/Data Lineage Impact Platform-10/PRD 1 - Lineage Collection.dc.html)
